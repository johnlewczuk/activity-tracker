# Capability: Flow Detection

Detects and scores "flow states" - periods of sustained, focused work with minimal context switching.

## ADDED Requirements

### Requirement: Flow State Detection

The system SHALL detect flow states based on focus patterns and track them as discrete sessions.

A flow state is defined as:
- 20+ minutes of sustained focus
- Less than 3 context switches per 30 minutes
- Same app family (related apps like VSCode tabs don't break flow)

#### Scenario: Flow session detected after 20 minutes

- **GIVEN** a user has been focused on VSCode for 25 minutes
- **AND** there have been 2 context switches within the window (e.g., switching tabs)
- **WHEN** the flow detector evaluates the focus pattern
- **THEN** a flow session SHALL be created with start_time = focus_start
- **AND** the flow score SHALL be calculated based on duration and switches

#### Scenario: Flow broken by too many switches

- **GIVEN** a user is in an active flow session
- **WHEN** the context switch count exceeds 3 in a 30-minute window
- **THEN** the flow session SHALL end
- **AND** break_reason SHALL be set to "Too many switches (N in 30min)"

#### Scenario: Flow broken by distraction app

- **GIVEN** a user is in an active flow session
- **WHEN** the user switches to an app marked as a distraction (e.g., Slack, Twitter)
- **THEN** the flow session SHALL end
- **AND** break_reason SHALL include the distraction app name

#### Scenario: Related app switching preserves flow

- **GIVEN** a user is focused on "code" (VSCode)
- **WHEN** the user switches to "gnome-terminal" (same work context)
- **THEN** the flow session SHALL continue
- **AND** this switch SHALL NOT count toward the switch limit

### Requirement: Flow Score Calculation

The system SHALL calculate a flow score (0-100) for each flow session based on duration and interruption count.

**Algorithm**:
- Base score: `min(50 + (duration_minutes - 20) * 0.75, 100)`
- Penalty: `min(context_switches * 5, 25)`
- Final: `max(0, base_score - penalty)`

#### Scenario: Perfect flow score

- **GIVEN** a flow session of 120 minutes
- **AND** 0 context switches
- **WHEN** the flow score is calculated
- **THEN** the score SHALL be 100

#### Scenario: Minimum flow score

- **GIVEN** a flow session of exactly 20 minutes
- **AND** 0 context switches
- **WHEN** the flow score is calculated
- **THEN** the score SHALL be 50

#### Scenario: Flow score with penalty

- **GIVEN** a flow session of 45 minutes
- **AND** 2 context switches
- **WHEN** the flow score is calculated
- **THEN** base score SHALL be 50 + (45-20) * 0.75 = 68.75
- **AND** penalty SHALL be 2 * 5 = 10
- **AND** final score SHALL be 59

### Requirement: Flow Session Persistence

The system SHALL persist completed flow sessions to the database.

#### Scenario: Flow session saved on end

- **GIVEN** a flow session ends (due to AFK, distraction, or excessive switches)
- **AND** the session duration >= 20 minutes
- **WHEN** the flow session is finalized
- **THEN** a record SHALL be inserted into `flow_sessions` table
- **AND** the record SHALL include: start_time, end_time, duration_minutes, flow_score, context_switches, primary_app, break_reason, session_id

#### Scenario: Short sessions not saved

- **GIVEN** a potential flow session that lasted only 15 minutes
- **WHEN** the session ends
- **THEN** no record SHALL be saved to the database

### Requirement: Flow Status API

The system SHALL provide a real-time API endpoint to check current flow status.

#### Scenario: User currently in flow

- **GIVEN** the user has been in flow for 35 minutes
- **WHEN** `GET /api/analytics/flow/current` is called
- **THEN** the response SHALL include:
  - `in_flow: true`
  - `duration_minutes: 35`
  - `flow_score: current_score`
  - `primary_app: "app_name"`
  - `context_switches: count`

#### Scenario: User building toward flow

- **GIVEN** the user has been focused for 12 minutes (not yet flow)
- **WHEN** `GET /api/analytics/flow/current` is called
- **THEN** the response SHALL include:
  - `in_flow: false`
  - `building_flow: true`
  - `potential_minutes: 12`
  - `minutes_to_flow: 8`

#### Scenario: User not in or building flow

- **GIVEN** the user just switched apps
- **WHEN** `GET /api/analytics/flow/current` is called
- **THEN** the response SHALL include:
  - `in_flow: false`
  - `building_flow: false`

### Requirement: Flow Session Query API

The system SHALL provide an API to query historical flow sessions.

#### Scenario: Query flow sessions by date range

- **GIVEN** 5 flow sessions exist between 2024-01-01 and 2024-01-07
- **WHEN** `GET /api/analytics/flow?start=2024-01-01&end=2024-01-07` is called
- **THEN** the response SHALL include all 5 flow sessions
- **AND** each session SHALL include: id, start_time, end_time, duration_minutes, flow_score, primary_app, break_reason

### Requirement: Daily Flow Statistics

The system SHALL provide aggregated flow statistics per day.

#### Scenario: Get daily flow summary

- **GIVEN** 3 flow sessions on 2024-01-15 totaling 180 minutes with average score 75
- **WHEN** `GET /api/analytics/flow/daily?date=2024-01-15` is called
- **THEN** the response SHALL include:
  - `flow_sessions: 3`
  - `flow_minutes: 180`
  - `avg_flow_score: 75`

## Database Schema

```sql
CREATE TABLE flow_sessions (
    id INTEGER PRIMARY KEY,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    duration_minutes REAL,
    flow_score INTEGER,
    context_switches INTEGER,
    primary_app TEXT,
    primary_window TEXT,
    break_reason TEXT,
    session_id INTEGER REFERENCES activity_sessions(id)
);

CREATE INDEX idx_flow_start ON flow_sessions(start_time);
CREATE INDEX idx_flow_session ON flow_sessions(session_id);
```

## Configuration

```yaml
flow_detection:
  enabled: true
  min_duration_minutes: 20
  max_switches_per_30min: 3
  related_app_grace: true  # Allow switching within app families
```

## Files

- `tracker/flow_detector.py` - FlowDetector class
- `tracker/storage.py` - Database table creation
- `web/app.py` - API endpoints
