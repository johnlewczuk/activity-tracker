# Capability: Standup Automation

Generates standup-ready summaries in "Yesterday/Today/Blockers" format from existing activity data.

## ADDED Requirements

### Requirement: Standup Format Generation

The system SHALL generate standup summaries from existing threshold summaries and focus data.

#### Scenario: Generate yesterday's standup

- **GIVEN** threshold summaries exist for yesterday
- **AND** focus events exist for yesterday
- **WHEN** `GET /api/standup?date=yesterday` is called
- **THEN** a standup-formatted summary SHALL be returned with:
  ```markdown
  ## Yesterday
  - Worked on activity-tracker flow detection (2h 15m)
  - Reviewed PRs on auth-service (45m)
  - Design review meeting (1h)

  ## Today
  [To be filled in]

  ## Blockers
  [To be filled in]
  ```

#### Scenario: No data for date

- **GIVEN** no summaries exist for the requested date
- **WHEN** standup generation is requested
- **THEN** the response SHALL include:
  - `message`: "No activity data for this date"
  - `yesterday`: "No tracked activity"

### Requirement: Activity Grouping by Project/Context

The system SHALL group activities by project or work context.

#### Scenario: Multiple projects grouped

- **GIVEN** activities: VSCode (main.py, 2h), VSCode (tests.py, 1h), Slack (30m)
- **WHEN** standup is generated
- **THEN** activities SHALL be grouped:
  - "Worked on main.py and tests.py (3h)"
  - "Team communication (30m)"

#### Scenario: Terminal activities grouped with context

- **GIVEN** terminal focus events with process context (vim, python, npm)
- **WHEN** standup is generated
- **THEN** terminal activities SHALL use process names:
  - "Debugging Python scripts (45m)"
  - "Running npm builds (15m)"

### Requirement: Time Estimates in Standup

The system SHALL include time estimates for each activity line.

#### Scenario: Time included in each line

- **GIVEN** activities with various durations
- **WHEN** standup is generated
- **THEN** each activity line SHALL include duration:
  - Format: "Activity description (Xh Ym)" or "(Xm)" for < 1h

#### Scenario: Round durations appropriately

- **GIVEN** an activity of 127 minutes
- **WHEN** standup is generated
- **THEN** duration SHALL be "2h 7m" or "~2h" depending on precision setting

### Requirement: Standup Templates

The system SHALL support multiple standup templates.

#### Scenario: Default template

- **GIVEN** no template specified
- **WHEN** standup is generated
- **THEN** the default "Yesterday/Today/Blockers" template SHALL be used

#### Scenario: Custom template

- **GIVEN** user has configured a custom template:
  ```
  What I did:
  {activities}

  What's next:
  [User input]
  ```
- **WHEN** standup is generated
- **THEN** the custom template SHALL be used

#### Scenario: Available templates

The system SHALL provide these built-in templates:
- `default`: Yesterday/Today/Blockers
- `simple`: Just activities list
- `detailed`: Activities with time breakdown per app

### Requirement: Standup Editing

The system SHALL allow users to edit generated standups before sharing.

#### Scenario: Inline editing

- **GIVEN** a generated standup is displayed
- **WHEN** user clicks "Edit"
- **THEN** the standup SHALL become editable
- **AND** changes SHALL be preserved until page refresh

#### Scenario: Add blockers manually

- **GIVEN** a generated standup with empty "Blockers" section
- **WHEN** user types in the blockers section
- **THEN** the text SHALL be included in the final output

### Requirement: Copy to Clipboard

The system SHALL provide one-click copy of the standup to clipboard.

#### Scenario: Copy markdown format

- **GIVEN** a generated standup is displayed
- **WHEN** user clicks "Copy" button
- **THEN** the standup SHALL be copied to clipboard
- **AND** format SHALL be Markdown (for Slack/Discord)
- **AND** a success toast SHALL be shown

#### Scenario: Copy plain text

- **GIVEN** user prefers plain text
- **WHEN** user clicks "Copy as Plain Text"
- **THEN** markdown formatting SHALL be stripped
- **AND** bullet points SHALL be preserved with "-"

### Requirement: Standup History

