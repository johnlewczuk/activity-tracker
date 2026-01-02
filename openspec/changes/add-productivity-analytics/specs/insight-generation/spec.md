# Capability: Insight Generation

Generates actionable, pattern-based insights about productivity habits using rule-based analysis of historical data.

## ADDED Requirements

### Requirement: Pattern-Based Insight Rules

The system SHALL generate insights using configurable rules that analyze productivity patterns.

#### Scenario: Time-of-day productivity insight

- **GIVEN** morning flow_minutes = 120, afternoon flow_minutes = 40
- **WHEN** insight generation runs
- **THEN** an insight SHALL be generated:
  - `category`: "time_of_day"
  - `message`: "Your mornings are 3x more productive than afternoons"
  - `data`: { morning: 120, afternoon: 40, ratio: 3.0 }
  - `priority`: "high"

#### Scenario: Context switching insight

- **GIVEN** today's context_switches = 65
- **AND** baseline (4-week average) = 40
- **WHEN** insight generation runs
- **THEN** an insight SHALL be generated:
  - `category`: "context_switching"
  - `message`: "You had 63% more context switches than usual today"
  - `data`: { today: 65, baseline: 40, change_percent: 62.5 }

#### Scenario: Flow improvement insight

- **GIVEN** this week's avg_flow_score = 72
- **AND** last week's avg_flow_score = 58
- **WHEN** insight generation runs
- **THEN** an insight SHALL be generated:
  - `category`: "improvement"
  - `message`: "Your flow quality improved 24% this week. Keep it up!"
  - `data`: { this_week: 72, last_week: 58, improvement: 24.1 }

### Requirement: Insight Categories

The system SHALL organize insights into categories for filtering and prioritization.

| Category | Description | Example Insight |
|----------|-------------|-----------------|
| time_of_day | Productivity patterns by hour | "Mornings are 2x more productive" |
| context_switching | Switch pattern analysis | "You switch apps every 3 minutes on average" |
| improvement | Positive trend detection | "Flow time increased 30% this week" |
| concern | Negative trend detection | "Distraction time doubled since last week" |
| peak_hours | Best working hours | "Your peak hours are 9-11 AM and 3-5 PM" |
| top_interruptors | Frequent distractions | "Slack interrupts you 15 times per day" |
| flow_breakers | What breaks flow | "You lose flow after checking email" |
| recovery | Focus recovery patterns | "It takes you ~5 min to refocus after Slack" |

#### Scenario: Insights categorized in response

- **GIVEN** insight generation produces 5 insights
- **WHEN** `GET /api/insights` is called
- **THEN** each insight SHALL have a `category` field
- **AND** insights can be filtered by `?category=concern`

### Requirement: Insight Prioritization

The system SHALL prioritize insights by relevance and actionability.

#### Scenario: High priority insight

- **GIVEN** an insight about 3x morning productivity advantage
- **WHEN** priority is calculated
- **THEN** it SHALL be "high" (strong signal, actionable)

#### Scenario: Medium priority insight

- **GIVEN** an insight about 20% improvement in flow score
- **WHEN** priority is calculated
- **THEN** it SHALL be "medium" (positive, but less actionable)

#### Scenario: Low priority insight

- **GIVEN** an insight about minor variation within normal range
- **WHEN** priority is calculated
- **THEN** it SHALL be "low" or not generated at all

### Requirement: Actionable Suggestions

The system SHALL include actionable suggestions with relevant insights.

#### Scenario: Morning productivity suggestion

- **GIVEN** insight: "Your mornings are 3x more productive"
- **WHEN** the insight is generated
- **THEN** it SHALL include:
  - `suggestion`: "Consider scheduling important tasks before noon"
  - `action_type`: "schedule_recommendation"

#### Scenario: Context switching suggestion

- **GIVEN** insight: "You switch apps every 3 minutes"
- **WHEN** the insight is generated
- **THEN** it SHALL include:
  - `suggestion`: "Try batching similar tasks. Group email checks to 3x daily"
  - `action_type`: "behavior_change"

#### Scenario: Distraction suggestion

