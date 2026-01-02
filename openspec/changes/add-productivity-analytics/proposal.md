# Change: Add Productivity Analytics Suite

## Why

Activity Tracker currently captures screenshots and generates AI summaries, but lacks higher-level productivity analytics that help users understand their work patterns. Users want to:

1. Know when they're in "flow state" - sustained focused work
2. Understand their context switching patterns and identify distractions
3. Compare productivity across different time periods
4. Receive actionable insights about their work habits
5. Get real-time feedback while working
6. Generate standup-ready summaries for team communication

This change transforms Activity Tracker from a passive recorder into an active productivity coach.

## What Changes

### Phase 1: Core Analytics Foundation
- **Flow State Detection**: Algorithm to detect and score sustained focus periods (20+ min, <3 switches/30min)
- **Context Switch Metrics**: Track app/window switches, categorize as productive/distraction/neutral
- **Comparative Analytics**: Side-by-side period comparison with trend indicators

### Phase 2: Real-Time Awareness
- **Distraction Detection**: Identify visits to distraction apps, alert during focus sessions
- **Real-Time Activity Widget**: SSE-powered live dashboard showing current activity and flow status

### Phase 3: Insights & Reporting
- **Insight Generation**: Pattern-based recommendations ("Your mornings are 2x more productive")
- **Standup Automation**: Yesterday/Today/Blockers format with project grouping

## Impact

### Affected Capabilities (New)
- `flow-detection` - Flow state detection and scoring
- `context-switches` - Context switch tracking and classification
- `comparative-analytics` - Period comparison and trends
- `distraction-detection` - Distraction app tracking and alerts
- `realtime-widget` - Live activity dashboard via SSE
- `insight-generation` - Pattern-based productivity insights
- `standup-automation` - Standup format generation

### Affected Code
- `tracker/storage.py` - New database tables (4 new tables)
- `tracker/window_watcher.py` - Hook for flow detector integration
- `tracker/config.py` - Configuration for distraction apps, thresholds
- `web/app.py` - New API endpoints and SSE stream
- `tracker/reports.py` - Standup format generation

### Database Schema Additions
- `flow_sessions` - Detected flow state periods with scores
- `context_switches` - Individual switch events with classification
- `distraction_events` - Distraction app visits
- `daily_analytics` - Cached daily metrics for fast querying

### New Files
- `tracker/flow_detector.py` - Flow detection algorithm
- `tracker/analytics.py` - Analytics engine for comparisons and aggregations
- `tracker/distractions.py` - Distraction detection logic
- `tracker/insights.py` - Pattern-based insight generation
- `web/static/js/realtime.js` - SSE client for live updates
- `web/templates/standup.html` - Standup generation UI

### API Endpoints (New)
```
GET /api/analytics/flow         - Flow sessions in range
GET /api/analytics/switches     - Context switch data
GET /api/analytics/compare      - Comparative analysis
GET /api/stream/activity        - SSE endpoint for live updates
GET /api/current                - Current activity snapshot
GET /api/insights               - Generated insights
GET /api/standup                - Standup format for date
POST /api/standup/copy          - Copy to clipboard format
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Performance impact from real-time tracking | SSE throttling, batch DB writes |
| Flow detection false positives | Configurable thresholds, user can adjust |
| Insight generation accuracy | Conservative language, confidence scores |
| Database growth from switch tracking | Daily aggregation, configurable retention |

## Success Criteria

1. Flow sessions detected with 0-100 score
2. Context switches counted and categorized per day
3. This week vs last week comparison available
4. Distraction apps configurable and tracked
5. Real-time widget shows current activity
6. Daily insights generated automatically
7. Standup copy-to-clipboard working