The system SHALL keep a history of generated standups.

#### Scenario: View past standups

- **GIVEN** standups have been generated for multiple days
- **WHEN** user navigates to standup history
- **THEN** a list of past standups SHALL be shown
- **AND** each can be viewed or copied

#### Scenario: Regenerate standup

- **GIVEN** a past standup exists
- **WHEN** user clicks "Regenerate"
- **THEN** a new standup SHALL be generated from current data
- **AND** may differ if summaries were regenerated

### Requirement: Standup UI Page

The system SHALL provide a dedicated standup generation page.

#### Scenario: Navigate to standup page

- **GIVEN** user is on the main timeline
- **WHEN** user clicks "Standup" in navigation
- **THEN** they SHALL be taken to `/standup`
- **AND** today's standup preparation SHALL be shown

#### Scenario: Standup page layout

- **GIVEN** the standup page is loaded
- **THEN** it SHALL display:
  - Yesterday's generated content (from activity data)
  - Editable "Today" section (user fills in)
  - Editable "Blockers" section (user fills in)
  - "Copy" button
  - Date selector for other days

### Requirement: Standup API Endpoints

The system SHALL provide API endpoints for standup operations.

#### Scenario: Get standup data

- **GIVEN** activity data exists
- **WHEN** `GET /api/standup?date=yesterday` is called
- **THEN** JSON SHALL be returned with:
  ```json
  {
    "date": "2024-01-14",
    "yesterday": [
      {"activity": "Flow detection implementation", "duration_minutes": 135}
    ],
    "raw_markdown": "## Yesterday\n- Flow detection...",
    "sources": ["threshold_summaries", "focus_events"]
  }
  ```

#### Scenario: Get formatted standup

- **GIVEN** standup data exists
- **WHEN** `GET /api/standup?date=yesterday&format=markdown` is called
- **THEN** plain Markdown text SHALL be returned
- **AND** Content-Type SHALL be `text/markdown`

### Requirement: Smart Activity Summarization

The system SHALL intelligently summarize activities for standup readability.

#### Scenario: Consolidate similar activities

- **GIVEN** 10 focus events all in VSCode on different files
- **WHEN** standup is generated
- **THEN** they SHALL be consolidated:
  - "Worked on activity-tracker codebase (4h 30m)"
  - NOT 10 separate lines

#### Scenario: Meeting detection

- **GIVEN** focus events on Zoom/Meet/Teams for extended periods
- **WHEN** standup is generated
- **THEN** they SHALL be labeled as meetings:
  - "Meetings (2h 15m)"
  - If window title has meeting name: "Design Review meeting (1h)"

#### Scenario: Short activities grouped

- **GIVEN** many short (<10min) activities throughout the day
- **WHEN** standup is generated
- **THEN** they SHALL be grouped as:
  - "Various administrative tasks (45m)"
  - OR omitted if total < 15 min

### Requirement: Standup Preferences

The system SHALL allow users to configure standup generation preferences.

#### Scenario: Configure minimum activity duration

- **GIVEN** user sets `min_activity_minutes: 15`
- **WHEN** standup is generated
- **THEN** activities < 15 minutes SHALL be grouped or omitted

#### Scenario: Configure grouping strategy

- **GIVEN** user sets `grouping: by_app`
- **WHEN** standup is generated
- **THEN** activities SHALL be grouped by application, not project

## API Endpoints

```
GET /api/standup
  Query parameters:
    - date: "yesterday", "today", or YYYY-MM-DD
    - format: "json" (default), "markdown", "plain"
    - template: "default", "simple", "detailed"
  Returns: Standup data in requested format

POST /api/standup/copy
  Body: { date: "yesterday", format: "markdown" }
  Returns: { success: true, copied_text: "..." }
  (For JavaScript copy-to-clipboard fallback)

GET /api/standup/history
  Returns: List of past standup generations

GET /api/standup/templates
  Returns: Available standup templates
```

## Files

- `tracker/reports.py` - Standup generation logic
- `web/app.py` - API endpoints
- `web/templates/standup.html` - Standup UI page
- `web/static/js/standup.js` - Standup page interactions
