"""Activity Tracking Daemon Module.

This module implements the main daemon process that coordinates screenshot capture,
window information extraction, and duplicate detection. It runs continuously in
the background, capturing screenshots at 30-second intervals and managing storage.

The daemon provides:
- Automated screenshot capture with configurable intervals
- Perceptual duplicate detection to avoid storing redundant images
- Window context extraction using xdotool (X11)
- Signal handling for graceful shutdown
- Comprehensive logging and error handling
- Integration with systemd for service management

Key Features:
- Runs as background service via systemd
- Skip duplicate screenshots based on perceptual hash similarity  
- Extract active window title and application name
- Graceful signal handling (SIGTERM, SIGINT)
- Automatic restart capability via systemd
- Structured logging to files and stderr

Dependencies:
- tracker.capture: Screenshot capture and hashing
- tracker.storage: Database storage management
- xdotool: X11 window information extraction
- mss, PIL: Screen capture and image processing

Example:
    # Run daemon programmatically
    >>> from tracker.daemon import ActivityDaemon
    >>> daemon = ActivityDaemon()
    >>> daemon.run()  # Runs until interrupted
    
    # Or via command line
    $ python -m tracker.daemon
"""

import sys
import time
import signal
import hashlib
import subprocess
import argparse
import threading
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import mss
from PIL import Image

from .capture import ScreenCapture
from .storage import ActivityStorage
from .app_inference import get_app_name_with_inference
from .afk import AFKWatcher
from .sessions import SessionManager


