# Capability: Distraction Detection

Identifies visits to distraction apps, tracks time spent, and provides awareness during focus sessions.

## ADDED Requirements

### Requirement: Distraction Event Recording

The system SHALL record each visit to a distraction app as a discrete event.

#### Scenario: Distraction visit recorded

- **GIVEN** user switches to Slack (a distraction app)
- **AND** spends 180 seconds in Slack
- **WHEN** they switch away from Slack
- **THEN** a distraction event SHALL be recorded with:
  - `timestamp`: time of switch to Slack
  - `app_name`: "slack"
  - `window_title`: the Slack window title
  - `duration_seconds`: 180
  - `during_flow`: true/false (was user in flow when distracted?)

#### Scenario: Multiple distraction visits aggregated

- **GIVEN** user visits Slack 5 times in an hour
- **WHEN** distraction events are queried
- **THEN** all 5 visits SHALL be recorded separately
- **AND** aggregated stats SHALL show: total_time, visit_count

### Requirement: Flow Interruption Tracking

The system SHALL track when distractions interrupt an active flow state.

#### Scenario: Distraction breaks flow

- **GIVEN** user is in an active flow session
- **WHEN** they switch to Twitter
- **THEN** the distraction event SHALL have `during_flow: true`
- **AND** this SHALL be highlighted in distraction reports

#### Scenario: Distraction outside flow

- **GIVEN** user is not in flow (just switched between apps)
- **WHEN** they check Slack
- **THEN** the distraction event SHALL have `during_flow: false`

### Requirement: Distraction Time Aggregation

The system SHALL provide aggregated distraction statistics.

#### Scenario: Daily distraction summary

- **GIVEN** multiple distraction visits throughout the day
- **WHEN** `GET /api/analytics/distractions?date=today` is called
- **THEN** the response SHALL include:
  - `total_distraction_minutes`: sum of all distraction durations
  - `distraction_count`: number of distraction visits
  - `flow_interruptions`: distractions that broke flow
  - `top_distractions`: apps ranked by time spent

#### Scenario: Per-app distraction breakdown

- **GIVEN** distractions: Slack (45min), Twitter (20min), YouTube (30min)
- **WHEN** distraction breakdown is requested
- **THEN** each app SHALL show:
  - app_name, total_minutes, visit_count, avg_duration

### Requirement: Distraction Patterns Analysis

The system SHALL identify patterns in distraction behavior.

#### Scenario: Peak distraction hours

- **GIVEN** distraction events throughout the day
- **WHEN** pattern analysis runs
- **THEN** it SHALL identify hours with highest distraction rate
- **AND** compare to work activity in those hours

#### Scenario: Distraction triggers

- **GIVEN** historical data on what apps precede distractions
- **WHEN** trigger analysis runs
- **THEN** it SHALL identify common preceding contexts
- **AND** report: "You often check Slack after email" (if pattern exists)

### Requirement: Distraction Warning Threshold

The system SHALL provide awareness when distraction threshold is exceeded.

#### Scenario: Threshold exceeded warning

- **GIVEN** user has configured distraction_warning_threshold: 30 (minutes per day)
- **AND** they have accumulated 35 minutes of distraction time
- **WHEN** distraction status is checked
- **THEN** the status SHALL indicate threshold exceeded
- **AND** include: current_minutes, threshold, exceeded_by

#### Scenario: Threshold not exceeded

- **GIVEN** distraction_warning_threshold: 60
- **AND** current distraction time: 25 minutes
- **WHEN** distraction status is checked
- **THEN** status SHALL show: "Within threshold (25/60 minutes)"

### Requirement: Distraction API Endpoints

The system SHALL provide API endpoints for distraction data.

#### Scenario: Query distraction events

- **GIVEN** distraction events exist for a date range
- **WHEN** `GET /api/analytics/distractions?start=&end=` is called
- **THEN** all matching events SHALL be returned
- **AND** support filtering by app_name and during_flow

#### Scenario: Get distraction summary

- **GIVEN** a day with distraction activity
- **WHEN** `GET /api/analytics/distractions/summary?date=` is called
- **THEN** aggregated statistics SHALL be returned
- **AND** include comparison to baseline

## Database Schema

```sql
CREATE TABLE distraction_events (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    app_name TEXT NOT NULL,
    window_title TEXT,
    during_flow BOOLEAN DEFAULT FALSE,
    duration_seconds REAL,
    session_id INTEGER REFERENCES activity_sessions(id)
);

CREATE INDEX idx_distraction_time ON distraction_events(timestamp);
CREATE INDEX idx_distraction_app ON distraction_events(app_name);
```

## Configuration

```yaml
distraction_detection:
  enabled: true
  warning_threshold_minutes: 60  # Warn when exceeded
  apps:
    - slack
    - discord
    - teams
    - twitter
    - facebook
    - reddit
    - instagram
    - youtube
    - tiktok
```

## Files

- `tracker/distractions.py` - DistractionDetector class
- `tracker/flow_detector.py` - Integration with flow state
- `tracker/storage.py` - Database table
- `web/app.py` - API endpoints
