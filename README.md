# Activity Tracker

A Linux background service that automatically captures desktop screenshots at regular intervals, stores metadata in SQLite, and provides a rich web interface with timeline visualization and analytics for browsing your activity history.

## Features

- **Automated Screenshot Capture**: Captures desktop screenshots every 30 seconds
- **Perceptual Duplicate Detection**: Uses dhash algorithm to skip near-identical screenshots
- **Window Context Extraction**: Records active window title and application name
- **Session-Based Tracking**: Automatically detects AFK periods to group activity into sessions
- **AFK Detection**: Uses pynput to monitor keyboard/mouse activity (configurable timeout)
- **Timeline View**: Interactive calendar heatmap with session-based activity breakdown
- **Analytics Dashboard**: Comprehensive charts showing activity patterns and trends
- **Web-based Viewer**: Rich Flask interface with multiple views for browsing and analysis
- **Efficient Storage**: WebP compression and organized directory structure
- **Systemd Integration**: Runs as user service with automatic restart
- **X11 Support**: Optimized for X11 display server (Wayland support planned)
- **AI Activity Summaries**: Vision LLM-powered session summaries with OCR grounding
- **Smart Session Resume**: Resumes previous session on restart if within AFK timeout

## Architecture

### Components

1. **Screenshot Capture (`tracker/capture.py`)**
   - MSS library for fast cross-platform screen capture
   - Perceptual hashing (dhash) for duplicate detection
   - WebP compression with 80% quality
   - Organized filesystem storage (YYYY/MM/DD structure)

2. **Database Storage (`tracker/storage.py`)**
   - SQLite database for metadata storage
   - Indexed queries for time-range and hash-based lookups
   - Context manager for safe database operations

3. **Background Daemon (`tracker/daemon.py`)**
   - Main service process with 30-second intervals
   - Signal handling for graceful shutdown
   - Window information extraction via xdotool
   - Duplicate detection and storage optimization
   - Session management with AFK detection
   - Smart session resume on restart

4. **AFK Detection (`tracker/afk.py`)**
   - Monitors keyboard and mouse activity via pynput
   - Configurable timeout (default 3 minutes)
   - Fires callbacks on AFK/active state transitions
   - Auto-installs missing dependencies

5. **Session Manager (`tracker/sessions.py`)**
   - Tracks continuous activity periods
   - Links screenshots to sessions
   - Handles session start/end with metadata
   - Minimum session duration filtering

6. **Web Viewer (`web/app.py`)**
   - Flask application with multiple views (timeline, analytics, day view)
   - Interactive timeline with calendar heatmap and session breakdown
   - Analytics dashboard with charts and statistics
   - Date-based navigation
   - REST API for programmatic access

7. **Analytics Engine (`tracker/analytics.py`)**
   - Activity pattern analysis and statistics
   - Calendar heatmap data generation
   - Hourly/daily/weekly aggregations
   - Application usage tracking

8. **Vision Summarizer (`tracker/vision.py`)**
   - Hybrid OCR + vision LLM for activity understanding
   - Tesseract OCR for text extraction from screenshots
   - Ollama integration with configurable vision models
   - Session-based summary generation with context continuity
   - Returns full API request details for debugging

### Data Storage Structure

```
~/activity-tracker-data/
├── screenshots/
│   └── YYYY/
│       └── MM/
│           └── DD/
│               └── YYYYMMDD_HHMMSS_hash.webp
├── activity.db (SQLite database)
└── logs/
    └── daemon.log
```

### Database Schema

