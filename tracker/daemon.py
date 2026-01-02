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
import subprocess
import argparse
import threading
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configure logging for submodules (afk, vision, etc.)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr
)

from .capture import ScreenCapture
from .storage import ActivityStorage
from .app_inference import get_app_name_with_inference
from .afk import AFKWatcher
from .sessions import SessionManager
from .monitors import get_monitors, get_monitor_for_window, get_primary_monitor
from .config import get_config_manager
from .summarizer_worker import SummarizerWorker
from .window_watcher import WindowWatcher, WindowFocusEvent
from .terminal_introspect import is_terminal_app, get_terminal_context


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
    
    def __init__(self, enable_web=False, web_port=55555,
                 afk_timeout=180, afk_poll_time=5.0):
        """Initialize the activity daemon with default configuration.

        Sets up screenshot capture, database storage, signal handlers, and
        initializes tracking state for duplicate detection.

        Args:
            enable_web (bool): Whether to start the web server
            web_port (int): Port for the web server (default: 55555)
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

        # Configuration manager
        self.config = get_config_manager()

        # Session management
        self.session_manager = SessionManager(self.storage)
        self.current_session_id = None

        # AFK detection (for session tracking, not summarization)
        self.afk_watcher = AFKWatcher(
            timeout=afk_timeout,
            poll_time=afk_poll_time,
            on_afk=self._handle_afk,
            on_active=self._handle_active,
        )

        # Threshold-based summarization worker
        self.summarizer_worker = SummarizerWorker(self.storage, self.config)
        if self.config.config.summarization.enabled:
            self.summarizer_worker.start()

        # Window focus tracking
        self.window_watcher = WindowWatcher(
            poll_interval=1.0,
            on_focus_change=self._handle_focus_change,
            min_duration_seconds=self.config.config.tracking.min_focus_duration,
            session_id_provider=lambda: self.current_session_id
        )
        self.last_capture_time = datetime.now()

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
        from web.app import app, set_summarizer_worker
        self.flask_app = app

        # Set the summarizer worker reference for API access
        set_summarizer_worker(self.summarizer_worker)
    
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

        Ends the current session and flushes the current focus event so that
        focus durations don't include AFK time. Summarization is handled by
        the threshold-based SummarizerWorker, not AFK events.
        """
        # Flush current focus event BEFORE ending session (so it has correct session_id)
        flushed_event = self.window_watcher.flush_current_event()
        if flushed_event:
            self._save_focus_event(flushed_event)

        if self.current_session_id:
            session = self.session_manager.end_session(self.current_session_id)
            if session:  # Was long enough
                self.log(f"Ended session {self.current_session_id}, duration: {session.get('duration_seconds', 0) // 60}m")
            self.current_session_id = None

    def _save_focus_event(self, event: WindowFocusEvent, next_app: str = None):
        """Save a focus event to storage with terminal introspection.

        Args:
            event: The completed focus event to save
            next_app: Name of the next app (for logging), or None if AFK
        """
        # Get terminal context if this was a terminal window
        terminal_context_json = None
        terminal_info = ""
        if is_terminal_app(event.app_name) and event.window_pid:
            context = get_terminal_context(event.window_pid)
            if context:
                terminal_context_json = context.to_json()
                terminal_info = f" [{context.format_short()}]"

        # Use session_id from the event (captured at focus start)
        # Backfill if None but we now have a session (handles AFK->active race condition)
        session_id = event.session_id
        if session_id is None and self.current_session_id is not None:
            session_id = self.current_session_id

        self.storage.save_focus_event(
            window_title=event.window_title,
            app_name=event.app_name,
            window_class=event.window_class,
            start_time=event.start_time,
            end_time=event.end_time,
            session_id=session_id,
            terminal_context=terminal_context_json
        )

        if next_app:
            self.log(f"Focus: {event.app_name}{terminal_info} ({event.duration_seconds:.1f}s) -> {next_app}")
        else:
            self.log(f"Focus: {event.app_name}{terminal_info} ({event.duration_seconds:.1f}s) -> AFK")

    def _handle_focus_change(self, old_window: WindowFocusEvent, new_window: WindowFocusEvent):
        """Called when window focus changes - save completed focus event.

        Args:
            old_window: The window that lost focus (with end_time set)
            new_window: The window that gained focus
        """
        self._save_focus_event(old_window, new_window.app_name)

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
                    # Handle negative numbers (isdigit() returns False for "-100")
                    try:
                        geo[key] = int(value)
                    except ValueError:
                        geo[key] = value

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
    
    def _should_capture(self) -> tuple[bool, str]:
        """Determine if we should capture now based on focus stability.

        Uses focus-aware capture logic:
        - Skip if no window is focused (unless max interval exceeded)
        - Skip transient windows (notifications, popups)
        - Wait for stable focus before capturing
        - Force capture after max interval to avoid missing activity

        Returns:
            tuple[bool, str]: (should_capture, reason)
                Reasons: 'stable_focus', 'max_interval_exceeded', 'no_window_fallback',
                        'no_window', 'transient_window', 'focus_unstable', 'interval_not_reached'
        """
        now = datetime.now()
        time_since_last = (now - self.last_capture_time).total_seconds()
        interval = self.config.config.capture.interval_seconds
        stability_threshold = self.config.config.capture.stability_threshold_seconds
        max_multiplier = self.config.config.capture.max_interval_multiplier

        current_window = self.window_watcher.get_current_window()

        # No window focused (screen locked, desktop, etc.)
        if not current_window:
            if time_since_last >= interval:
                return True, "no_window_fallback"
            return False, "no_window"

        focus_duration = current_window.duration_seconds

        # Skip transient windows (notifications, popups)
        if self.config.config.capture.skip_transient_windows:
            if self._is_transient_window(current_window):
                return False, "transient_window"

        # Force capture if we've waited too long (don't miss activity during rapid switching)
        max_wait = interval * max_multiplier
        if time_since_last >= max_wait:
            return True, "max_interval_exceeded"

        # Normal capture: interval passed AND focus is stable
        if time_since_last >= interval:
            if focus_duration >= stability_threshold:
                return True, "stable_focus"
            else:
                return False, "focus_unstable"

        return False, "interval_not_reached"

    def _is_transient_window(self, window: WindowFocusEvent) -> bool:
        """Check if window is transient (notification, popup, etc.).

        Args:
            window: The window to check

        Returns:
            bool: True if the window matches a transient pattern
        """
        window_class_lower = (window.window_class or '').lower()
        title_lower = window.window_title.lower()

        for pattern in self.config.config.tracking.transient_window_classes:
            pattern_lower = pattern.lower()
            if pattern_lower in window_class_lower or pattern_lower in title_lower:
                return True
        return False

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

        # Start window watcher for focus tracking
        self.window_watcher.start()

        # Check for active session from database (e.g., after restart)
        active_session = self.storage.get_active_session()
        if active_session:
            session_id = active_session["id"]
            last_ts = self.storage.get_last_screenshot_timestamp_for_session(session_id)

            if last_ts:
                # Check if last activity was within AFK timeout
                seconds_since_last = int(time.time()) - last_ts

                # Also check if daemon was down for significant time (> 30s)
                # If daemon was down, user may have gone AFK while we weren't watching
                session_start_ts = active_session.get("start_time")
                daemon_likely_down = seconds_since_last > 30  # No screenshot for 30s suggests daemon was down

                if seconds_since_last < self.afk_watcher.timeout and not daemon_likely_down:
                    # Resume the session - last activity was very recent (daemon just restarted quickly)
                    resumed_session = self.session_manager.resume_active_session()
                    self.current_session_id = resumed_session
                    self.log(f"Resumed active session {resumed_session} (last activity {seconds_since_last}s ago)")
                else:
                    # End the old session - was AFK or daemon was down too long
                    reason = "daemon downtime" if daemon_likely_down else "AFK timeout"
                    self.log(f"Previous session {session_id} stale ({seconds_since_last}s since last activity, {reason})")
                    self.session_manager.end_session(session_id)
                    # Start a fresh session
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

        # Cache monitors list (refreshed periodically in get_monitors())
        monitors = None

        while self.running:
            try:
                # Focus-aware capture: check if we should capture now
                should_capture, capture_reason = self._should_capture()

                if not should_capture:
                    # Not time to capture yet - short sleep for responsive checks
                    time.sleep(1)
                    continue

                # Get focus context before capture
                current_window = self.window_watcher.get_current_window()
                focus_duration = current_window.duration_seconds if current_window else None

                # Get window information (needed for monitor detection)
                window_title, app_name = self._get_active_window_info()
                window_geometry = self._get_focused_window_geometry()

                # Determine which monitor to capture
                monitors = get_monitors()  # Cached internally with 60s refresh
                active_monitor = None
                capture_region = None

                if window_geometry:
                    # Find monitor containing the focused window
                    active_monitor = get_monitor_for_window(window_geometry, monitors)

                if active_monitor:
                    # Capture only the active monitor
                    capture_region = {
                        'left': active_monitor.x,
                        'top': active_monitor.y,
                        'width': active_monitor.width,
                        'height': active_monitor.height
                    }

                    # Adjust window geometry to be relative to monitor
                    if window_geometry:
                        window_geometry['x'] -= active_monitor.x
                        window_geometry['y'] -= active_monitor.y
                else:
                    # No focused window or couldn't determine monitor - use primary
                    primary_monitor = get_primary_monitor(monitors)
                    if primary_monitor:
                        active_monitor = primary_monitor
                        capture_region = {
                            'left': primary_monitor.x,
                            'top': primary_monitor.y,
                            'width': primary_monitor.width,
                            'height': primary_monitor.height
                        }

                # Capture screenshot with monitor region
                filepath, current_dhash = self.capture.capture_screen(region=capture_region)
                if not filepath:
                    self.log("Failed to capture screenshot")
                    time.sleep(1)
                    continue

                # Check if we should skip this screenshot (duplicate detection)
                if self._should_skip_screenshot(current_dhash):
                    self.log(f"Screenshot too similar to previous (distance < 3), skipping...")
                    try:
                        Path(filepath).unlink(missing_ok=True)
                    except PermissionError as e:
                        self.log(f"Warning: Could not delete duplicate screenshot {filepath}: {e}")
                    # Still update capture time to avoid rapid retries
                    self.last_capture_time = datetime.now()
                    time.sleep(1)
                    continue

                # Infer app_name from window_title if app_name is NULL
                app_name = get_app_name_with_inference(app_name, window_title)

                # Save to database (with window geometry and monitor info)
                screenshot_id = self.storage.save_screenshot(
                    filepath=filepath,
                    dhash=current_dhash,
                    window_title=window_title,
                    app_name=app_name,
                    window_geometry=window_geometry,
                    monitor_name=active_monitor.name if active_monitor else None,
                    monitor_width=active_monitor.width if active_monitor else None,
                    monitor_height=active_monitor.height if active_monitor else None
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
                self.last_capture_time = datetime.now()

                # Log capture with focus context
                focus_info = f", focus={focus_duration:.1f}s" if focus_duration else ""
                self.log(f"Captured ({capture_reason}{focus_info}): {Path(filepath).name}")


            except Exception as e:
                # TODO: Edge case - daemon should be more resilient to errors and not crash
                # Should implement exponential backoff and distinguish between recoverable/fatal errors
                self.log(f"Error in capture loop: {e}")

            # Short sleep for responsive focus-aware capture
            time.sleep(1)

        # Cleanup on shutdown
        self.log("Shutting down...")

        # Stop AFK watcher
        self.afk_watcher.stop()

        # Stop window watcher
        self.window_watcher.stop()

        # Stop summarizer worker
        self.summarizer_worker.stop()

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
    parser.add_argument("--afk-timeout", type=int, default=180,
                        help="Seconds of inactivity before considered AFK (default: 180)")
    parser.add_argument("--afk-poll", type=float, default=5.0,
                        help="How often to check AFK status in seconds (default: 5.0)")

    args = parser.parse_args()

    daemon = ActivityDaemon(
        enable_web=args.web,
        web_port=args.web_port,
        afk_timeout=args.afk_timeout,
        afk_poll_time=args.afk_poll,
    )
    daemon.run()