- **GIVEN** insight: "Slack interrupts you 15 times per day"
- **WHEN** the insight is generated
- **THEN** it SHALL include:
  - `suggestion`: "Consider enabling Slack DND during focus hours"
  - `action_type`: "app_setting"

### Requirement: Daily Insight Generation

The system SHALL generate insights automatically at configurable intervals.

#### Scenario: End-of-day insight generation

- **GIVEN** insight generation is enabled
- **WHEN** the day ends (or on-demand trigger)
- **THEN** insights for the day SHALL be generated
- **AND** stored for later retrieval

#### Scenario: Weekly insight summary

- **GIVEN** a week of activity data
- **WHEN** weekly insights are generated
- **THEN** a "Weekly Productivity Report Card" insight SHALL be created
- **AND** include: flow_time, switches, distractions, improvement_areas

### Requirement: Insight Query API

The system SHALL provide an API to retrieve generated insights.

#### Scenario: Get today's insights

- **GIVEN** 5 insights generated for today
- **WHEN** `GET /api/insights?date=today` is called
- **THEN** all 5 insights SHALL be returned
- **AND** ordered by priority (high first)

#### Scenario: Get weekly insights

- **GIVEN** insights for the past week
- **WHEN** `GET /api/insights?period=week` is called
- **THEN** aggregated weekly insights SHALL be returned
- **AND** include week-over-week comparisons

#### Scenario: Filter by category

- **GIVEN** insights in multiple categories
- **WHEN** `GET /api/insights?category=concern` is called
- **THEN** only "concern" category insights SHALL be returned

### Requirement: Insight Confidence Scoring

The system SHALL assign confidence scores to insights based on data quality.

#### Scenario: High confidence insight

- **GIVEN** insight based on 30 days of data
- **AND** strong, consistent pattern
- **WHEN** confidence is calculated
- **THEN** confidence SHALL be >= 0.8

#### Scenario: Low confidence insight

- **GIVEN** insight based on only 3 days of data
- **WHEN** confidence is calculated
- **THEN** confidence SHALL be < 0.5
- **AND** insight message SHALL include "early data suggests..."

### Requirement: Insight Deduplication

The system SHALL avoid generating redundant or repetitive insights.

#### Scenario: Same insight not repeated daily

- **GIVEN** "Mornings are 2x more productive" was generated yesterday
- **WHEN** today's insights are generated
- **THEN** the same insight SHALL NOT be repeated
- **UNLESS** the pattern has changed significantly

#### Scenario: Similar insights consolidated

- **GIVEN** multiple insights about context switching
- **WHEN** insights are generated
- **THEN** they SHALL be consolidated into one
- **OR** only the most relevant SHALL be shown

## Insight Rule Examples

```python
INSIGHT_RULES = [
    {
        'id': 'morning_productivity',
        'condition': 'morning_flow > afternoon_flow * 1.5',
        'template': 'Your mornings are {ratio:.1f}x more productive than afternoons.',
        'suggestion': 'Consider scheduling important tasks before noon.',
        'category': 'time_of_day',
        'priority_fn': lambda ratio: 'high' if ratio > 2 else 'medium'
    },
    {
        'id': 'switch_frequency',
        'condition': 'context_switches > baseline * 1.3',
        'template': 'You had {percent:.0f}% more context switches than usual ({count} vs {baseline}).',
        'suggestion': 'Try batching similar tasks to reduce interruptions.',
        'category': 'context_switching',
        'priority_fn': lambda percent: 'high' if percent > 50 else 'medium'
    },
    # ... more rules
]
```

## API Endpoints

```
GET /api/insights
  Query parameters:
    - date: specific date or "today"
    - period: "day", "week", "month"
    - category: filter by category
    - priority: filter by priority
  Returns: Array of insight objects

POST /api/insights/generate
  Force regeneration of insights for a period
  Body: { period: "today" | "week" }

GET /api/insights/rules
  Get list of active insight rules (for debugging/transparency)
```

## Files

- `tracker/insights.py` - InsightGenerator class and rules
- `tracker/analytics.py` - Data aggregation for insight generation
- `web/app.py` - API endpoints
- `web/templates/insights.html` - Insights UI page
