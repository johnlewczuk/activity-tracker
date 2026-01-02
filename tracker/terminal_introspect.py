"""Terminal Process Introspection Module.

This module provides functionality to inspect what processes are running inside
terminal emulators by walking the process tree via /proc filesystem.

When a terminal (Tilix, gnome-terminal, etc.) is focused, this module can
determine:
- What command is running (vim, python, npm, etc.)
- The working directory
- Whether it's an SSH session
- Tmux session info if applicable
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Set

logger = logging.getLogger(__name__)

# Terminal emulator app names (lowercase for comparison)
TERMINAL_APPS: Set[str] = {
    'tilix', 'gnome-terminal', 'gnome-terminal-', 'gnome-terminal-server',
    'konsole', 'xterm', 'alacritty', 'kitty', 'terminator',
    'urxvt', 'rxvt', 'xfce4-terminal', 'mate-terminal', 'lxterminal',
    'terminology', 'st', 'sakura', 'guake', 'yakuake', 'tilda',
}

# Shell process names (used to identify intermediate shells)
SHELL_NAMES: Set[str] = {
    'bash', 'zsh', 'fish', 'sh', 'dash', 'tcsh', 'csh', 'ksh',
}

# Processes to skip when looking for "interesting" foreground process
SKIP_PROCESSES: Set[str] = SHELL_NAMES | {
    'tmux: client', 'tmux: server', 'screen',
}


@dataclass
class TerminalContext:
    """Context information about what's running in a terminal."""
    foreground_process: str      # e.g., "vim", "python", "npm"
    full_command: str            # e.g., "vim tracker/daemon.py"
    working_directory: str       # e.g., "/home/user/projects/activity-tracker"
    shell: str                   # e.g., "bash", "zsh"
    is_ssh: bool                 # True if connected via SSH
    tmux_session: Optional[str]  # e.g., "dev" if in tmux

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string for database storage."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> Optional['TerminalContext']:
        """Create from JSON string."""
        try:
            data = json.loads(json_str)
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def format_short(self) -> str:
        """Format for display in focus context (concise)."""
        parts = []

        # Main process
        if self.foreground_process and self.foreground_process not in SHELL_NAMES:
            parts.append(self.foreground_process)
        elif self.shell:
            parts.append(f"{self.shell} (idle)")

        # Working directory (just the last component)
        if self.working_directory:
            dir_name = Path(self.working_directory).name
            if dir_name and dir_name != self.foreground_process:
                parts.append(f"in {dir_name}")

        # SSH indicator
        if self.is_ssh:
            parts.append("[ssh]")

        # Tmux session
        if self.tmux_session:
            parts.append(f"[tmux:{self.tmux_session}]")

        return " ".join(parts) if parts else "terminal"


def is_terminal_app(app_name: Optional[str]) -> bool:
    """Check if an app name corresponds to a terminal emulator.

    Args:
        app_name: The application name from window properties.

    Returns:
        True if this is a terminal emulator.
    """
    if not app_name:
        return False
    return app_name.lower() in TERMINAL_APPS


def get_terminal_context(window_pid: int) -> Optional[TerminalContext]:
    """Get context about what's running in a terminal window.

    Walks the process tree from the terminal window PID to find:
    - The foreground process (what the user is actively running)
    - Working directory
    - Shell type
    - SSH and tmux detection

    For terminals with tmux attached, queries tmux directly for accurate
    active pane information (works correctly with tiling terminals like Tilix).

    Args:
        window_pid: PID of the terminal window (from xdotool getwindowpid).

    Returns:
        TerminalContext with process details, or None if inspection fails.
    """
    try:
        # Get all descendant processes
        descendants = _get_descendant_pids(window_pid)
        if not descendants:
            # Terminal might be empty or just started
            return _get_process_context(window_pid)

        # Check if this terminal's shell is actually running inside tmux
        # We verify by checking for TMUX env var, not just "tmux" in process names.
        # This prevents querying tmux for terminals that aren't attached to it.
        shell_in_tmux = _any_shell_in_tmux(descendants)

        if shell_in_tmux:
            # Use tmux-aware introspection for accurate active pane detection
            # This queries tmux directly and handles SSH detection internally
            tmux_context = _get_tmux_active_pane_context()
            if tmux_context:
                return tmux_context

        # Fallback: Find the "interesting" foreground process (deepest non-shell)
        foreground_pid = _find_foreground_process(descendants)
        if foreground_pid is None:
            foreground_pid = descendants[-1] if descendants else window_pid

        # Get context from the foreground process
        context = _get_process_context(foreground_pid)
        if context is None:
            return None

        # Check for SSH anywhere in the tree
        context.is_ssh = _check_ssh_in_tree(descendants)

        # Check for tmux
        context.tmux_session = _get_tmux_session(descendants)

        return context

    except Exception as e:
        logger.debug(f"Terminal introspection failed for PID {window_pid}: {e}")
        return None


