# Project Context

## Purpose
Activity Tracker is a Linux background service that captures screenshots at configurable intervals, stores metadata in SQLite, and provides AI-powered activity summarization. It helps users understand how they spend their time by:
- Capturing screenshots of the active monitor
- Tracking window focus events with duration
- Generating AI summaries of work sessions using local LLMs (Ollama)
- Providing analytics dashboards and report generation

## Tech Stack
- **Language**: Python 3.11+
- **Screenshot Capture**: mss (multi-monitor support)
- **Database**: SQLite for metadata storage
- **Web Framework**: Flask with Jinja2 templates
- **Frontend**: Vanilla JS + Chart.js (no React/Vue)
- **Image Format**: WebP for compression
- **AI/LLM**: Ollama (local models, default: gemma3:12b-it-qat)
- **OCR**: Tesseract (optional, for text extraction)
- **Testing**: pytest with 85% coverage target
- **Service Management**: systemd user service

## Project Conventions

### Code Style
- Follow PEP 8 for Python code
- Full docstrings on all modules following PEP 257
- Type hints encouraged but not mandatory
- Import organization: stdlib, third-party, local (separated by blank lines)
- Descriptive variable names over comments

### Architecture Patterns
- **Daemon Pattern**: Background service via systemd user service
- **Worker Queue**: SummarizerWorker with queue-based processing for AI summarization
- **Cron-like Scheduling**: Summarization runs at fixed clock intervals (e.g., hh:00, hh:15, hh:30, hh:45)
- **Storage Layout**: `~/activity-tracker-data/` with subdirectories:
  - `screenshots/YYYY/MM/DD/{timestamp}_{hash}.webp`
  - `thumbnails/` (200px width for faster loading)
  - `reports/`
  - `activity.db` (SQLite database)
- **Configuration**: YAML-based via `config.py` ConfigManager

### Testing Strategy
- pytest test suite in `tests/` directory
- Test categories: capture, storage, dhash, integration
- Target 85% code coverage
- Run with: `pytest tests/ --cov=tracker --cov-report=html`
- Fixtures in `conftest.py`

### Git Workflow
- Main branch: `master`
- Always confirm before pushing to git
- Commit messages should be descriptive
- After code changes to daemon modules, restart service:
  ```bash
  systemctl --user restart activity-tracker
  ```

## Domain Context
- **Sessions**: Automatic session start/end based on AFK detection (keyboard/mouse monitoring via pynput)
- **Focus Events**: Real-time window focus tracking with duration, stored in `window_focus_events` table
- **Terminal Introspection**: Detects processes running inside terminal emulators (vim, ssh, tmux)
- **Threshold Summaries**: AI summaries triggered at configurable intervals (default: 15 minutes)
- **Focus-Weighted Sampling**: Screenshots sampled proportionally to app focus time for LLM context
- **Structured Summaries**: LLM returns SUMMARY, EXPLANATION, and CONFIDENCE fields

## Important Constraints
- **X11 Only**: Wayland not supported (requires xdotool + xrandr)
- **Local LLM Only**: Uses Ollama for privacy (no cloud APIs)
- **Single Active Monitor**: Captures only the monitor with focused window
- **AFK Detection**: Summarization skipped during AFK periods
- **Context Window Limits**: Daily summaries limited to prevent exceeding Ollama context

## External Dependencies
- **Ollama**: Local LLM server (default host: http://localhost:11434)
  - Models loaded dynamically from API
  - 1h keepalive for faster subsequent responses
- **xdotool**: Window focus and geometry detection
- **xrandr**: Monitor detection for multi-monitor support
- **xprop**: Window property queries
- **Tesseract**: OCR for text extraction (optional)
- **weasyprint**: PDF export for reports (optional)
