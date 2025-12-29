"""
Window focus tracking with duration measurement.

Uses xdotool/xprop to detect window focus changes and track how long
each window is focused. More accurate than screenshot-based tracking.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
import threading
import subprocess
import logging

logger = logging.getLogger(__name__)


@dataclass
class WindowFocusEvent:
    """Represents a completed window focus period"""
    window_title: str
    app_name: str
    window_class: str
    start_time: datetime
    end_time: Optional[datetime] = None
    window_pid: Optional[int] = None
    session_id: Optional[int] = None  # Captured at focus start, not save time

    @property
    def duration_seconds(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.now() - self.start_time).total_seconds()


class WindowWatcher:
    """
    Track window focus changes in real-time.

    Usage:
        def on_change(old, new):
            print(f"Switched from {old.app_name} to {new.app_name}")

        watcher = WindowWatcher(on_focus_change=on_change)
        watcher.start()
    """

    def __init__(
        self,
        poll_interval: float = 1.0,
        on_focus_change: Callable[[WindowFocusEvent, WindowFocusEvent], None] = None,
        min_duration_seconds: float = 1.0,
        session_id_provider: Callable[[], Optional[int]] = None
    ):
        """
        Args:
            poll_interval: How often to check for focus changes (seconds)
            on_focus_change: Callback(old_window, new_window) on focus change
            min_duration_seconds: Ignore focus events shorter than this
            session_id_provider: Callback that returns current session_id (captured at focus start)
        """
        self.poll_interval = poll_interval
        self.on_focus_change = on_focus_change
        self.min_duration = min_duration_seconds
        self.session_id_provider = session_id_provider

        self._running = False
        self._thread = None
        self._current_window: Optional[WindowFocusEvent] = None
        self._lock = threading.Lock()

    def start(self):
        """Start watching for window focus changes"""
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info("Window watcher started")

    def stop(self):
        """Stop watching and close out current window"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

        with self._lock:
            if self._current_window:
                self._current_window.end_time = datetime.now()
        logger.info("Window watcher stopped")

    def get_current_window(self) -> Optional[WindowFocusEvent]:
        """Get currently focused window (ongoing, no end_time yet)"""
        with self._lock:
            return self._current_window

    def flush_current_event(self) -> Optional[WindowFocusEvent]:
        """End the current focus event and return it (for AFK transitions).

        This closes out the current window focus event without requiring a new
        window to take focus. Used when user goes AFK - we want to save the
        focus event with accurate duration (not including AFK time).

        Returns:
            The completed focus event if it met min_duration, None otherwise.
            The internal current_window is reset to None.
        """
        now = datetime.now()
        completed_event = None

        with self._lock:
            if self._current_window:
                self._current_window.end_time = now

                # Only return if duration >= minimum
                if self._current_window.duration_seconds >= self.min_duration:
                    completed_event = self._current_window

                # Clear current window - will be re-created when user becomes active
                self._current_window = None

        return completed_event

    def _watch_loop(self):
        """Main polling loop"""
        last_window_id = None

        while self._running:
            try:
                window_info = self._get_active_window()

                if window_info and window_info.get('window_id') != last_window_id:
                    self._handle_focus_change(window_info)
                    last_window_id = window_info.get('window_id')

            except Exception as e:
                logger.debug(f"Window watch error: {e}")

            # Sleep in small increments for responsive shutdown
            for _ in range(int(self.poll_interval * 10)):
                if not self._running:
                    break
                threading.Event().wait(0.1)

    def _get_active_window(self) -> Optional[dict]:
        """Get active window info using xdotool and xprop"""
        try:
            # Get window ID
            window_id = subprocess.check_output(
                ['xdotool', 'getactivewindow'],
                stderr=subprocess.DEVNULL,
                timeout=1
            ).decode().strip()

            if not window_id:
                return None

            # Get window name
            window_name = subprocess.check_output(
                ['xdotool', 'getwindowname', window_id],
                stderr=subprocess.DEVNULL,
                timeout=1
            ).decode().strip()

            # Get window PID
            window_pid = None
            try:
                pid_output = subprocess.check_output(
                    ['xdotool', 'getwindowpid', window_id],
                    stderr=subprocess.DEVNULL,
                    timeout=1
                ).decode().strip()
                if pid_output:
                    window_pid = int(pid_output)
            except (subprocess.CalledProcessError, ValueError):
                pass  # Some windows don't have PIDs

            # Get window class (app name) via xprop
            xprop_output = subprocess.check_output(
                ['xprop', '-id', window_id, 'WM_CLASS'],
                stderr=subprocess.DEVNULL,
                timeout=1
            ).decode().strip()

            # Parse WM_CLASS = "instance", "class"
            app_name = "Unknown"
            window_class = ""
            if 'WM_CLASS' in xprop_output and '=' in xprop_output:
                parts = xprop_output.split('=')[1].strip()
                classes = [c.strip().strip('"') for c in parts.split(',')]
                if len(classes) >= 2:
                    window_class = classes[0]
                    app_name = classes[1]
                elif classes:
                    app_name = classes[0]

            return {
                'window_id': window_id,
                'window_title': window_name,
                'app_name': app_name,
                'window_class': window_class,
                'window_pid': window_pid
            }

        except subprocess.TimeoutExpired:
            return None
        except subprocess.CalledProcessError:
            return None
        except Exception as e:
            logger.debug(f"Failed to get active window: {e}")
            return None

    def _handle_focus_change(self, new_window: dict):
        """Handle a window focus change"""
        now = datetime.now()

        # Capture session_id at focus START time (not when event is saved)
        current_session_id = None
        if self.session_id_provider:
            try:
                current_session_id = self.session_id_provider()
            except Exception as e:
                logger.debug(f"Failed to get session_id: {e}")

        with self._lock:
            old_window = self._current_window

            # Close out previous window
            if old_window:
                old_window.end_time = now

                # Only report if duration >= minimum
                if old_window.duration_seconds < self.min_duration:
                    old_window = None

            # Create new window event with session_id captured NOW
            self._current_window = WindowFocusEvent(
                window_title=new_window['window_title'],
                app_name=new_window['app_name'],
                window_class=new_window['window_class'],
                start_time=now,
                window_pid=new_window.get('window_pid'),
                session_id=current_session_id
            )

        # Fire callback outside lock
        if self.on_focus_change and old_window:
            try:
                self.on_focus_change(old_window, self._current_window)
            except Exception as e:
                logger.error(f"Focus change callback error: {e}")


# Simple test
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    def on_change(old, new):
        print(f"{old.app_name} ({old.duration_seconds:.1f}s) -> {new.app_name}")

    watcher = WindowWatcher(on_focus_change=on_change)
    watcher.start()

    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        watcher.stop()