def _get_immediate_children(pid: int) -> List[int]:
    """Get immediate child PIDs of a process (not recursive).

    Args:
        pid: Parent process ID.

    Returns:
        List of immediate child PIDs.
    """
    try:
        children_path = Path(f"/proc/{pid}/task/{pid}/children")
        if children_path.exists():
            children_str = children_path.read_text().strip()
            if children_str:
                return [int(p) for p in children_str.split()]
    except (OSError, ValueError):
        pass
    return []


def _get_descendant_pids(pid: int) -> List[int]:
    """Get all descendant PIDs of a process (depth-first order).

    Uses /proc to find child processes recursively.

    Args:
        pid: Parent process ID.

    Returns:
        List of descendant PIDs in depth-first order.
    """
    descendants = []
    to_visit = [pid]
    visited = set()

    while to_visit:
        current = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)

        # Find children of current
        try:
            children_path = Path(f"/proc/{current}/task/{current}/children")
            if children_path.exists():
                children_str = children_path.read_text().strip()
                if children_str:
                    children = [int(p) for p in children_str.split()]
                    descendants.extend(children)
                    to_visit.extend(children)
        except (OSError, ValueError):
            continue

    return descendants


def _process_in_tmux(pid: int) -> bool:
    """Check if a process is running inside a tmux session.

    Reads the process's environment to check for the TMUX variable,
    which tmux sets in shells it spawns.

    Args:
        pid: Process ID to check.

    Returns:
        True if the process has TMUX environment variable set.
    """
    try:
        env_path = Path(f"/proc/{pid}/environ")
        if not env_path.exists():
            return False
        # Environment is null-separated key=value pairs
        env_bytes = env_path.read_bytes()
        env_str = env_bytes.decode('utf-8', errors='replace')
        # Check for TMUX= in the environment
        for entry in env_str.split('\x00'):
            if entry.startswith('TMUX='):
                return True
        return False
    except (OSError, PermissionError):
        return False


def _any_shell_in_tmux(pids: List[int]) -> bool:
    """Check if any shell process in the list is running inside tmux.

    This is more reliable than just checking for 'tmux' in process names,
    because it verifies the shell is actually attached to a tmux session
    (not just that tmux is running somewhere on the system).

    Args:
        pids: List of process IDs to check.

    Returns:
        True if any shell process has TMUX environment variable.
    """
    for pid in pids:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if comm in SHELL_NAMES:
                if _process_in_tmux(pid):
                    return True
        except OSError:
            continue
    return False


def _find_foreground_process(pids: List[int]) -> Optional[int]:
    """Find the most interesting foreground process from a list of PIDs.

    Looks for the deepest process that isn't a shell or multiplexer.

    Args:
        pids: List of process IDs to examine.

    Returns:
        PID of the foreground process, or None if all are shells.
    """
    # Work backwards (deepest processes first)
    for pid in reversed(pids):
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if comm not in SKIP_PROCESSES:
                return pid
        except OSError:
            continue

    return None


def _get_process_context(pid: int) -> Optional[TerminalContext]:
    """Get context information for a specific process.

    Args:
        pid: Process ID to inspect.

    Returns:
        TerminalContext with process details.
    """
    try:
        proc_path = Path(f"/proc/{pid}")

        # Get process name
        comm = (proc_path / "comm").read_text().strip()

        # Get full command line
        try:
            cmdline_bytes = (proc_path / "cmdline").read_bytes()
            # cmdline is null-separated
            cmdline_parts = cmdline_bytes.decode('utf-8', errors='replace').split('\x00')
            full_command = ' '.join(p for p in cmdline_parts if p).strip()
        except OSError:
            full_command = comm

        # Get working directory
        try:
            cwd = os.readlink(proc_path / "cwd")
        except OSError:
            cwd = ""

        # Determine shell (check parent processes)
        shell = _find_shell_in_ancestry(pid)

        return TerminalContext(
            foreground_process=comm,
            full_command=full_command[:200],  # Limit length
            working_directory=cwd,
            shell=shell or "unknown",
            is_ssh=False,  # Will be set by caller
            tmux_session=None,  # Will be set by caller
        )

    except OSError as e:
        logger.debug(f"Failed to get process context for PID {pid}: {e}")
        return None


def _find_shell_in_ancestry(pid: int) -> Optional[str]:
    """Find the shell process in the ancestry of a PID.

    Args:
        pid: Process ID to start from.

    Returns:
        Shell name (bash, zsh, etc.) or None.
    """
    current = pid
    visited = set()

    while current > 1 and current not in visited:
        visited.add(current)
        try:
            comm = Path(f"/proc/{current}/comm").read_text().strip()
            if comm in SHELL_NAMES:
                return comm

            # Get parent PID
            stat = Path(f"/proc/{current}/stat").read_text()
            # stat format: pid (comm) state ppid ...
            # Need to handle comm with spaces/parens
            close_paren = stat.rfind(')')
            ppid = int(stat[close_paren+2:].split()[1])
            current = ppid

        except (OSError, ValueError, IndexError):
            break

    return None


