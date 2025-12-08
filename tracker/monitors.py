"""Multi-monitor detection and management for X11.

This module provides utilities for detecting connected monitors using xrandr
and determining which monitor contains the focused window. This enables
capturing only the active monitor to reduce screenshot size and improve privacy.

Key Features:
- Parse xrandr output to get monitor configuration
- Detect monitor containing a specific window
- Handle edge cases (spanning windows, primary monitor fallback)
- Cache monitor list with periodic refresh for hotplug support

Dependencies:
- xrandr: X11 resize and rotate utility
- X11 display server

Example:
    >>> from tracker.monitors import get_monitors, get_monitor_for_window
    >>> monitors = get_monitors()
    >>> for m in monitors:
    ...     print(f"{m.name}: {m.width}x{m.height} at ({m.x}, {m.y})")
    >>>
    >>> window_geo = {'x': 100, 'y': 100, 'width': 800, 'height': 600}
    >>> active = get_monitor_for_window(window_geo, monitors)
    >>> print(f"Window is on {active.name}")
"""

import subprocess
import re
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Monitor:
    """Represents a physical monitor in the X11 multi-monitor setup.

    Attributes:
        name (str): Monitor identifier from xrandr (e.g., "DP-1", "HDMI-0")
        x (int): X offset from virtual screen origin (pixels)
        y (int): Y offset from virtual screen origin (pixels)
        width (int): Monitor width in pixels
        height (int): Monitor height in pixels
        is_primary (bool): Whether this is the primary monitor

    Example:
        >>> mon = Monitor("DP-1", 0, 0, 3840, 2160, True)
        >>> print(f"{mon.name} is {mon.width}x{mon.height}")
        DP-1 is 3840x2160
    """
    name: str
    x: int
    y: int
    width: int
    height: int
    is_primary: bool

    def contains_point(self, x: int, y: int) -> bool:
        """Check if a point is within this monitor's bounds.

        Args:
            x: X coordinate in virtual screen space
            y: Y coordinate in virtual screen space

        Returns:
            True if point is within monitor bounds, False otherwise
        """
        return (self.x <= x < self.x + self.width and
                self.y <= y < self.y + self.height)

    def overlap_area(self, x: int, y: int, width: int, height: int) -> int:
        """Calculate overlap area between this monitor and a rectangle.

        Args:
            x: Rectangle X coordinate
            y: Rectangle Y coordinate
            width: Rectangle width
            height: Rectangle height

        Returns:
            Overlap area in square pixels (0 if no overlap)
        """
        # Calculate intersection rectangle
        left = max(self.x, x)
        top = max(self.y, y)
        right = min(self.x + self.width, x + width)
        bottom = min(self.y + self.height, y + height)

        # Check if there's any overlap
        if left < right and top < bottom:
            return (right - left) * (bottom - top)
        return 0


# Monitor cache to avoid repeated xrandr calls
_monitor_cache = {
    'monitors': None,
    'timestamp': 0,
    'refresh_interval': 60  # Refresh every 60 seconds for hotplug support
}


def get_monitors(use_cache: bool = True) -> list[Monitor]:
    """Get list of connected monitors from xrandr.

    Parses xrandr output to extract monitor names, positions, and sizes.
    Results are cached for 60 seconds to avoid repeated xrandr calls.

    Args:
        use_cache: Whether to use cached monitor list (default True)

    Returns:
        List of Monitor objects, sorted with primary monitor first

    Raises:
        RuntimeError: If xrandr is not available or parsing fails

    Example:
        >>> monitors = get_monitors()
        >>> primary = next(m for m in monitors if m.is_primary)
        >>> print(f"Primary: {primary.name} at {primary.x},{primary.y}")
    """
    # Check cache
    if use_cache and _monitor_cache['monitors'] is not None:
        age = time.time() - _monitor_cache['timestamp']
        if age < _monitor_cache['refresh_interval']:
            return _monitor_cache['monitors']

    try:
        output = subprocess.check_output(
            ['xrandr', '--query'],
            stderr=subprocess.DEVNULL,
            timeout=5
        ).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning(f"Failed to run xrandr: {e}")
        # Return fallback single monitor (assume 1920x1080 primary)
        fallback = [Monitor("primary", 0, 0, 1920, 1080, True)]
        _monitor_cache['monitors'] = fallback
        _monitor_cache['timestamp'] = time.time()
        return fallback

    monitors = []

    # Pattern matches lines like:
    # "DP-1 connected primary 3840x2160+0+0"
    # "HDMI-0 connected 2560x1440+3840+0"
    # "eDP-1 connected 1920x1080+0+1080 (normal left inverted right x axis y axis) 344mm x 194mm"
    pattern = r'(\S+) connected (primary )?(\d+)x(\d+)\+(\d+)\+(\d+)'

    for match in re.finditer(pattern, output):
        try:
            monitors.append(Monitor(
                name=match.group(1),
                is_primary=bool(match.group(2)),
                width=int(match.group(3)),
                height=int(match.group(4)),
                x=int(match.group(5)),
                y=int(match.group(6))
            ))
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse monitor from xrandr: {match.group(0)}: {e}")
            continue

    if not monitors:
        logger.warning("No monitors found in xrandr output, using fallback")
        monitors = [Monitor("primary", 0, 0, 1920, 1080, True)]

    # Sort with primary monitor first
    monitors.sort(key=lambda m: (not m.is_primary, m.name))

    # Update cache
    _monitor_cache['monitors'] = monitors
    _monitor_cache['timestamp'] = time.time()

    logger.debug(f"Detected {len(monitors)} monitor(s): {[m.name for m in monitors]}")

    return monitors