```sql
CREATE TABLE screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,           -- Unix timestamp
    filepath TEXT NOT NULL,              -- Relative path to image file
    dhash TEXT NOT NULL,                 -- Perceptual hash (16-char hex)
    window_title TEXT,                   -- Active window title
    app_name TEXT                        -- Application class name
);

-- Indexes for performance
CREATE INDEX idx_timestamp ON screenshots(timestamp);
CREATE INDEX idx_dhash ON screenshots(dhash);

-- Activity summaries table (hourly - legacy)
CREATE TABLE activity_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                   -- YYYY-MM-DD
    hour INTEGER NOT NULL,                -- 0-23
    summary TEXT NOT NULL,                -- LLM-generated summary
    screenshot_ids TEXT NOT NULL,         -- JSON array of screenshot IDs
    model_used TEXT NOT NULL,             -- e.g., "gemma3:27b-it-qat"
    inference_time_ms INTEGER NOT NULL,   -- Processing time
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, hour)
);

-- Activity sessions table (continuous periods of user activity)
CREATE TABLE activity_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TIMESTAMP NOT NULL,        -- Session start
    end_time TIMESTAMP,                   -- NULL if ongoing
    duration_seconds INTEGER,             -- Calculated on session end
    summary TEXT,                         -- LLM-generated summary
    screenshot_count INTEGER DEFAULT 0,
    unique_windows INTEGER DEFAULT 0,
    model_used TEXT,
    inference_time_ms INTEGER,
    prompt_text TEXT,                     -- Full API request for debugging
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Session screenshots junction table
CREATE TABLE session_screenshots (
    session_id INTEGER REFERENCES activity_sessions(id),
    screenshot_id INTEGER REFERENCES screenshots(id),
    PRIMARY KEY (session_id, screenshot_id)
);

-- Session OCR cache (per unique window title)
CREATE TABLE session_ocr_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES activity_sessions(id),
    window_title TEXT NOT NULL,
    ocr_text TEXT,
    screenshot_id INTEGER,
    UNIQUE(session_id, window_title)
);

-- Threshold-based summaries (auto-generated every N screenshots)
CREATE TABLE threshold_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,               -- ISO timestamp of first screenshot
    end_time TEXT NOT NULL,                 -- ISO timestamp of last screenshot
    summary TEXT NOT NULL,                  -- LLM-generated summary
    screenshot_ids TEXT NOT NULL,           -- JSON array of screenshot IDs
    model_used TEXT NOT NULL,               -- e.g., "gemma3:14b-it-qat"
    config_snapshot TEXT,                   -- JSON snapshot of summarization config
    inference_time_ms INTEGER,
    regenerated_from INTEGER,               -- Links to original if this is a regeneration
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Installation

### Prerequisites

- **Python 3.11+** with pip
- **X11 display server** (standard on most Linux desktops)
- **xdotool** for window information extraction
- **systemd** for service management (optional but recommended)

```bash
# Ubuntu/Debian
sudo apt install python3 python3-pip python3-venv xdotool

# Fedora/RHEL
sudo dnf install python3 python3-pip xdotool

# Arch Linux
sudo pacman -S python python-pip xdotool
```

### Setup

1. **Clone and setup virtual environment:**

```bash
git clone <repository-url>
cd activity-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. **Install as systemd service:**

```bash
# Run the installation script (automatically enables and starts the service)
./scripts/install.sh

# The script automatically enables:
# - Web interface at http://127.0.0.1:55555
# - Auto-summarization (triggers every 10 screenshots)
```

3. **Verify installation:**

```bash
# Check service status
systemctl --user status activity-tracker

# View live logs
journalctl --user -u activity-tracker -f
```

### AI Summarization (Optional)

The activity tracker can generate AI-powered summaries of your work using a local vision LLM running in Docker. This requires additional setup:

**Hardware Requirements:**
- **gemma3:27b-it-qat** (recommended): ~18GB VRAM (RTX 3090, 4090, A6000)
- **gemma3:14b-it-qat** (alternative): ~8GB VRAM (RTX 3080, 4070)
- **gemma3:4b-it-qat** (lightweight): ~3GB VRAM (any modern GPU)

**Software Dependencies:**

```bash
# Install Tesseract OCR
sudo apt install tesseract-ocr

# Start Ollama Docker container (with GPU support)
docker run -d --gpus=all \
  -v ollama:/root/.ollama \
  -p 11434:11434 \
  --name ollama \
  ollama/ollama

# Pull the vision model (choose based on your VRAM)
docker exec ollama ollama pull gemma3:27b-it-qat    # ~18GB VRAM
# OR
docker exec ollama ollama pull gemma3:14b-it-qat    # ~8GB VRAM
```

**Managing the Ollama Container:**

```bash
# Stop the container
docker stop ollama

# Start the container
docker start ollama

# View container logs
docker logs ollama

# Check available models
docker exec ollama ollama list
```

**Remote Ollama Server:**

Configure the Ollama host in the Settings page (`http://127.0.0.1:55555/settings`) or edit `~/.config/activity-tracker/config.yaml`:

```yaml
summarization:
  ollama_host: http://gpu-server:11434
```

**Threshold-Based Summarization:**