def _check_ssh_in_tree(pids: List[int]) -> bool:
    """Check if any process in the tree indicates an SSH session.

    Args:
        pids: List of process IDs to check.

    Returns:
        True if SSH is detected.
    """
    ssh_indicators = {'ssh', 'sshd', 'mosh-client', 'mosh-server'}

    for pid in pids:
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if comm in ssh_indicators:
                return True
        except OSError:
            continue

    return False


def _get_tmux_session(pids: List[int]) -> Optional[str]:
    """Get tmux session name if running in tmux.

    Only queries tmux if a shell process in the list is actually inside
    a tmux session (has TMUX env var). This prevents returning tmux info
    from other terminals that aren't attached to this session.

    Args:
        pids: List of process IDs to check.

    Returns:
        Tmux session name or None.
    """
    # Verify a shell is actually running inside tmux by checking env var
    if not _any_shell_in_tmux(pids):
        return None

    # Try to get current tmux session name
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#{session_name}'],
            capture_output=True,
            text=True,
            timeout=1
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return "tmux"  # Generic fallback


def _get_tmux_active_pane_context() -> Optional[TerminalContext]:
    """Get context from tmux's active pane.

    When tmux is running, this queries tmux directly for accurate
    information about what's running in the currently focused pane.
    This is more reliable than process tree walking for multi-pane setups.

    IMPORTANT: We trust tmux's pane_current_command as the foreground process
    rather than walking the process tree, because background child processes
    (like MCP servers spawned by Claude Code) would incorrectly be identified
    as the foreground process.

    Returns:
        TerminalContext from the active tmux pane, or None if query fails.
    """
    try:
        # Query tmux for active pane info in a single call
        # Format: command|path|pid|session
        result = subprocess.run(
            ['tmux', 'display-message', '-p',
             '#{pane_current_command}|#{pane_current_path}|#{pane_pid}|#{session_name}'],
            capture_output=True,
            text=True,
            timeout=1
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        parts = result.stdout.strip().split('|')
        if len(parts) < 4:
            return None

        pane_command = parts[0]
        pane_path = parts[1]
        pane_pid = parts[2]
        session_name = parts[3]

        # pane_current_command from tmux is the ACTUAL foreground process
        # This is what the user is interacting with, not background children
        foreground_process = pane_command
        full_command = pane_command

        # For shell processes, check if there's a direct child (immediate foreground job)
        # But don't walk the entire tree - background processes aren't foreground
        children = []
        if pane_command in SHELL_NAMES and pane_pid.isdigit():
            pid = int(pane_pid)
            # Only get immediate children, not the full descendant tree
            immediate_children = _get_immediate_children(pid)
            if immediate_children:
                # Get context from the first non-shell immediate child
                for child_pid in immediate_children:
                    ctx = _get_process_context(child_pid)
                    if ctx and ctx.foreground_process not in SHELL_NAMES:
                        foreground_process = ctx.foreground_process
                        full_command = ctx.full_command
                        break

            # Get all descendants for SSH detection
            children = _get_descendant_pids(pid)

        # If foreground is a known app (not shell), get its cmdline for full_command
        elif pane_pid.isdigit():
            pid = int(pane_pid)
            # Find the process matching pane_command to get its full cmdline
            children = _get_descendant_pids(pid)
            for child_pid in children:
                ctx = _get_process_context(child_pid)
                if ctx and ctx.foreground_process == pane_command:
                    full_command = ctx.full_command
                    break

        # Determine shell type
        shell = _find_shell_in_ancestry(int(pane_pid)) if pane_pid.isdigit() else "bash"
        if not shell:
            shell = "bash"

        return TerminalContext(
            foreground_process=foreground_process,
            full_command=full_command,
            working_directory=pane_path,
            shell=shell,
            is_ssh=_check_ssh_in_tree(children),
            tmux_session=session_name
        )

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError) as e:
        logger.debug(f"Failed to get tmux active pane context: {e}")
        return None


def get_window_pid(window_id: str) -> Optional[int]:
    """Get the PID of a window using xdotool.

    Args:
        window_id: X11 window ID.

    Returns:
        Process ID or None if lookup fails.
    """
    try:
        result = subprocess.run(
            ['xdotool', 'getwindowpid', window_id],
            capture_output=True,
            text=True,
            timeout=1
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        pass
    return None


# For testing
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        # Test with provided PID
        pid = int(sys.argv[1])
        print(f"Testing with PID: {pid}")
        context = get_terminal_context(pid)
        if context:
            print(f"Context: {context}")
            print(f"Short: {context.format_short()}")
            print(f"JSON: {context.to_json()}")
        else:
            print("Failed to get context")
    else:
        # Test with active window
        try:
            result = subprocess.run(
                ['xdotool', 'getactivewindow'],
                capture_output=True,
                text=True,
                timeout=1
            )
            window_id = result.stdout.strip()
            print(f"Active window ID: {window_id}")

            window_pid = get_window_pid(window_id)
            print(f"Window PID: {window_pid}")

            if window_pid:
                context = get_terminal_context(window_pid)
                if context:
                    print(f"Context: {context}")
                    print(f"Short: {context.format_short()}")
                else:
                    print("Failed to get context (might not be a terminal)")
        except Exception as e:
            print(f"Error: {e}")
