# Capability: Comparative Analytics

Enables side-by-side comparison of productivity metrics across different time periods with trend indicators.

## ADDED Requirements

### Requirement: Period-to-Period Comparison

The system SHALL compare productivity metrics between two time periods.

#### Scenario: This week vs last week

- **GIVEN** metrics exist for this week and last week
- **WHEN** `GET /api/analytics/compare?period1=this_week&period2=last_week` is called
- **THEN** the response SHALL include for each metric:
  - `current`: value for period1
  - `previous`: value for period2
  - `change_percent`: ((current - previous) / previous) * 100
  - `trend`: "up", "down", or "stable"

#### Scenario: Today vs yesterday

- **GIVEN** metrics exist for today and yesterday
- **WHEN** comparison is requested
- **THEN** the response SHALL show:
  - `flow_minutes`: current vs previous with % change
  - `context_switches`: current vs previous with % change
  - `distraction_minutes`: current vs previous with % change
  - `avg_flow_score`: current vs previous with % change

#### Scenario: Insufficient data for comparison

- **GIVEN** no data exists for the previous period
- **WHEN** comparison is requested
- **THEN** the response SHALL include:
  - `previous: null`
  - `change_percent: null`
  - `message: "No data for comparison period"`

### Requirement: Rolling Baseline Calculation

The system SHALL calculate a rolling baseline (default: 4-week average) for anomaly detection.

#### Scenario: Calculate baseline metrics

- **GIVEN** 4 weeks of historical data
- **WHEN** baseline is computed
- **THEN** it SHALL include average values for:
  - flow_minutes_per_day
  - context_switches_per_day
  - distraction_minutes_per_day
  - avg_flow_score

#### Scenario: Compare against baseline

- **GIVEN** today's context_switches = 60, baseline = 40
- **WHEN** anomaly detection runs
- **THEN** the system SHALL flag this as "above baseline" (+50%)
- **AND** include in insights

### Requirement: Trend Analysis

The system SHALL identify trends over time (improving, declining, stable).

#### Scenario: Improving flow time trend

- **GIVEN** flow_minutes over 4 weeks: [120, 150, 180, 210]
- **WHEN** trend analysis runs
- **THEN** flow_minutes SHALL be marked as "improving"
- **AND** average improvement rate SHALL be calculated

#### Scenario: Stable metric

- **GIVEN** context_switches over 4 weeks: [45, 42, 48, 44]
- **WHEN** trend analysis runs
- **THEN** context_switches SHALL be marked as "stable"
- **AND** variance SHALL be noted as within normal range (< 15%)

#### Scenario: Declining metric

- **GIVEN** avg_flow_score over 4 weeks: [80, 75, 68, 62]
- **WHEN** trend analysis runs
- **THEN** avg_flow_score SHALL be marked as "declining"
- **AND** this SHALL be flagged for user attention

### Requirement: Comparison Metrics

The system SHALL track and compare the following core metrics:

| Metric | Description | Unit |
|--------|-------------|------|
| flow_minutes | Total time in flow state | minutes |
| flow_sessions | Number of flow sessions | count |
| avg_flow_score | Average flow score | 0-100 |
| context_switches | Total context switches | count |
| distraction_switches | Switches to distraction apps | count |
| distraction_minutes | Time spent in distraction apps | minutes |
| total_tracked_minutes | Total tracked work time | minutes |
| deep_work_percentage | flow_minutes / total_tracked_minutes | percent |

#### Scenario: All metrics in comparison response

- **GIVEN** a comparison request for two periods
- **WHEN** the response is returned
- **THEN** it SHALL include all 8 core metrics
- **AND** each metric SHALL have current, previous, and change values

### Requirement: Anomaly Detection

The system SHALL detect unusual patterns that deviate significantly from baseline.

#### Scenario: Unusually high context switching

- **GIVEN** today's context_switches = 80
- **AND** baseline (4-week average) = 40
- **WHEN** anomaly detection runs
- **THEN** an anomaly SHALL be flagged: "Context switches 2x higher than usual"

#### Scenario: Unusually low flow time

- **GIVEN** today's flow_minutes = 30
- **AND** baseline = 150
- **WHEN** anomaly detection runs
- **THEN** an anomaly SHALL be flagged: "Flow time 80% below normal"

#### Scenario: Normal variation not flagged

- **GIVEN** today's context_switches = 45
- **AND** baseline = 40 (12.5% above)
- **WHEN** anomaly detection runs
- **THEN** no anomaly SHALL be flagged (within normal variance)

### Requirement: Daily Analytics Cache

The system SHALL cache daily aggregated metrics for fast querying.

#### Scenario: Cache updated end of day

- **GIVEN** a day has completed
- **WHEN** the daily analytics cache is computed
- **THEN** `daily_analytics` table SHALL be updated with:
  - date, total_tracked_minutes, flow_minutes, context_switches
  - distraction_minutes, top_apps (JSON), computed_at

#### Scenario: Cache used for comparison

- **GIVEN** a comparison request for last 30 days
- **WHEN** metrics are retrieved
- **THEN** cached daily values SHALL be used
- **AND** query time SHALL be < 100ms

#### Scenario: Cache refresh on demand

- **GIVEN** user requests a cache refresh
- **WHEN** `POST /api/analytics/refresh?date=2024-01-15` is called
- **THEN** the cache for that date SHALL be recomputed
- **AND** new values SHALL be returned

## API Endpoints

```
GET /api/analytics/compare?period1=&period2=
  Returns comparison of two periods

GET /api/analytics/trends?metric=&days=30
  Returns trend analysis for a metric

GET /api/analytics/anomalies?date=today
  Returns detected anomalies for a date

GET /api/analytics/baseline
  Returns current baseline values (4-week average)

POST /api/analytics/refresh?date=
  Refreshes cache for a specific date
```

## Database Schema

```sql
CREATE TABLE daily_analytics (
    id INTEGER PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    total_tracked_minutes REAL,
    flow_minutes REAL,
    flow_sessions INTEGER,
    avg_flow_score REAL,
    context_switches INTEGER,
    distraction_switches INTEGER,
    distraction_minutes REAL,
    top_apps JSON,
    computed_at TIMESTAMP
);

CREATE INDEX idx_daily_date ON daily_analytics(date);
```

## Files

- `tracker/analytics.py` - Comparison and trend functions
- `tracker/storage.py` - daily_analytics table
- `web/app.py` - API endpoints
