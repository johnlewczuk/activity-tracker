# Activity Tracker MVP

## Session Start Instructions
When starting a new session on this project:
1. Run `git status`, `git diff`, and `git log --oneline -10` to understand recent changes
2. Check for any TODO comments: `grep -r "TODO" tracker/`
3. Review the Decision Log below for context
4. Inform user that you've reviewed recent changes and ask what to work on next

**IMPORTANT:** After every task, check if CLAUDE.md needs to be updated

## Project Goal
Linux background service that captures screenshots at intervals, stores metadata in SQLite, and provides a simple web viewer.

## Tech Stack
- Python 3.11+
- mss for screenshots
- SQLite for metadata
- Flask for web viewer
- WebP for image compression
- pytest for testing

## Architecture
- Capture daemon: runs via systemd user service
- Storage: ~/activity-tracker-data/
  - screenshots/YYYY/MM/DD/{timestamp}_{hash}.webp
  - activity.db (SQLite)
- Web viewer: localhost:5000

## Key Constraints
- X11 first (Wayland support later)
- Fixed 30-second intervals for MVP
- No OCR in MVP
- Single monitor assumption for MVP

## File Structure
```
activity-tracker/
├── tracker/
│   ├── __init__.py
│   ├── capture.py           # Screenshot capture logic
│   ├── storage.py           # SQLite + filesystem management
│   ├── daemon.py            # Background service daemon
│   ├── config.py            # YAML-based configuration management
│   ├── analytics.py         # Activity analytics and statistics
│   ├── vision.py            # AI summarization (OCR + LLM)
│   ├── summarizer_worker.py # Background worker for threshold-based summarization
│   ├── afk.py               # AFK detection via pynput
│   └── sessions.py          # Session management
├── web/
│   ├── app.py               # Flask application with REST API
│   └── templates/
│       ├── timeline.html    # Timeline view with AI summaries
│       ├── analytics.html   # Analytics dashboard
│       └── settings.html    # Configuration UI
├── tests/                   # Pytest test suite
│   ├── conftest.py          # Test fixtures
│   ├── test_capture.py      # Capture functionality tests
│   ├── test_storage.py      # Storage CRUD tests
│   └── test_dhash.py        # Hash comparison tests
├── scripts/
│   ├── install.sh           # Systemd service setup (auto-enables web + summarization)
│   └── summarize_activity.py  # CLI for generating summaries
├── requirements.txt
├── README.md                # Project documentation
└── CLAUDE.md
```

## Decision Log
- **2025-11-27**: Added comprehensive test suite with pytest (85% coverage target)
- **2025-11-27**: Added full docstrings to all modules following PEP 257
- **2025-11-27**: Created README.md with installation and usage instructions
- **2025-11-27**: Identified 13 edge cases requiring attention (see TODO comments)

### 2024-12-02 - Phase 1: Timeline + Analytics
- Building rich timeline UI with calendar heatmap + hourly drill-down
- Full analytics dashboard with charts (using Chart.js)
- New routes: /timeline, /analytics, /api/activity-data
- Keeping existing day view, timeline is additive
- Stack: Flask + Jinja2 + Chart.js + vanilla JS (no React)

### 2025-12-08 - Phase 2: Session-Based Tracking
- Implemented AFK detection using pynput (keyboard/mouse monitoring)
- Session management: automatic session start/end on AFK transitions
- Screenshots linked to sessions via junction table
- Session summaries with context continuity (previous session context)
- OCR caching per unique window title within sessions
- Smart session resume on daemon restart (checks if within AFK timeout)
- API request details stored for debugging (prompt_text column)
- Info icons added throughout UI with tooltips
- Live counts for ongoing sessions (calculated from session_screenshots)
- Generate button shows loading feedback immediately
- Window geometry detection and cropping for improved OCR/LLM accuracy
  - Captures focused window bounds using xdotool
  - Stores window geometry (x, y, width, height) in database
  - Creates cropped screenshots on-demand for OCR and summarization
  - Cached as {original}_crop.webp files for performance
  - Handles edge cases: fullscreen apps, partially off-screen windows, missing geometry
  - Multi-monitor support via geometry coordinates
- Multi-monitor support with intelligent capture
  - Detects connected monitors using xrandr
  - Captures only the monitor with the focused window
  - Stores monitor metadata (name, width, height) in database
  - Window geometry coordinates relative to captured monitor
  - Automatic fallback to primary monitor when no focused window
  - 60-second cache refresh for hotplug support
  - Handles edge cases: window spanning monitors, xrandr unavailable, monitor hotplug
  - Significant file size reduction (e.g., 5K instead of dual 10K virtual screen)

### 2025-12-09 - Phase 3: Threshold-Based Summarization
- Replaced session-based summarization with threshold-based approach
- Summaries triggered after every N screenshots (configurable, default: 10)
- Background SummarizerWorker with queue-based processing
- New threshold_summaries table with version history support
- Regenerate summaries with different models/settings
- Timeline UI shows AI summaries panel with regenerate/history/delete actions
- Settings page loads Ollama models dynamically from API
- Model changes take effect immediately (no daemon restart needed)
- Install script now auto-enables web server and summarization (no prompts)

## Known Issues (TODO Comments Added)
- **Multi-monitor support**: ✅ RESOLVED - Captures only active monitor, stores monitor metadata
- **Wayland compatibility**: Assumes X11, needs display server detection (xdotool + xrandr requirement)
- **Permission handling**: Missing checks for directory/file access
- **Configuration**: ✅ RESOLVED - config.py with YAML-based ConfigManager
- **Error resilience**: Daemon needs better error recovery

## Testing
Run tests with: `pytest tests/ --cov=tracker --cov-report=html`
Test categories: capture, storage, dhash, integration

