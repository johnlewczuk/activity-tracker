# Capability: Context Switch Analytics

Tracks and classifies every context switch (window/app change) to help users understand their interruption patterns.

## ADDED Requirements

### Requirement: Context Switch Recording

The system SHALL record every context switch event with source and destination context.

#### Scenario: Normal context switch recorded

- **GIVEN** the user is focused on "VSCode - main.py" for 300 seconds
- **WHEN** they switch to "Firefox - GitHub"
- **THEN** a context switch event SHALL be recorded with:
  - `timestamp`: current time
  - `from_app`: "code"
  - `from_window`: "VSCode - main.py"
  - `to_app`: "firefox"
  - `to_window`: "Firefox - GitHub"
  - `time_in_previous`: 300

#### Scenario: Rapid switches recorded

- **GIVEN** the user switches apps 5 times in 30 seconds
- **WHEN** the focus changes are processed
- **THEN** all 5 context switches SHALL be recorded
- **AND** each shall have accurate `time_in_previous` values

### Requirement: Context Switch Classification

The system SHALL classify each context switch as `productive`, `distraction`, or `neutral`.

#### Scenario: Switch to distraction app

- **GIVEN** the user is in VSCode
- **WHEN** they switch to "slack" (a configured distraction app)
- **THEN** the switch SHALL be classified as "distraction"

#### Scenario: Return from distraction

- **GIVEN** the user is in Slack (distraction app)
- **WHEN** they switch back to VSCode (work app)
- **THEN** the switch SHALL be classified as "productive"

#### Scenario: Work to work switch

- **GIVEN** the user is in VSCode
- **WHEN** they switch to Firefox (not a distraction app)
- **THEN** the switch SHALL be classified as "neutral"

### Requirement: Distraction App Configuration

The system SHALL allow users to configure which apps are considered distractions.

#### Scenario: Default distraction apps

- **GIVEN** a fresh installation
- **WHEN** distraction detection is initialized
- **THEN** the default distraction apps SHALL include:
  - slack, discord, teams (communication)
  - twitter, facebook, reddit, instagram (social media)
  - youtube (streaming, unless work-related patterns detected)

#### Scenario: Custom distraction apps

- **GIVEN** user adds "spotify" to distraction apps via settings
- **WHEN** they switch to Spotify
- **THEN** the switch SHALL be classified as "distraction"

### Requirement: Context Switch Aggregation

The system SHALL provide aggregated context switch metrics for analysis.

#### Scenario: Daily switch count

- **GIVEN** 45 context switches occurred today
- **WHEN** `GET /api/analytics/switches?date=today` is called
- **THEN** the response SHALL include:
  - `total_switches: 45`
  - `distraction_switches: count`
  - `productive_switches: count`
  - `neutral_switches: count`

#### Scenario: Hourly breakdown

- **GIVEN** context switches throughout the day
- **WHEN** `GET /api/analytics/switches?date=today&breakdown=hourly` is called
- **THEN** the response SHALL include switch counts per hour
- **AND** indicate peak switching hours

### Requirement: Top Interruptors Analysis

The system SHALL identify which apps cause the most interruptions.

#### Scenario: Top apps by switch count

- **GIVEN** switches to Slack: 15, Firefox: 10, Terminal: 8
- **WHEN** `GET /api/analytics/switches/interruptors?date=today` is called
- **THEN** the response SHALL rank apps by switch frequency
- **AND** include percentage of total switches

#### Scenario: Top apps by lost focus time

- **GIVEN** app switches with various durations
- **WHEN** interruptors are calculated with `metric=time_lost`
- **THEN** apps SHALL be ranked by total time spent after switching to them
- **AND** estimate "recovery time" (time to return to productive work)

### Requirement: Context Switch Query API

The system SHALL provide an API to query raw context switch events.

#### Scenario: Query switches by date range

- **GIVEN** 200 context switches in a week
- **WHEN** `GET /api/analytics/switches?start=2024-01-01&end=2024-01-07` is called
- **THEN** the response SHALL include all switch events
- **AND** support pagination via `limit` and `offset` parameters

#### Scenario: Filter by switch type

- **GIVEN** 50 switches, 15 of which are "distraction"
- **WHEN** `GET /api/analytics/switches?type=distraction` is called
- **THEN** only the 15 distraction switches SHALL be returned

### Requirement: Ramp-Up Cost Estimation

The system SHALL estimate the cognitive cost of context switches.

#### Scenario: Calculate ramp-up time

- **GIVEN** historical data showing average time to return to focus after switch
- **WHEN** ramp-up cost is calculated for today
- **THEN** the system SHALL estimate total "lost time" due to switches
- **AND** use a default of 2 minutes per switch if insufficient data

#### Scenario: Display ramp-up cost in UI

- **GIVEN** 45 context switches today with estimated 2-min ramp-up each
- **WHEN** the analytics dashboard is displayed
- **THEN** it SHALL show "~90 minutes lost to context switching"

## Database Schema

```sql
CREATE TABLE context_switches (
    id INTEGER PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    from_app TEXT,
    from_window TEXT,
    to_app TEXT,
    to_window TEXT,
    switch_type TEXT,  -- 'productive', 'distraction', 'neutral'
    time_in_previous REAL,  -- seconds spent in previous window
    session_id INTEGER REFERENCES activity_sessions(id)
);

CREATE INDEX idx_switch_time ON context_switches(timestamp);
CREATE INDEX idx_switch_type ON context_switches(switch_type);
CREATE INDEX idx_switch_session ON context_switches(session_id);
```

## Configuration

```yaml
context_switches:
  enabled: true
  ramp_up_estimate_minutes: 2  # Default ramp-up time per switch
  distraction_apps:
    - slack
    - discord
    - teams
    - twitter
    - facebook
    - reddit
    - instagram
    - youtube
```

## Files

- `tracker/flow_detector.py` - ContextSwitch recording (integrated with flow)
- `tracker/analytics.py` - Aggregation and analysis functions
- `tracker/storage.py` - Database table creation
- `web/app.py` - API endpoints