class ActivityDaemon:
    """Main daemon process for automated screenshot capture and monitoring.
    
    Coordinates screenshot capture, window information extraction, and storage
    operations. Runs continuously in the background with configurable intervals
    and provides duplicate detection to optimize storage usage.
    
    The daemon captures screenshots every 30 seconds, extracts window context
    using xdotool, computes perceptual hashes for duplicate detection, and
    stores metadata in SQLite database.
    
    Attributes:
        running (bool): Controls daemon execution loop
        capture (ScreenCapture): Screenshot capture instance
        storage (ActivityStorage): Database storage instance  
        last_dhash (str): Previous screenshot hash for duplicate detection
        
    Example:
        >>> daemon = ActivityDaemon()
        >>> daemon.run()  # Blocks until interrupted
        
        # Or with custom signal handling
        >>> daemon = ActivityDaemon()
        >>> try:
        ...     daemon.run()
        ... except KeyboardInterrupt:
        ...     daemon.log("Shutdown requested")
    """
    
    def __init__(self, enable_web=False, web_port=55555, auto_summarize=True,
                 afk_timeout=180, afk_poll_time=5.0):
        """Initialize the activity daemon with default configuration.

        Sets up screenshot capture, database storage, signal handlers, and
        initializes tracking state for duplicate detection.

        Args:
            enable_web (bool): Whether to start the web server
            web_port (int): Port for the web server (default: 55555)
            auto_summarize (bool): Whether to auto-summarize sessions on AFK (default True)
            afk_timeout (int): Seconds of inactivity before considered AFK (default 180)
            afk_poll_time (float): How often to check AFK status (default 5.0)

        Signal handlers are registered for:
        - SIGTERM: Graceful shutdown (systemd stop)
        - SIGINT: Interrupt signal (Ctrl+C)
        """
        self.running = True
        self.capture = ScreenCapture()
        self.storage = ActivityStorage()
        self.last_dhash = None
        self.enable_web = enable_web
        self.web_port = web_port
        self.flask_app = None
        self.web_thread = None
        self.auto_summarize = auto_summarize
        self.last_summarized_hour = None
        self.summarize_thread = None

        # Session management
        self.session_manager = SessionManager(self.storage)
        self.current_session_id = None

        # AFK detection
        self.afk_watcher = AFKWatcher(
            timeout=afk_timeout,
            poll_time=afk_poll_time,
            on_afk=self._handle_afk,
            on_active=self._handle_active,
        )

        if enable_web:
            self._setup_flask_app()

        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals for graceful shutdown.
        
        Called when SIGTERM or SIGINT is received. Sets running flag to False
        to exit the main capture loop cleanly.
        
        Args:
            signum (int): Signal number received
            frame: Current stack frame (unused)
        """
        self.log(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
    
    def _setup_flask_app(self):
        """Set up the Flask web application for the activity viewer.

        Imports the existing Flask app from web/app.py instead of duplicating routes.
        This ensures all routes (/timeline, /analytics, etc.) are available.
        """
        import sys
        from pathlib import Path

        # Add project root to sys.path so we can import web.app
        project_root = Path(__file__).parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        # Import the existing Flask app from web/app.py
        from web.app import app
        self.flask_app = app
    
    def _start_web_server(self):
        """Start the Flask web server in a separate thread."""
        if self.flask_app:
            self.log(f"Starting web server on http://0.0.0.0`:{self.web_port}")
            self.flask_app.run(host='0.0.0.0', port=self.web_port, debug=False, use_reloader=False)
    
    def _stop_web_server(self):
        """Stop the web server thread."""
        if self.web_thread and self.web_thread.is_alive():
            self.log("Stopping web server...")
            # Flask doesn't have a clean shutdown method, thread will end when daemon stops

    def _handle_active(self):
        """Called when user becomes active after AFK.

        Starts a new session to track the upcoming activity period.
        """
        self.current_session_id = self.session_manager.start_session()
        self.log(f"Started session {self.current_session_id}")

    def _handle_afk(self):
        """Called when user goes AFK.

        Ends the current session and triggers summarization if enabled.
        """
        if self.current_session_id:
            session = self.session_manager.end_session(self.current_session_id)
            if session:  # Was long enough
                self.log(f"Ended session {self.current_session_id}, duration: {session.get('duration_seconds', 0) // 60}m")
                if self.auto_summarize:
                    # Run in background thread to not block
                    threading.Thread(
                        target=self._summarize_session,
                        args=(session,),
                        daemon=True
                    ).start()
            self.current_session_id = None

    def _summarize_session(self, session: dict):
        """Background summarization of completed session.

        Args:
            session: Session dict with id, start_time, etc.
        """
        try:
            from .vision import HybridSummarizer
            from . import config

            summarizer = HybridSummarizer(
                model=config.SUMMARIZER_MODEL,
                ollama_host=config.OLLAMA_HOST,
            )

            if not summarizer.is_available():
                self.log("Summarizer not available (check Ollama and Tesseract)")
                return

            session_id = session["id"]

            # Get screenshots for session
            screenshots = self.storage.get_session_screenshots(session_id)
            if len(screenshots) < 2:
                self.log(f"Session {session_id}: Not enough screenshots for summary")
                return

            # Process OCR for unique window titles
            unique_titles = self.storage.get_unique_window_titles_for_session(session_id)
            ocr_texts = []

            for title in unique_titles:
                # Check cache first
                cached = self.storage.get_cached_ocr(session_id, title)
                if cached is not None:
                    ocr_texts.append({"window_title": title, "ocr_text": cached})
                    continue

                # Find a screenshot with this title
                for s in screenshots:
                    if s.get("window_title") == title:
                        try:
                            # Use cropped version for better OCR accuracy
                            cropped_path = summarizer.get_cropped_path(s)
                            ocr_text = summarizer.extract_ocr(cropped_path)
                            self.storage.cache_ocr(session_id, title, ocr_text, s["id"])
                            ocr_texts.append({"window_title": title, "ocr_text": ocr_text})
                        except Exception as e:
                            self.log(f"OCR failed for '{title}': {e}")
                        break

            # Get previous session summary for context
            recent_summaries = self.storage.get_recent_summaries(1)
            previous_summary = recent_summaries[0] if recent_summaries else None

            # Generate summary
            self.log(f"Generating summary for session {session_id}...")
            summary, inference_ms, prompt_text, screenshot_ids_used = summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=previous_summary,
            )

            # Save to database
            self.storage.save_session_summary(
                session_id=session_id,
                summary=summary,
                model=summarizer.model,
                inference_ms=inference_ms,
                prompt_text=prompt_text,
                screenshot_ids_used=screenshot_ids_used,
            )

            self.log(f"Session {session_id}: {summary}")

        except Exception as e:
            self.log(f"Summarization error for session {session.get('id')}: {e}")

    def _should_trigger_summarization(self) -> tuple[bool, int]:
        """Check if we should trigger hourly summarization.

        Summarization is triggered at :05 past each hour for the previous hour.

        Returns:
            tuple[bool, int]: (should_summarize, hour_to_summarize)
        """
        now = datetime.now()

        # Only trigger at :05 past the hour (with some tolerance)
        if now.minute < 5 or now.minute > 10:
            return False, -1

        # Calculate the previous hour to summarize
        previous_hour = now.hour - 1
        if previous_hour < 0:
            previous_hour = 23

        # Don't re-summarize the same hour
        if self.last_summarized_hour == (now.date(), previous_hour):
            return False, -1

        return True, previous_hour

    def _run_summarization(self, date_str: str, hour: int):
        """Run summarization for a specific hour in background thread."""
        try:
            from .vision import HybridSummarizer
            from . import config

            summarizer = HybridSummarizer(
                model=config.SUMMARIZER_MODEL,
                ollama_host=config.OLLAMA_HOST,
            )

            if not summarizer.is_available():
                self.log("Summarizer not available (check Ollama and Tesseract)")
                return

            # Get screenshots for the hour
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            start_ts = int(date_obj.timestamp()) + hour * 3600
            end_ts = start_ts + 3600

            screenshots = self.storage.get_screenshots(start_ts, end_ts - 1)

            if len(screenshots) < 2:
                self.log(f"Skipping {hour}:00 - only {len(screenshots)} screenshot(s)")
                return

            # Sample if too many
            if len(screenshots) > config.SUMMARIZER_SAMPLES_PER_HOUR:
                step = len(screenshots) / config.SUMMARIZER_SAMPLES_PER_HOUR
                indices = [int(i * step) for i in range(config.SUMMARIZER_SAMPLES_PER_HOUR)]
                screenshots = [screenshots[i] for i in indices]

            # Get paths and IDs
            data_dir = Path.home() / "activity-tracker-data" / "screenshots"
            paths = [str(data_dir / s["filepath"]) for s in screenshots]
            screenshot_ids = [s["id"] for s in screenshots]

            self.log(f"Generating summary for {hour}:00 ({len(screenshots)} screenshots)...")

            start_time = time.time()
            summary = summarizer.summarize_hour(paths)
            inference_ms = int((time.time() - start_time) * 1000)

            self.storage.save_summary(
                date=date_str,
                hour=hour,
                summary=summary,
                screenshot_ids=screenshot_ids,
                model=summarizer.model,
                inference_ms=inference_ms,
            )

            self.log(f"Summary for {hour}:00 generated in {inference_ms}ms")

        except Exception as e:
            self.log(f"Summarization error for {hour}:00: {e}")

    def _trigger_summarization(self, hour: int):
        """Trigger summarization for an hour in a background thread."""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        # If it's a new day (hour 23 from yesterday)
        if hour == 23 and now.hour == 0:
            from datetime import timedelta
            yesterday = now - timedelta(days=1)
            date_str = yesterday.strftime("%Y-%m-%d")

        # Mark as summarized before starting to avoid re-triggering
        self.last_summarized_hour = (now.date(), hour)

        # Run in background thread to not block capture
        self.summarize_thread = threading.Thread(
            target=self._run_summarization,
            args=(date_str, hour),
            daemon=True
        )
        self.summarize_thread.start()
    
    def log(self, message: str):
        """Log a timestamped message to stderr.
        
        Provides structured logging with ISO timestamp format. Messages are
        written to stderr for systemd journal integration and immediate flushing.
        
        Args:
            message (str): Log message to write
            
        Example:
            >>> daemon = ActivityDaemon()
            >>> daemon.log("Screenshot captured successfully")
            [2023-11-27 10:30:15] Screenshot captured successfully
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)
    
    def _get_active_window_info(self) -> tuple[Optional[str], Optional[str]]:
        """Extract information about the currently active window.

        Uses xdotool to query X11 for the focused window's title and class name.
        This provides context about what application the user was using when
        the screenshot was captured.

        Returns:
            tuple[Optional[str], Optional[str]]: A tuple containing:
                - window_title: Title of the active window (or None if unavailable)
                - app_name: Application class name (or None if unavailable)

        Note:
            Requires xdotool to be installed and X11 display server.
            Wayland support is planned for future versions.

        Example:
            >>> daemon = ActivityDaemon()
            >>> title, app = daemon._get_active_window_info()
            >>> print(f"Active: {app} - {title}")
            Active: firefox - Mozilla Firefox
        """
        # TODO: Wayland compatibility - xdotool is X11-only, need alternative for Wayland
        # Should detect display server and use appropriate tools (e.g., swaymsg for Sway)
        try:
            # Get active window ID first
            result = subprocess.run(
                ["xdotool", "getwindowfocus"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return None, None

            window_id = result.stdout.strip()

            # Get active window title
            result = subprocess.run(
                ["xdotool", "getwindowfocus", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=5
            )
            window_title = result.stdout.strip() if result.returncode == 0 else None

            # Get active window class (app name) using xprop
            # WM_CLASS returns: "instance", "Class" - we want the Class (second value)
            result = subprocess.run(
                ["xprop", "-id", window_id, "WM_CLASS"],
                capture_output=True,
                text=True,
                timeout=5
            )
            app_name = None
            if result.returncode == 0:
                # Parse output like: WM_CLASS(STRING) = "tilix", "Tilix"
                output = result.stdout.strip()
                if '=' in output:
                    class_part = output.split('=', 1)[1].strip()
                    # Extract the second quoted string (the Class name)
                    matches = re.findall(r'"([^"]*)"', class_part)
                    if len(matches) >= 2:
                        app_name = matches[1]  # Use the Class (second value)
                    elif len(matches) == 1:
                        app_name = matches[0]  # Fallback to instance if only one value

            return window_title, app_name

        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
            # TODO: Permission errors - handle case where xdotool fails due to X11 permissions
            # Should check for X11 access permissions and provide helpful error messages
            self.log(f"Failed to get window info: {e}")
            return None, None

    def _get_focused_window_geometry(self) -> Optional[dict]:
        """Get bounds of focused window using xdotool.

        Extracts the geometry (position and size) of the currently focused window.
        This is used for cropping screenshots to the active window for improved
        OCR and LLM accuracy.

        Returns:
            Optional[dict]: Dictionary with keys:
                - x (int): X position in pixels
                - y (int): Y position in pixels
                - width (int): Window width in pixels
                - height (int): Window height in pixels
                Or None if window geometry cannot be determined.

        Note:
            Requires xdotool to be installed and X11 display server.
            Falls back to full screenshot if geometry cannot be determined.

        Example:
            >>> daemon = ActivityDaemon()
            >>> geo = daemon._get_focused_window_geometry()
            >>> if geo:
            ...     print(f"Window at ({geo['x']}, {geo['y']}) size {geo['width']}x{geo['height']}")
        """
        try:
            # Get active window ID
            result = subprocess.run(
                ['xdotool', 'getactivewindow'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return None

            window_id = result.stdout.strip()

            # Get geometry using --shell option for easy parsing
            result = subprocess.run(
                ['xdotool', 'getwindowgeometry', '--shell', window_id],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                return None

            # Parse output: WINDOW=123\nX=100\nY=200\nWIDTH=1920\nHEIGHT=1080
            geo = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    geo[key] = int(value) if value.isdigit() else value

            # Validate we have all required fields
            if not all(k in geo for k in ['X', 'Y', 'WIDTH', 'HEIGHT']):
                return None

            return {
                'x': geo['X'],
                'y': geo['Y'],
                'width': geo['WIDTH'],
                'height': geo['HEIGHT']
            }

        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError, ValueError) as e:
            self.log(f"Failed to get window geometry: {e}")
            return None
    
    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """Calculate Hamming distance between two perceptual hashes.
        
        Computes the number of differing bits between two hexadecimal hash strings
        by XORing their binary representations. Used for duplicate detection.
        
        Args:
            hash1 (str): First hash as hexadecimal string
            hash2 (str): Second hash as hexadecimal string
            
        Returns:
            int: Hamming distance (number of different bits).
                Returns infinity if hash lengths differ.
                
        Example:
            >>> daemon = ActivityDaemon()
            >>> distance = daemon._hamming_distance("abc123", "abc124")
            >>> print(f"Hashes differ by {distance} bits")
        """
        if len(hash1) != len(hash2):
            return float('inf')
        
        # Convert hex to int and XOR
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        xor_result = int1 ^ int2
        
        # Count set bits (Hamming distance)
        return bin(xor_result).count('1')
    
    def _should_skip_screenshot(self, current_dhash: str) -> bool:
        """Determine if current screenshot should be skipped due to similarity.
        
        Compares the current screenshot's perceptual hash with the previous
        one to detect near-duplicates. Skips storage if images are too similar
        to avoid redundant data.
        
        Args:
            current_dhash (str): Perceptual hash of current screenshot
            
        Returns:
            bool: True if screenshot should be skipped (too similar to previous),
                False if it should be stored
                
        Note:
            Uses a threshold of 3 bits difference for duplicate detection.
            This catches minor changes like cursor movement while preserving
            significant content changes.
        """
        if not self.last_dhash or not current_dhash:
            return False
        
        distance = self._hamming_distance(current_dhash, self.last_dhash)
        return distance < 3
    
    def run(self):
        """Start the main daemon loop for continuous screenshot monitoring.

        Runs indefinitely until interrupted by signal or error. Captures screenshots
        every 30 seconds, performs duplicate detection, extracts window information,
        and stores metadata to database.

        The main loop:
        1. Capture screenshot and compute perceptual hash
        2. Check for similarity with previous screenshot
        3. Extract active window information via xdotool
        4. Store metadata to SQLite database
        5. Link screenshot to current session
        6. Sleep for 30 seconds
        7. Repeat until shutdown signal received

        Raises:
            KeyboardInterrupt: If interrupted by Ctrl+C (gracefully handled)
            Exception: For unexpected errors (logged and daemon continues)

        Example:
            >>> daemon = ActivityDaemon()
            >>> try:
            ...     daemon.run()
            ... except KeyboardInterrupt:
            ...     print("Daemon stopped")

        Note:
            This method blocks until the daemon is stopped via signal.
            For systemd integration, stdout/stderr are redirected to journal.
        """
        self.log("Activity daemon starting...")

        # Start AFK watcher
        self.afk_watcher.start()

        # Check for active session from database (e.g., after restart)
        active_session = self.storage.get_active_session()
        if active_session:
            session_id = active_session["id"]
            last_ts = self.storage.get_last_screenshot_timestamp_for_session(session_id)

            if last_ts:
                # Check if last activity was within AFK timeout
                seconds_since_last = int(time.time()) - last_ts
                if seconds_since_last < self.afk_watcher.timeout:
                    # Resume the session - last activity was recent enough
                    resumed_session = self.session_manager.resume_active_session()
                    self.current_session_id = resumed_session
                    self.log(f"Resumed active session {resumed_session} (last activity {seconds_since_last}s ago)")
                else:
                    # End the old session (was AFK) and start fresh
                    self.log(f"Previous session {session_id} stale ({seconds_since_last}s since last activity)")
                    session = self.session_manager.end_session(session_id)
                    if session and self.auto_summarize:
                        # Summarize the old session in background
                        threading.Thread(
                            target=self._summarize_session,
                            args=(session,),
                            daemon=True
                        ).start()
                    # Start new session
                    self.current_session_id = self.session_manager.start_session()
                    self.log(f"Started new session {self.current_session_id}")
            else:
                # No screenshots in session yet, just resume it
                resumed_session = self.session_manager.resume_active_session()
                self.current_session_id = resumed_session
                self.log(f"Resumed empty session {resumed_session}")
        else:
            # No active session, start a new one
            self.current_session_id = self.session_manager.start_session()
            self.log(f"Started initial session {self.current_session_id}")

        # Start web server in separate thread if enabled
        if self.enable_web:
            self.web_thread = threading.Thread(target=self._start_web_server, daemon=True)
            self.web_thread.start()

        while self.running:
            try:
                # Capture screenshot
                filepath, current_dhash = self.capture.capture_screen()
                if not filepath:
                    self.log("Failed to capture screenshot")
                    time.sleep(30)
                    continue
                
                # Check if we should skip this screenshot
                if self._should_skip_screenshot(current_dhash):
                    self.log(f"Screenshot too similar to previous (distance < 3), skipping...")
                    # TODO: Permission errors - handle case where file deletion fails due to permissions
                    try:
                        Path(filepath).unlink(missing_ok=True)
                    except PermissionError as e:
                        self.log(f"Warning: Could not delete duplicate screenshot {filepath}: {e}")
                    time.sleep(30)
                    continue
                
                # Get window information
                window_title, app_name = self._get_active_window_info()

                # Get window geometry for cropping
                window_geometry = self._get_focused_window_geometry()

                # Infer app_name from window_title if app_name is NULL
                app_name = get_app_name_with_inference(app_name, window_title)

                # Save to database (with window geometry if available)
                screenshot_id = self.storage.save_screenshot(
                    filepath=filepath,
                    dhash=current_dhash,
                    window_title=window_title,
                    app_name=app_name,
                    window_geometry=window_geometry
                )

                # Link to current session if active
                if self.current_session_id:
                    self.session_manager.add_screenshot_to_session(
                        self.current_session_id, screenshot_id
                    )
                    # Track window title for OCR optimization
                    if window_title:
                        is_new = self.session_manager.track_window_title(
                            self.current_session_id, window_title
                        )
                        if is_new:
                            self.log(f"New window in session: {window_title[:50]}")

                self.last_dhash = current_dhash
                self.log(f"Saved screenshot {screenshot_id}: {Path(filepath).name}")
                
            except Exception as e:
                # TODO: Edge case - daemon should be more resilient to errors and not crash
                # Should implement exponential backoff and distinguish between recoverable/fatal errors
                self.log(f"Error in capture loop: {e}")
            
            # Check if we should trigger auto-summarization
            if self.auto_summarize:
                should_summarize, hour = self._should_trigger_summarization()
                if should_summarize:
                    self._trigger_summarization(hour)

            # Sleep for 30 seconds
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)

        # Cleanup on shutdown
        self.log("Shutting down...")

        # Stop AFK watcher
        self.afk_watcher.stop()

        # End any active session
        if self.current_session_id:
            session = self.session_manager.end_session(self.current_session_id)
            if session:
                self.log(f"Ended session {self.current_session_id} on shutdown")
            self.current_session_id = None

        self.log("Activity daemon stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Activity tracking daemon")
    parser.add_argument("--web", action="store_true", help="Enable web server")
    parser.add_argument("--web-port", type=int, default=55555, help="Web server port (default: 55555)")
    parser.add_argument("--auto-summarize", action="store_true",
                        help="Enable auto-summarization of sessions on AFK")
    parser.add_argument("--afk-timeout", type=int, default=180,
                        help="Seconds of inactivity before considered AFK (default: 180)")
    parser.add_argument("--afk-poll", type=float, default=5.0,
                        help="How often to check AFK status in seconds (default: 5.0)")

    args = parser.parse_args()

    daemon = ActivityDaemon(
        enable_web=args.web,
        web_port=args.web_port,
        auto_summarize=args.auto_summarize,
        afk_timeout=args.afk_timeout,
        afk_poll_time=args.afk_poll,
    )
    daemon.run()
