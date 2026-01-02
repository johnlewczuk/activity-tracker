# Technical Design: Productivity Analytics Suite

## Context

Activity Tracker captures window focus events via `WindowWatcher` but doesn't analyze patterns beyond simple duration tracking. This design adds analytics layers that process focus events in real-time to detect flow states, categorize switches, and generate insights.

### Stakeholders
- End users wanting productivity insights
- Developers maintaining the codebase

### Constraints
- Must integrate with existing `WindowWatcher` without breaking changes
- Real-time features must not block the daemon's capture loop
- Database writes must be efficient (batch when possible)
- SSE must work with Flask's synchronous nature

## Goals / Non-Goals

### Goals
- Detect flow states from focus event patterns
- Track and categorize context switches
- Enable period-to-period comparison
- Provide real-time activity visibility
- Generate actionable insights
- Automate standup generation

### Non-Goals
- Predictive analytics ("you'll be in flow at 9am tomorrow")
- Team/multi-user features (this is single-user)
- Cloud sync or external integrations
- Desktop notifications (future scope)

## Decisions

### D1: Flow Detection Architecture

**Decision**: Create `FlowDetector` class that hooks into `WindowWatcher.on_focus_change` callback.

**Rationale**:
- WindowWatcher already fires callbacks on focus change
- Adding another callback is non-breaking
- Flow detection is stateful (tracks current flow session)
- Keeps flow logic isolated from window tracking

**Implementation**:
```python
# In daemon.py
flow_detector = FlowDetector(storage=storage)
window_watcher = WindowWatcher(
    on_focus_change=lambda old, new: [
        save_focus_event(old, new),
        flow_detector.on_focus_change(old, new)  # New hook
    ]
)
```

### D2: Flow State Criteria

**Decision**: Flow requires:
- Same app/project (with related-app grace period)
- <3 context switches per 30 minutes
- 20+ minutes of uninterrupted focus

**Rationale**:
- Based on research on deep work (Cal Newport)
- 20 minutes is minimum for meaningful focus
- 3 switches allows for brief interruptions (checking a reference)
- Related-app switching (VSCode tabs, terminal windows) shouldn't break flow

**Flow Score Algorithm**:
```
Base score = min(50 + (duration_minutes - 20) * 0.75, 100)
Switch penalty = min(context_switches * 5, 25)
Final score = max(0, base_score - switch_penalty)
```

Example scores:
- 20 min, 0 switches = 50
- 45 min, 1 switch = 69
- 60 min, 0 switches = 80
- 120 min, 2 switches = 90

### D3: Context Switch Classification

**Decision**: Classify switches as:
- `productive` - Returning from distraction to work
- `distraction` - Switching to a distraction app
- `neutral` - Work-to-work switching

**Rationale**:
- Simple trichotomy covers 90% of cases
- Distraction apps configurable by user
- Allows tracking "distraction time" separately

**Distraction App Detection**:
```python
distraction_apps = {'slack', 'discord', 'twitter', 'youtube', 'reddit'}
# Configurable in config.yaml
```

### D4: Database Schema Design

**Decision**: Four new tables with daily aggregation cache.

**Rationale**:
- Raw events in `context_switches` for detailed analysis
- Flow sessions as first-class entities for querying
- Daily cache in `daily_analytics` for fast dashboard loading
- Separate `distraction_events` for focused distraction reporting

**Schema**:
```sql
-- Flow sessions: detected focus periods
CREATE TABLE flow_sessions (
    id INTEGER PRIMARY KEY,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    duration_minutes REAL,
    flow_score INTEGER,  -- 0-100
    context_switches INTEGER,
    primary_app TEXT,
    primary_window TEXT,
    break_reason TEXT,
    session_id INTEGER REFERENCES activity_sessions(id)
);

-- Context switches: every app/window change
CREATE TABLE context_switches (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    from_app TEXT,
    from_window TEXT,
    to_app TEXT,
    to_window TEXT,
    switch_type TEXT,  -- 'productive', 'distraction', 'neutral'
    time_in_previous REAL,
    session_id INTEGER REFERENCES activity_sessions(id)
);

-- Distraction events: visits to distraction apps
CREATE TABLE distraction_events (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    app_name TEXT,
    window_title TEXT,
    during_flow BOOLEAN,
    duration_seconds REAL,
    session_id INTEGER REFERENCES activity_sessions(id)
);

-- Daily analytics: pre-computed for fast querying
CREATE TABLE daily_analytics (
    id INTEGER PRIMARY KEY,
    date DATE UNIQUE,
    total_tracked_minutes REAL,
    flow_minutes REAL,
    context_switches INTEGER,
    distraction_minutes REAL,
    top_apps JSON,
    computed_at TIMESTAMP
);

CREATE INDEX idx_flow_start ON flow_sessions(start_time);
CREATE INDEX idx_switch_time ON context_switches(timestamp);
CREATE INDEX idx_distraction_time ON distraction_events(timestamp);
```

