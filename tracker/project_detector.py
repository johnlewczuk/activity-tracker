"""
Detect project context from window titles, file paths, and app usage.

Uses heuristics to identify distinct project contexts:
- Git repository paths in terminal/editor titles
- URL domains in browser titles
- Distinct application contexts
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


@dataclass
class ProjectContext:
    """Represents a detected project/context"""
    name: str                          # e.g., "activity-tracker", "acusight", "browsing"
    confidence: float                  # 0.0 - 1.0
    source: str                        # How it was detected: "path", "git", "url", "app"
    identifiers: list[str] = field(default_factory=list)  # Matching patterns found


class ProjectDetector:
    """
    Detect project context from window metadata.

    Strategies:
    1. File paths in editor/terminal titles (most reliable)
    2. Git repo/branch in terminal prompts
    3. URL domains for browser activity
    4. App-based context (Slack = communication, etc.)
    """

    # Apps that indicate specific contexts (not project-specific)
    CONTEXT_APPS = {
        'slack': 'communication',
        'discord': 'communication',
        'teams': 'communication',
        'zoom': 'meetings',
        'thunderbird': 'email',
        'evolution': 'email',
        'spotify': 'music',
        'vlc': 'media',
    }

    # Patterns to extract project name from paths
    PATH_PATTERNS = [
        # /home/user/projects/PROJECT_NAME/...
        r'/(?:home|Users)/[^/]+/(?:projects?|repos?|dev|src|code|work|Developer)/([^/]+)',
        # /home/user/PROJECT_NAME/src/...
        r'/(?:home|Users)/[^/]+/([^/]+)/(?:src|lib|app|pkg|cmd)',
        # ~/PROJECT_NAME - ...  (VS Code style)
        r'~/([^/\s]+)\s*[-–—]',
        # PROJECT_NAME/file.py - Editor (VS Code, etc.)
        r'^([a-zA-Z][\w-]+)/[\w/]+\.\w+\s*[-–—]',
    ]

    # Git branch patterns in terminal prompts
    GIT_PATTERNS = [
        # (main), (master), (feature/xyz)
        r'\(([^)]+)\)',
        # git:main, git:feature/xyz
        r'git:(\S+)',
        # [main], [feature/xyz]
        r'\[([^\]]+)\]',
    ]

    # URL domain extraction
    URL_PATTERNS = [
        r'https?://(?:www\.)?([^/\s]+)',
        r'([a-zA-Z0-9-]+\.(?:com|org|io|dev|net|app))',
    ]

    def detect(self, window_title: str, app_name: str) -> ProjectContext:
        """
        Detect project context from window title and app name.

        Returns ProjectContext with best guess at project/context.
        """
        window_title = window_title or ""
        app_name = (app_name or "").lower()

        # 1. Check for context-specific apps first
        for app_pattern, context in self.CONTEXT_APPS.items():
            if app_pattern in app_name:
                return ProjectContext(
                    name=context,
                    confidence=0.9,
                    source="app",
                    identifiers=[app_name]
                )

        # 2. Try to extract project from file path
        project = self._extract_from_path(window_title)
        if project:
            return project

        # 3. Try to extract from URL (browser)
        if any(b in app_name for b in ['chrome', 'firefox', 'chromium', 'brave', 'edge']):
            url_context = self._extract_from_url(window_title)
            if url_context:
                return url_context

        # 4. Try git branch/repo detection
        git_context = self._extract_from_git(window_title)
        if git_context:
            return git_context

        # 5. Fallback: use app name as context
        return ProjectContext(
            name=app_name or "unknown",
            confidence=0.3,
            source="app_fallback",
            identifiers=[app_name]
        )

    def _extract_from_path(self, title: str) -> Optional[ProjectContext]:
        """Extract project name from file path in title"""
        for pattern in self.PATH_PATTERNS:
            match = re.search(pattern, title)
            if match:
                project_name = match.group(1).lower()
                # Filter out generic names
                if project_name not in ['src', 'app', 'lib', 'bin', 'home', 'user', 'root']:
                    return ProjectContext(
                        name=project_name,
                        confidence=0.85,
                        source="path",
                        identifiers=[match.group(0)]
                    )
        return None

    def _extract_from_url(self, title: str) -> Optional[ProjectContext]:
        """Extract domain/site context from browser title"""
        # Common patterns: "Page Title - Site Name" or URL in title

        # Check for GitHub/GitLab with repo
        gh_match = re.search(r'github\.com/([^/]+/[^/\s]+)', title, re.I)
        if gh_match:
            return ProjectContext(
                name=gh_match.group(1).split('/')[-1].lower(),  # repo name
                confidence=0.8,
                source="github",
                identifiers=[gh_match.group(0)]
            )

        gl_match = re.search(r'gitlab\.com/([^/]+/[^/\s]+)', title, re.I)
        if gl_match:
            return ProjectContext(
                name=gl_match.group(1).split('/')[-1].lower(),
                confidence=0.8,
                source="gitlab",
                identifiers=[gl_match.group(0)]
            )

        # Generic URL extraction
        for pattern in self.URL_PATTERNS:
            match = re.search(pattern, title)
            if match:
                domain = match.group(1).lower()
                # Classify common sites
                if 'stackoverflow' in domain or 'stackexchange' in domain:
                    return ProjectContext(name="research", confidence=0.6, source="url", identifiers=[domain])
                elif 'docs.' in domain or 'documentation' in title.lower():
                    return ProjectContext(name="documentation", confidence=0.6, source="url", identifiers=[domain])
                elif any(news in domain for news in ['news', 'reddit', 'twitter', 'bbc', 'cnn', 'nytimes']):
                    return ProjectContext(name="browsing", confidence=0.7, source="url", identifiers=[domain])
                else:
                    return ProjectContext(name=domain, confidence=0.5, source="url", identifiers=[domain])

        return None

    def _extract_from_git(self, title: str) -> Optional[ProjectContext]:
        """Extract project context from git info in terminal prompt"""
        for pattern in self.GIT_PATTERNS:
            match = re.search(pattern, title)
            if match:
                branch = match.group(1)
                # Branch might contain project hint
                if '/' in branch:  # feature/project-thing
                    return ProjectContext(
                        name=branch.split('/')[0],
                        confidence=0.6,
                        source="git_branch",
                        identifiers=[branch]
                    )
        return None


def group_by_project(
    items: list[dict],
    detector: ProjectDetector = None,
    window_title_key: str = 'window_title',
    app_name_key: str = 'app_name'
) -> dict[str, list[dict]]:
    """
    Group a list of items (screenshots, focus events, etc.) by detected project.

    Returns dict mapping project_name -> list of items
    """
    if detector is None:
        detector = ProjectDetector()

    grouped = defaultdict(list)

    for item in items:
        context = detector.detect(
            item.get(window_title_key, ''),
            item.get(app_name_key, '')
        )
        grouped[context.name].append(item)

    return dict(grouped)