Auto-summarization is enabled by default. Summaries are automatically generated after every N screenshots (default: 10). Configure this in the Settings page:
- **Trigger Threshold**: Number of screenshots before generating a summary
- **Model**: Select from available Ollama models (auto-detected)
- **Include Previous Summary**: Use context from last summary for continuity

## Usage

### Running the Service

The service runs automatically after installation via systemd:

```bash
# Start the service
systemctl --user start activity-tracker

# Stop the service
systemctl --user stop activity-tracker

# Restart the service
systemctl --user restart activity-tracker

# View status and recent logs
systemctl --user status activity-tracker
```

### Web Interface

The web interface can run in two modes:

**Option 1: Integrated Mode (Recommended)**
If you enabled the web interface during installation, it runs automatically with the daemon:
```bash
# Web server is already running with the systemd service
# Just open your browser
firefox http://localhost:55555
```

**Option 2: Standalone Mode**
Run the web interface separately (if you didn't enable it during install):
```bash
# Start the web interface
cd activity-tracker
source venv/bin/activate
python web/app.py

# Open in browser (runs on port 55555)
firefox http://localhost:55555
```

### AI Summarization

Generate activity summaries from your screenshots:

```bash
# Summarize today's unsummarized hours
python scripts/summarize_activity.py

# Summarize a specific date
python scripts/summarize_activity.py --date 2025-12-01

# Backfill last 7 days
python scripts/summarize_activity.py --backfill 7

# Summarize only a specific hour
python scripts/summarize_activity.py --hour 14

# Re-generate existing summaries
python scripts/summarize_activity.py --force

# Preview what would be processed
python scripts/summarize_activity.py --dry-run

# Use a different model (for lower VRAM)
python scripts/summarize_activity.py --model gemma3:14b-it-qat
```

**How It Works:**
1. Samples 4-6 screenshots evenly from each hour
2. Extracts OCR text from the middle screenshot for context
3. Sends screenshots + OCR to vision LLM with summarization prompt
4. Stores results in database with timing info

**Web Interface:**
- Timeline view shows ✨ badges on hours with summaries
- Click "Generate" on any hour to create a summary
- "Generate All" processes all unsummarized hours
- Daily Summary section shows concatenated hourly summaries
- Analytics dashboard displays recent activity summaries

#### Available Views

1. **Timeline View** (`http://localhost:55555/timeline`) - Default homepage
   - Interactive calendar heatmap showing daily activity intensity
   - Click any day to see hourly breakdown
   - Click hourly bars to view screenshots from that hour
   - Keyboard navigation: Arrow keys for day navigation, H/L for month navigation

2. **Analytics Dashboard** (`http://localhost:55555/analytics`)
   - Summary statistics (total screenshots, daily average, most active periods)
   - Daily activity bar chart with weekend highlighting
   - Top applications pie chart and detailed usage table
   - Hourly activity heatmap (7 days × 24 hours)
   - 30-day activity trend line chart
   - Date range selector (last 7 days, last 30 days, this month)

3. **Day View** (`http://localhost:55555/day/YYYY-MM-DD`)
   - View all screenshots from a specific date
   - Navigate between days using arrow buttons
   - Shows window title and application for each screenshot

### Manual Operation

For testing or development:

```bash
# Activate virtual environment
source venv/bin/activate

# Run daemon manually (Ctrl+C to stop)
python -m tracker.daemon

# Capture a single screenshot
python -c "from tracker.capture import ScreenCapture; c = ScreenCapture(); print(c.capture_screen())"

# Query database
python -c "from tracker.storage import ActivityStorage; s = ActivityStorage(); print(len(s.get_screenshots(0, 2147483647)))"
```

### Configuration

Configuration is managed via YAML file at `~/.config/activity-tracker/config.yaml` or through the web Settings page at `http://127.0.0.1:55555/settings`.

**Default Settings:**

- **Capture Interval**: 30 seconds (configurable via Settings)
- **Image Format**: WebP with 80% quality
- **Duplicate Threshold**: 3 bits Hamming distance
- **Storage Location**: `~/activity-tracker-data/`
- **AFK Timeout**: 180 seconds (3 minutes)
- **Trigger Threshold**: 10 screenshots before auto-summarization

**Example config.yaml:**
```yaml
capture:
  interval_seconds: 30
  format: webp
  quality: 80
afk:
  timeout_seconds: 180
  min_session_minutes: 5
summarization:
  enabled: true
  model: gemma3:14b-it-qat
  ollama_host: http://localhost:11434
  trigger_threshold: 10
  include_previous_summary: true
```

## API Reference

### Python API

```python
from tracker import ScreenCapture, ActivityStorage, ActivityDaemon

# Capture screenshots
capture = ScreenCapture()
filepath, dhash = capture.capture_screen()
similar = capture.are_similar(hash1, hash2, threshold=10)

# Database operations  
storage = ActivityStorage()
screenshot_id = storage.save_screenshot(filepath, dhash, "Firefox", "firefox")
screenshots = storage.get_screenshots(start_timestamp, end_timestamp)

# Run daemon
daemon = ActivityDaemon()
daemon.run()  # Blocks until interrupted
```

### REST API

The web interface provides a comprehensive REST API:

#### Core Endpoints

**Get screenshots in time range:**
```bash
curl "http://localhost:55555/api/screenshots?start=1637000000&end=1637100000"
```

**Get calendar heatmap data:**
```bash
curl "http://localhost:55555/api/calendar/2024/12"
```

**Get daily summary statistics:**
```bash
curl "http://localhost:55555/api/day/2024-12-03/summary"
```

**Get hourly breakdown for a day:**
```bash
curl "http://localhost:55555/api/day/2024-12-03/hourly"
```

**Get screenshots for specific hour:**
```bash
curl "http://localhost:55555/api/screenshots/2024-12-03/14"
```

**Get weekly statistics:**
```bash
curl "http://localhost:55555/api/week/2024-12-03"
```

#### Summarization Endpoints

**Get summaries for a date:**
```bash
curl "http://localhost:55555/api/summaries/2024-12-03"
# Returns: { "date": "...", "summaries": [{"hour": 9, "summary": "...", "screenshot_count": 6}, ...] }
```

**Get summary coverage stats:**
```bash
curl "http://localhost:55555/api/summaries/coverage"
# Returns: { "total_days": 14, "summarized_hours": 89, "total_hours": 120, "coverage_pct": 74.2 }
```

**Generate summaries (background):**
```bash
curl -X POST "http://localhost:55555/api/summaries/generate" \
  -H "Content-Type: application/json" \
  -d '{"date": "2024-12-03", "hours": [9, 10, 11]}'
# Returns: { "status": "started", "hours_queued": 3 }
```

**Check generation status:**
```bash
curl "http://localhost:55555/api/summaries/generate/status"
# Returns: { "running": true, "current_hour": 10, "completed": 1, "total": 3 }
```

#### Session Endpoints

**Get sessions for a date:**
```bash
curl "http://localhost:55555/api/sessions/2024-12-03"
# Returns: { "date": "...", "sessions": [...], "total_active_minutes": 420, "session_count": 4 }
```

**Get screenshots for a session:**
```bash
curl "http://localhost:55555/api/sessions/42/screenshots?page=1&per_page=50"
# Returns: { "session_id": 42, "screenshots": [...], "total": 300 }
```

**Get current active session:**
```bash
curl "http://localhost:55555/api/sessions/current"
# Returns: { "session": {...} or null, "is_afk": true/false }
```

**Summarize a session:**
```bash
curl -X POST "http://localhost:55555/api/sessions/42/summarize"
# Returns: { "status": "started" }
```

See [API_ENDPOINTS.md](API_ENDPOINTS.md) for complete documentation.

## Development

### Project Structure

```
activity-tracker/
├── tracker/                    # Core library
│   ├── __init__.py            # Package exports and metadata
│   ├── capture.py             # Screenshot capture and dhash
│   ├── storage.py             # SQLite database interface
│   ├── daemon.py              # Background service process
│   ├── analytics.py           # Activity analytics and statistics
│   ├── vision.py              # AI summarization (OCR + LLM)
│   ├── afk.py                 # AFK detection via pynput
│   ├── sessions.py            # Session management
│   └── config.py              # Configuration settings
├── web/                       # Web interface
│   ├── app.py                 # Flask application with REST API
│   └── templates/             # HTML templates
│       ├── base.html          # Base template with navigation
│       ├── timeline.html      # Calendar heatmap view (with summaries)
│       ├── analytics.html     # Analytics dashboard
│       └── day.html           # Daily screenshot view
├── scripts/                   # Installation and utilities
│   ├── install.sh             # Systemd service setup
│   ├── uninstall.sh           # Service removal
│   └── summarize_activity.py  # CLI for generating summaries
├── tests/                     # Test suite
│   ├── conftest.py            # Pytest fixtures
│   ├── test_capture.py        # Capture tests
│   ├── test_storage.py        # Storage tests
│   └── test_dhash.py          # Hash comparison tests
├── requirements.txt           # Python dependencies
├── CLAUDE.md                  # Project documentation
└── README.md                  # This file
```

### Running Tests

```bash
# Activate virtual environment
source venv/bin/activate

# Test screenshot capture
python -c "from tracker.capture import ScreenCapture; c = ScreenCapture(); print('Screenshot test:', c.capture_screen()[0])"

# Test database operations
python -c "from tracker.storage import ActivityStorage; s = ActivityStorage(); print('Database test: OK')"

# Test duplicate detection
python -c "
from tracker.capture import ScreenCapture
c = ScreenCapture()
# Same image should have distance 0
h1 = c._generate_dhash(c.capture_screen()[0])
h2 = c._generate_dhash(c.capture_screen()[0]) 
print('Hash test:', c.compare_hashes(h1, h2) == 0)
"
```

### Contributing

1. **Code Style**: Follow PEP 8 Python style guidelines
2. **Documentation**: Add comprehensive docstrings for new functions
3. **Error Handling**: Use appropriate exception types and logging
4. **Testing**: Test new features manually before submission

## Troubleshooting

### Common Issues

**Service won't start:**
```bash
# Check logs for errors
journalctl --user -u activity-tracker --no-pager

# Common causes:
# - X11 not available (check DISPLAY variable)
# - Permission denied (check data directory permissions)  
# - Python dependencies missing (reinstall requirements.txt)
```

**Screenshots not captured:**
```bash
# Test manual capture
source venv/bin/activate
python -c "from tracker.capture import ScreenCapture; print(ScreenCapture().capture_screen())"

# Check X11 connection
echo $DISPLAY
xdpyinfo | head -5
```

**Web interface not accessible:**
```bash
# Check if Flask is running
ps aux | grep python.*app.py

# Verify database exists
ls -la ~/activity-tracker-data/activity.db

# Check Flask logs for errors
python web/app.py
```

**Permission errors:**
```bash
# Check data directory permissions
ls -ld ~/activity-tracker-data

# Fix permissions if needed
chmod 755 ~/activity-tracker-data
chmod 644 ~/activity-tracker-data/activity.db
```

### Performance Optimization

- **Storage usage**: Monitor `~/activity-tracker-data/screenshots/` size
- **Database performance**: SQLite handles thousands of records efficiently
- **Memory usage**: Daemon typically uses 50-100MB RAM
- **CPU usage**: Minimal impact with 30-second intervals

## Roadmap

### Completed ✓
- [x] **Activity Analytics**: Usage patterns and application time tracking
- [x] **Timeline View**: Calendar heatmap with hourly breakdown
- [x] **Charts & Visualization**: Interactive charts using Chart.js
- [x] **Comprehensive Test Suite**: Pytest-based testing with 85% coverage
- [x] **AI Summarization**: Vision LLM-powered activity summaries with OCR grounding
- [x] **Auto-Summarization**: Threshold-based background summarization (every N screenshots)
- [x] **Session-Based Tracking**: AFK detection with pynput, session management
- [x] **Smart Session Resume**: Resume previous session on restart if within timeout
- [x] **Summary Debugging**: View exact API requests sent to Ollama
- [x] **Configuration File**: YAML-based settings with web UI (Settings page)
- [x] **Multi-monitor Support**: Captures only active monitor, stores monitor metadata
- [x] **Summary Regeneration**: Regenerate summaries with different models/settings

### Planned
- [ ] **Wayland Support**: Add sway/wlroots integration for window information
- [ ] **Privacy Filters**: Blur sensitive areas or skip certain applications
- [ ] **Export Features**: Generate reports and data exports (CSV/JSON/PDF)
- [ ] **Search & Tagging**: Search screenshots by window title, add custom tags
- [ ] **Daily Rollup Summaries**: Consolidate threshold summaries into daily digests

## License

MIT License - see LICENSE file for details.

## Support

For issues and feature requests, please use the project's issue tracker.

---

**Note**: This software captures your screen continuously. Ensure compliance with your organization's security policies and applicable privacy laws. Screenshots may contain sensitive information - secure your data directory appropriately.