### D5: Real-Time Widget via SSE

**Decision**: Use Server-Sent Events (SSE) for live updates, not WebSockets.

**Rationale**:
- SSE is simpler than WebSockets
- Flask supports SSE natively with generators
- Unidirectional (server→client) is sufficient
- Automatic reconnection in browsers

**Implementation**:
```python
@app.route('/api/stream/activity')
def stream_activity():
    def generate():
        while True:
            data = get_current_activity()
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(1)  # 1 second updates
    return Response(generate(), mimetype='text/event-stream')
```

**Throttling**:
- 1 second update interval
- Only send if data changed (hash comparison)
- Client reconnect with exponential backoff

### D6: Comparative Analytics Engine

**Decision**: Simple period-to-period comparison with % change indicators.

**Rationale**:
- "This week vs last week" is most common use case
- % change is universally understood
- Rolling baseline (4-week average) provides context

**Comparison Metrics**:
```python
{
    'flow_minutes': {'current': 240, 'previous': 180, 'change': +33.3},
    'context_switches': {'current': 45, 'previous': 60, 'change': -25.0},
    'distraction_minutes': {'current': 30, 'previous': 45, 'change': -33.3},
    'flow_score_avg': {'current': 72, 'previous': 65, 'change': +10.8}
}
```

### D7: Insight Generation Strategy

**Decision**: Rule-based insights with configurable thresholds, not LLM-generated.

**Rationale**:
- LLM calls are expensive and slow
- Rule-based is deterministic and debuggable
- Insights are pattern-based, not creative
- Can add LLM layer later for elaboration

**Insight Rules**:
```python
insights = [
    {
        'condition': 'morning_flow > afternoon_flow * 1.5',
        'template': 'Your mornings are {ratio:.1f}x more productive than afternoons.',
        'category': 'time_of_day'
    },
    {
        'condition': 'context_switches > baseline * 1.5',
        'template': 'You had {percent:.0f}% more context switches than usual ({count} vs {baseline}).',
        'category': 'context_switching'
    },
    # ... more rules
]
```

### D8: Standup Generation Format

**Decision**: Template-based generation from existing summaries, not new LLM calls.

**Rationale**:
- Threshold summaries already exist for yesterday
- Just need to reformat, not re-summarize
- Faster response (no LLM latency)
- User can edit before sharing

**Format**:
```markdown
## Yesterday
- Worked on activity-tracker flow detection (2h 15m)
- Reviewed PRs on auth-service (45m)
- Design review meeting (1h)

## Today
[User fills in]

## Blockers
[User fills in]
```

## Risks / Trade-offs

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| SSE blocking Flask | Medium | High | Use background thread for data, generator just reads |
| High DB write volume from switches | Medium | Medium | Batch writes every 10 switches or 30 seconds |
| Flow detection false negatives | Medium | Low | Tune thresholds, add config options |
| Insight rules feel generic | Medium | Low | Start simple, iterate based on feedback |

## Migration Plan

1. **Phase 1**: Add database tables (non-breaking, additive)
2. **Phase 2**: Add flow detector hook to daemon
3. **Phase 3**: Add SSE endpoint (new route)
4. **Phase 4**: Add UI components
5. **Rollback**: Disable flow detector hook, tables remain but unused

No data migration required - new tables only.

## Open Questions

1. **Q**: Should distraction detection send desktop notifications?
   **A**: Defer to future - requires additional dependencies (notify2/libnotify)

2. **Q**: How long to retain context switch data?
   **A**: Default 90 days (same as screenshots), configurable

3. **Q**: Should insights be cached or computed on-demand?
   **A**: Compute daily, cache results in `daily_analytics`