def get_monitor_at_point(x: int, y: int, monitors: list[Monitor] = None) -> Optional[Monitor]:
    """Find which monitor contains the given point.

    Args:
        x: X coordinate in virtual screen space
        y: Y coordinate in virtual screen space
        monitors: Optional pre-fetched monitor list (calls get_monitors() if None)

    Returns:
        Monitor containing the point, or primary monitor if point is outside all monitors

    Example:
        >>> monitor = get_monitor_at_point(4000, 100)
        >>> print(f"Point is on {monitor.name}")
    """
    if monitors is None:
        monitors = get_monitors()

    # Find monitor containing the point
    for m in monitors:
        if m.contains_point(x, y):
            return m

    # Point is outside all monitors - return primary as fallback
    primary = next((m for m in monitors if m.is_primary), None)
    if primary:
        logger.debug(f"Point ({x}, {y}) outside monitors, using primary: {primary.name}")
        return primary

    # No primary found - return first monitor
    if monitors:
        logger.warning(f"Point ({x}, {y}) outside monitors and no primary found, using {monitors[0].name}")
        return monitors[0]

    return None


def get_monitor_for_window(window_geometry: dict, monitors: list[Monitor] = None) -> Optional[Monitor]:
    """Find monitor containing the window.

    For windows spanning multiple monitors, returns the monitor with the
    largest overlap area (most of the window is on that monitor).

    Args:
        window_geometry: Dict with keys: x, y, width, height (in virtual screen coordinates)
        monitors: Optional pre-fetched monitor list

    Returns:
        Monitor containing most of the window, or None if window_geometry is invalid

    Example:
        >>> geo = {'x': 100, 'y': 100, 'width': 1920, 'height': 1080}
        >>> monitor = get_monitor_for_window(geo)
        >>> print(f"Window is mostly on {monitor.name}")
    """
    if not window_geometry:
        return None

    if monitors is None:
        monitors = get_monitors()

    # Extract window bounds
    try:
        win_x = window_geometry['x']
        win_y = window_geometry['y']
        win_width = window_geometry['width']
        win_height = window_geometry['height']
    except (KeyError, TypeError) as e:
        logger.warning(f"Invalid window geometry: {window_geometry}: {e}")
        return None

    # Calculate window center point
    center_x = win_x + win_width // 2
    center_y = win_y + win_height // 2

    # First try: monitor containing center point (fast path for most cases)
    center_monitor = get_monitor_at_point(center_x, center_y, monitors)
    if center_monitor:
        # Verify the center monitor has significant overlap (> 50%)
        overlap = center_monitor.overlap_area(win_x, win_y, win_width, win_height)
        window_area = win_width * win_height
        if overlap > window_area * 0.5:
            return center_monitor

    # Second try: find monitor with largest overlap (for spanning windows)
    max_overlap = 0
    best_monitor = None

    for m in monitors:
        overlap = m.overlap_area(win_x, win_y, win_width, win_height)
        if overlap > max_overlap:
            max_overlap = overlap
            best_monitor = m

    if best_monitor:
        logger.debug(f"Window spans monitors, largest overlap on {best_monitor.name}")
        return best_monitor

    # Fallback: return primary monitor
    primary = next((m for m in monitors if m.is_primary), None)
    if primary:
        logger.debug(f"Window outside all monitors, using primary: {primary.name}")
        return primary

    # Last resort: return first monitor
    if monitors:
        logger.warning(f"No primary monitor, using first: {monitors[0].name}")
        return monitors[0]

    return None


def get_primary_monitor(monitors: list[Monitor] = None) -> Optional[Monitor]:
    """Get the primary monitor.

    Args:
        monitors: Optional pre-fetched monitor list

    Returns:
        Primary monitor, or first monitor if no primary set, or None if no monitors
    """
    if monitors is None:
        monitors = get_monitors()

    # Find primary monitor
    primary = next((m for m in monitors if m.is_primary), None)
    if primary:
        return primary

    # No primary - return first monitor
    if monitors:
        logger.warning("No primary monitor found, using first monitor")
        return monitors[0]

    return None
