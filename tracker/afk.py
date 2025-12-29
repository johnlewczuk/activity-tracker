"""
AFK (Away From Keyboard) detection module.

This module detects when the user is away from keyboard by monitoring
input events using pynput, modeled after ActivityWatch's aw-watcher-afk.

When the user becomes AFK, callbacks are fired to trigger session
management and summarization.
"""

import logging
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _install_package(package: str) -> bool:
    """Attempt to install a package using pip.

    Args:
        package: Name of the package to install.

    Returns:
        True if installation succeeded, False otherwise.
    """
    try:
        logger.info(f"Auto-installing missing dependency: {package}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install {package}: {e}")
        return False


# Track whether pynput is available
PYNPUT_AVAILABLE = True
try:
    from pynput import keyboard, mouse
except ImportError:
    logger.warning("pynput not found, attempting auto-install...")
    if _install_package("pynput"):
        try:
            from pynput import keyboard, mouse
            logger.info("pynput installed successfully")
        except ImportError:
            PYNPUT_AVAILABLE = False
            logger.error("pynput import failed after installation - AFK detection disabled")
    else:
        PYNPUT_AVAILABLE = False
        logger.error("Failed to install pynput - AFK detection disabled")


class AFKWatcher:
    """
    Monitors keyboard and mouse activity to detect AFK state.

    Uses pynput to listen for input events and fires callbacks when
    the user transitions between active and AFK states.

    Attributes:
        timeout: Seconds of inactivity before considered AFK.
        poll_time: How often to check AFK status.
        is_afk: Current AFK state.
    """

    def __init__(
        self,
        timeout: int = 180,
        poll_time: float = 5.0,
        on_afk: Optional[Callable[[], None]] = None,
        on_active: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize the AFK watcher.

        Args:
            timeout: Seconds of inactivity before considered AFK (default 180).
            poll_time: How often to check status in seconds (default 5.0).
            on_afk: Callback fired when user becomes AFK.
            on_active: Callback fired when user becomes active again.
        """
        self.timeout = timeout
        self.poll_time = poll_time
        self.on_afk = on_afk
        self.on_active = on_active

        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._is_afk = False
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._keyboard_listener = None
        self._mouse_listener = None

    @property
    def is_afk(self) -> bool:
        """Returns current AFK state (thread-safe)."""
        with self._lock:
            return self._is_afk

    def seconds_since_last_input(self) -> float:
        """Returns seconds since last keyboard/mouse event."""
        with self._lock:
            return time.time() - self._last_activity

    def _on_input_event(self, *args, **kwargs):
        """Called on any keyboard or mouse event.

        Also immediately fires on_active callback if transitioning from AFK.
        This ensures the daemon starts a new session before the window watcher
        can create focus events, avoiding NULL session_id events.
        """
        fire_active = False
        with self._lock:
            self._last_activity = time.time()
            # Immediately transition to active if we were AFK
            if self._is_afk:
                self._is_afk = False
                fire_active = True
                logger.info("User became active (immediate detection)")

        # Fire callback outside lock to avoid deadlocks
        if fire_active and self.on_active:
            try:
                self.on_active()
            except Exception as e:
                logger.error(f"on_active callback error: {e}")

    def _poll_loop(self):
        """
        Background thread that checks idle time vs timeout.

        Fires callbacks on state transitions only.
        """
        logger.info(f"AFK poll loop started (timeout={self.timeout}s, poll={self.poll_time}s)")

        while self._running:
            try:
                idle_seconds = self.seconds_since_last_input()

                with self._lock:
                    was_afk = self._is_afk

                    if idle_seconds >= self.timeout and not was_afk:
                        # Transition to AFK
                        self._is_afk = True
                        logger.info(f"User went AFK after {idle_seconds:.0f}s of inactivity")
                        if self.on_afk:
                            try:
                                self.on_afk()
                            except Exception as e:
                                logger.error(f"on_afk callback error: {e}")

                    elif idle_seconds < self.timeout and was_afk:
                        # Transition to active
                        self._is_afk = False
                        logger.info("User became active")
                        if self.on_active:
                            try:
                                self.on_active()
                            except Exception as e:
                                logger.error(f"on_active callback error: {e}")

            except Exception as e:
                logger.error(f"Poll loop error: {e}")

            time.sleep(self.poll_time)

        logger.info("AFK poll loop stopped")

    def start(self):
        """
        Start keyboard and mouse listeners and polling thread.

        If pynput is not available, logs a warning and the watcher
        will always report the user as active.
        """
        if not PYNPUT_AVAILABLE:
            logger.warning("pynput not available - AFK detection disabled, always reporting active")
            return

        if self._running:
            logger.warning("AFKWatcher already running")
            return

        self._running = True
        self._last_activity = time.time()
        self._is_afk = False

        # Start keyboard listener
        try:
            self._keyboard_listener = keyboard.Listener(
                on_press=self._on_input_event,
                on_release=self._on_input_event,
            )
            self._keyboard_listener.start()
            logger.debug("Keyboard listener started")
        except Exception as e:
            logger.error(f"Failed to start keyboard listener: {e}")

        # Start mouse listener
        try:
            self._mouse_listener = mouse.Listener(
                on_move=self._on_input_event,
                on_click=self._on_input_event,
                on_scroll=self._on_input_event,
            )
            self._mouse_listener.start()
            logger.debug("Mouse listener started")
        except Exception as e:
            logger.error(f"Failed to start mouse listener: {e}")

        # Start poll thread
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        logger.info("AFKWatcher started")

    def stop(self):
        """Clean shutdown of listeners and thread."""
        if not self._running:
            return

        self._running = False

        # Stop keyboard listener
        if self._keyboard_listener:
            try:
                self._keyboard_listener.stop()
            except Exception as e:
                logger.debug(f"Error stopping keyboard listener: {e}")
            self._keyboard_listener = None

        # Stop mouse listener
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception as e:
                logger.debug(f"Error stopping mouse listener: {e}")
            self._mouse_listener = None

        # Wait for poll thread
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

        logger.info("AFKWatcher stopped")

    def reset_activity(self):
        """
        Manually reset the last activity timestamp.

        Useful for simulating activity or testing.
        """
        with self._lock:
            self._last_activity = time.time()
