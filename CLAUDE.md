<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

# Activity Tracker MVP

## Session Start Instructions
When starting a new session:
1. Run `git status`, `git diff`, `git log --oneline -10`
2. Review Decision Log below for context
3. Ask what to work on next

**IMPORTANT:** After every task, check if CLAUDE.md needs to be updated

## After Code Changes
Auto-restart daemon after modifying `tracker/*.py`:
```bash
systemctl --user restart activity-tracker
```

## Overview
Linux background service: captures screenshots at intervals, stores in SQLite, web viewer at localhost:55555.

**Tech**: Python 3.11+, mss, SQLite, Flask, WebP, pytest
**Storage**: ~/activity-tracker-data/ (screenshots/YYYY/MM/DD/, activity.db)
**Constraints**: X11 only (xdotool + xrandr), 30s default interval

## Key Modules
- `tracker/daemon.py` - Systemd service with AFK detection, window focus tracking
- `tracker/vision.py` - Two-stage LLM summarization (Ollama), focus-weighted sampling
- `tracker/summarizer_worker.py` - Cron-like scheduled summarization
- `tracker/window_watcher.py` - Real-time focus tracking with terminal introspection
- `tracker/reports.py` + `report_export.py` - Report generation (MD/HTML/PDF/JSON)
- `web/app.py` - Flask API + timeline/analytics/reports/settings UI

## Current Architecture (Phase 17)
- **Summarization**: Cron-scheduled (e.g., :00/:15/:30/:45), two-stage LLM calls, AFK-aware
- **Focus tracking**: Window focus events with terminal context, session-linked
- **Screenshot sampling**: Focus-weighted by (app, window_title) pairs, Hamilton allocation, min 3 samples
- **Summaries**: Structured output (SUMMARY/EXPLANATION/CONFIDENCE/TAGS), stored with prompt_text
- **Reports**: Synthesize from existing summaries + focus context
- **UI**: Shared CSS/JS, toast notifications, export history

## Decision Log Summary
| Phase | Date | Key Changes |
|-------|------|-------------|
| 1 | 2024-12-02 | Timeline + Analytics UI (Chart.js) |
| 2 | 2025-12-08 | AFK detection (pynput), sessions, window geometry, multi-monitor |
| 3 | 2025-12-09 | Threshold-based summarization, background worker |
| 4 | 2025-12-09 | Reports (timeparser, export formats) |
| 5 | 2025-12-09 | Window focus tracking, focus analytics |
| 6 | 2025-12-09 | Raw app/window data to LLM (no project detection) |
| 7 | 2025-12-10 | Simplified settings UI, thumbnails, quality presets |
| 8 | 2025-12-11 | Removed project detection, timezone fixes |
| 9 | 2025-12-12 | Focus context in reports, geometry parsing fix |
| 10 | 2025-12-15 | Cron-like scheduling, time-range summarization |
| 11 | 2025-12-15 | Terminal introspection (process tree, tmux/ssh) |
| 12 | 2025-12-16 | Structured output: explanation + confidence |
| 13 | 2025-12-20 | AFK-aware summarization, tmux introspection fix |
| 14 | 2025-12-24 | Hamilton allocation for focus-weighted sampling |
| 15 | 2025-12-29 | AFK edge cases: focus flush, session_id capture |
| 16 | 2025-12-30 | Two-stage summarization, tags, shared CSS/JS |
| 17 | 2026-01-01 | Window-level sampling (app+title pairs) |

## Key Implementation Details

### Focus-Weighted Sampling (vision.py)
- Groups by (app_name, window_title) not just app
- Hamilton allocation for fair distribution
- 5% min focus threshold, min 3 samples
- Falls back to uniform if no focus data

### LLM Prompt Guidance
- Pass raw app/window usage breakdown
- "Do NOT assume different apps/windows are related unless clearly same project"
- Focus on where most time was spent

### AFK Handling
- Focus events flush on AFK, session_id captured at focus start
- Summarization skips AFK time slots
- Daemon restart detection (>30s stale)

## Future Improvements
- Database normalization: unify summaries tables, separate prompts table

## Known Issues
- Wayland: assumes X11 (xdotool + xrandr)
- Permission handling: missing directory/file checks
- Error resilience: daemon needs better recovery

## Testing
```bash
pytest tests/ --cov=tracker --cov-report=html
```
