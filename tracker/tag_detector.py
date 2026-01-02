"""Auto-detect activity tags from app and window patterns.

This module analyzes focus events and classifies them into meaningful
activity categories (tags) based on the app name and window title patterns.
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class TagRule:
    """Rule for detecting a tag from app/window patterns."""
    apps: List[str]  # App name patterns (case-insensitive substring match)
    windows: List[str]  # Window title patterns (regex patterns)
    color: str  # CSS color for visualization


# Tag detection rules - order matters for priority
TAG_RULES: Dict[str, TagRule] = {
    '#coding': TagRule(
        apps=['code', 'vscode', 'pycharm', 'intellij', 'webstorm', 'vim', 'nvim',
              'neovim', 'emacs', 'sublime', 'atom', 'zed'],
        windows=[
            r'\.py\b', r'\.js\b', r'\.ts\b', r'\.tsx\b', r'\.jsx\b',
            r'\.go\b', r'\.rs\b', r'\.rb\b', r'\.java\b', r'\.kt\b',
            r'\.html\b', r'\.css\b', r'\.scss\b', r'\.vue\b', r'\.svelte\b',
            r'\.c\b', r'\.cpp\b', r'\.h\b', r'\.hpp\b',
            r'\.md\b.*(?:code|vscode|vim)',  # Markdown in editors
            r'\[Running\]', r'\[Debug\]',  # IDE debug indicators
        ],
        color='#6366f1'  # Indigo
    ),
    '#research': TagRule(
        apps=[],  # Detected via browser window titles
        windows=[
            r'dribbble', r'figma', r'behance', r'awwwards',
            r'stackoverflow', r'stack overflow', r'github\.com',
            r'gitlab', r'bitbucket',
            r'docs\.', r'documentation', r'api reference',
            r'medium\.com', r'dev\.to', r'hashnode',
            r'arxiv', r'scholar\.google', r'research',
            r'wikipedia', r'wiki',
            r'reddit.*programming', r'hacker news', r'hn\b',
            r'tutorial', r'guide', r'how to',
        ],
        color='#f59e0b'  # Amber
    ),
    '#communication': TagRule(
        apps=['slack', 'discord', 'teams', 'element', 'signal', 'telegram',
              'whatsapp', 'messenger'],
        windows=[
            r'gmail', r'outlook', r'protonmail', r'mail\b',
            r'inbox', r'compose.*mail',
            r'linkedin.*messag', r'twitter.*dm', r'x\.com.*messages',
        ],
        color='#22c55e'  # Green
    ),
    '#meetings': TagRule(
        apps=['zoom', 'teams', 'webex', 'gotomeeting', 'bluejeans'],
        windows=[
            r'google meet', r'meet\.google',
            r'zoom meeting', r'zoom webinar',
            r'microsoft teams.*call', r'teams.*meeting',
            r'huddle', r'standup', r'sync\b',
        ],
        color='#ec4899'  # Pink
    ),
    '#writing': TagRule(
        apps=['notion', 'obsidian', 'logseq', 'roam', 'bear', 'ulysses',
              'typora', 'marktext', 'ia writer'],
        windows=[
            r'google docs', r'docs\.google',
            r'notion\.so',
            r'confluence',
            r'dropbox paper',
            r'coda\.io',
            r'airtable',
            r'\.md\b',  # Markdown files (when not in code editor)
        ],
        color='#8b5cf6'  # Purple
    ),
    '#terminal': TagRule(
        apps=['terminal', 'iterm', 'iterm2', 'tilix', 'konsole',
              'gnome-terminal', 'alacritty', 'kitty', 'wezterm', 'hyper'],
        windows=[],  # Any terminal window matches
        color='#14b8a6'  # Teal
    ),
    '#media': TagRule(
        apps=['spotify', 'vlc', 'mpv', 'netflix', 'youtube', 'prime video',
              'plex', 'audacity', 'ableton', 'logic'],
        windows=[
            r'youtube\.com', r'youtu\.be',
            r'netflix\.com', r'hulu\.com', r'disney\+',
            r'spotify\.com', r'music\.apple',
            r'twitch\.tv',
        ],
        color='#f43f5e'  # Rose
    ),
    '#browsing': TagRule(
        apps=['chrome', 'firefox', 'brave', 'safari', 'edge', 'arc', 'vivaldi', 'opera'],
        windows=[],  # Fallback for browser without specific patterns
        color='#64748b'  # Slate (neutral)
    ),
}

# Default tag for unclassified activities
DEFAULT_TAG = '#other'
DEFAULT_TAG_COLOR = '#94a3b8'  # Gray


def detect_tag(app_name: Optional[str], window_title: Optional[str]) -> str:
    """Detect the most appropriate tag for a focus event.

    Args:
        app_name: The application name (e.g., 'code', 'Chrome').
        window_title: The window title (e.g., 'reports.py - activity-tracker').

    Returns:
        The detected tag (e.g., '#coding', '#research').
    """
    if not app_name and not window_title:
        return DEFAULT_TAG

    app_lower = (app_name or '').lower()
    window_lower = (window_title or '').lower()

    # First pass: check for specific content-based tags (research, meetings, etc.)
    # These take priority over app-based detection
    for tag, rule in TAG_RULES.items():
        if tag in ('#browsing', '#terminal'):  # Skip fallback tags in first pass
            continue

        # Check window title patterns first (more specific)
        for pattern in rule.windows:
            if re.search(pattern, window_lower, re.IGNORECASE):
                return tag

        # Then check app name patterns
        for app_pattern in rule.apps:
            if app_pattern.lower() in app_lower:
                return tag

    # Second pass: app-based fallbacks (terminal, browsing)
    for tag in ('#terminal', '#browsing'):
        rule = TAG_RULES[tag]
        for app_pattern in rule.apps:
            if app_pattern.lower() in app_lower:
                return tag

    return DEFAULT_TAG


def get_tag_color(tag: str) -> str:
    """Get the color for a tag.

    Args:
        tag: The tag name (e.g., '#coding').

    Returns:
        CSS color string (e.g., '#6366f1').
    """
    if tag in TAG_RULES:
        return TAG_RULES[tag].color
    return DEFAULT_TAG_COLOR


def get_all_tags() -> List[str]:
    """Get all defined tags in priority order."""
    return list(TAG_RULES.keys()) + [DEFAULT_TAG]


def get_tag_colors() -> Dict[str, str]:
    """Get a mapping of all tags to their colors."""
    colors = {tag: rule.color for tag, rule in TAG_RULES.items()}
    colors[DEFAULT_TAG] = DEFAULT_TAG_COLOR
    return colors


@dataclass
class TaggedActivity:
    """An activity with its detected tag."""
    tag: str
    app_name: str
    window_title: str
    duration_seconds: float
    color: str


def tag_focus_events(focus_events: List[Dict]) -> Dict[str, List[TaggedActivity]]:
    """Tag and group focus events by their detected category.

    Args:
        focus_events: List of focus event dicts from storage.

    Returns:
        Dict mapping tags to lists of TaggedActivity objects.
    """
    tagged: Dict[str, List[TaggedActivity]] = {}

    for event in focus_events:
        app_name = event.get('app_name') or ''
        window_title = event.get('window_title') or ''
        duration = event.get('duration_seconds') or 0

        tag = detect_tag(app_name, window_title)
        color = get_tag_color(tag)

        activity = TaggedActivity(
            tag=tag,
            app_name=app_name,
            window_title=window_title,
            duration_seconds=duration,
            color=color
        )

        if tag not in tagged:
            tagged[tag] = []
        tagged[tag].append(activity)

    return tagged


@dataclass
class TagBreakdown:
    """Summary of time spent on a tag."""
    tag: str
    total_seconds: float
    percentage: float
    color: str
    windows: List[Dict]  # List of {window_title, app_name, duration_seconds}


def get_tag_breakdown(focus_events: List[Dict]) -> List[TagBreakdown]:
    """Get a breakdown of time by tag with window details.

    Args:
        focus_events: List of focus event dicts from storage.

    Returns:
        List of TagBreakdown objects sorted by total time descending.
    """
    # Group events by tag
    tag_events = tag_focus_events(focus_events)

    # Calculate total time for percentage calculation
    total_time = sum(
        event.get('duration_seconds') or 0
        for event in focus_events
    )

    if total_time == 0:
        return []

    breakdowns = []

    for tag, activities in tag_events.items():
        # Aggregate time by window
        window_times: Dict[Tuple[str, str], float] = {}
        for activity in activities:
            key = (activity.app_name, activity.window_title)
            window_times[key] = window_times.get(key, 0) + activity.duration_seconds

        # Sort windows by time descending
        sorted_windows = sorted(
            [
                {
                    'app_name': app_name,
                    'window_title': window_title,
                    'duration_seconds': duration
                }
                for (app_name, window_title), duration in window_times.items()
            ],
            key=lambda x: x['duration_seconds'],
            reverse=True
        )

        tag_total = sum(a.duration_seconds for a in activities)

        breakdowns.append(TagBreakdown(
            tag=tag,
            total_seconds=tag_total,
            percentage=(tag_total / total_time) * 100,
            color=get_tag_color(tag),
            windows=sorted_windows[:10]  # Limit to top 10 windows per tag
        ))

    # Sort by total time descending
    breakdowns.sort(key=lambda x: x.total_seconds, reverse=True)

    return breakdowns
