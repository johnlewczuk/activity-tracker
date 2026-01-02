# Spec Delta: Hierarchical Summaries

## ADDED Requirements

### Requirement: Hierarchical Summary Generation

The system SHALL automatically generate hierarchical summaries at three levels: daily, weekly, and monthly. Each level synthesizes summaries from the level below:
- Daily summaries synthesize from 30-minute threshold summaries
- Weekly summaries synthesize from daily summaries
- Monthly summaries synthesize from weekly summaries

#### Scenario: Daily summary auto-generation
- **WHEN** the system time crosses midnight
- **AND** the previous day has at least one threshold summary
- **THEN** the system SHALL generate a daily summary for the previous day
- **AND** store it in `cached_reports` with period_type='daily'
- **AND** store the prompt_text used for generation
- **AND** store the child_summary_ids referencing the threshold summaries used

#### Scenario: Weekly summary auto-generation
- **WHEN** the system time crosses Sunday 00:05
- **AND** the previous week has at least one daily summary
- **THEN** the system SHALL generate a weekly summary for the previous week (Monday-Sunday)
- **AND** store it in `cached_reports` with period_type='weekly'
- **AND** use period_date format YYYY-Www (e.g., "2024-W52")
- **AND** store the child_summary_ids referencing the daily summaries used

#### Scenario: Monthly summary auto-generation
- **WHEN** the system time crosses the 1st of the month at 00:10
- **AND** the previous month has at least one weekly summary
- **THEN** the system SHALL generate a monthly summary for the previous month
- **AND** store it in `cached_reports` with period_type='monthly'
- **AND** use period_date format YYYY-MM (e.g., "2024-12")
- **AND** store the child_summary_ids referencing the weekly summaries used

#### Scenario: Graceful handling of missing child summaries
- **WHEN** generating a hierarchical summary
- **AND** some expected child summaries are missing
- **THEN** the system SHALL generate the summary using available child summaries
- **AND** note the coverage percentage in the explanation field

### Requirement: Hierarchical Summary Storage

The system SHALL store hierarchical summaries with full metadata for transparency and regeneration. The `cached_reports` table SHALL include:
- `prompt_text`: Full prompt sent to LLM
- `explanation`: LLM-provided explanation of the summary
- `tags`: JSON array of activity tags extracted from content
- `confidence`: Confidence score (0.0-1.0)
- `child_summary_ids`: JSON array of IDs from the child summary source
- `regenerated_at`: Timestamp of last regeneration (null if never regenerated)

#### Scenario: Store prompt text for daily summary
- **WHEN** a daily summary is generated
- **THEN** the system SHALL store the complete prompt text in `prompt_text`
- **AND** the prompt SHALL include: date, total active time, top apps, and individual 30-minute summary texts

#### Scenario: Store child summary references
- **WHEN** a weekly summary is generated from 7 daily summaries
- **THEN** the system SHALL store the IDs of all 7 daily summaries in `child_summary_ids`
- **AND** these IDs SHALL be stored as a JSON array

### Requirement: Hierarchical Summary Regeneration

The system SHALL support regenerating hierarchical summaries at any level with current LLM settings while preserving the original input data.

#### Scenario: Regenerate daily summary
- **WHEN** a user requests regeneration of a daily summary
- **THEN** the system SHALL regenerate using the same child threshold summaries
- **AND** update the existing record in-place (not create a new one)
- **AND** update the `regenerated_at` timestamp
- **AND** preserve the original `created_at` timestamp

#### Scenario: Queue regeneration through worker
- **WHEN** regeneration is requested via API
- **THEN** the system SHALL queue the regeneration task
- **AND** process it through SummarizerWorker
- **AND** return a status indicating the task is queued

### Requirement: Hierarchical Summary Detail Page

The system SHALL provide detail pages for each hierarchical summary level, showing full metadata and allowing regeneration.

#### Scenario: View daily summary detail
- **GIVEN** a daily summary exists for "2024-12-30"
- **WHEN** a user navigates to `/summary/daily/2024-12-30`
- **THEN** the page SHALL display:
  - The executive summary text
  - The explanation field
  - Tags as badges
  - Confidence score
  - List of child threshold summaries with links to their detail pages
  - Analytics (total time, top apps, activity by hour)
  - Generation details (model, inference time, created/regenerated timestamps)
  - The full prompt text (collapsed by default)
  - Regenerate and Delete buttons

#### Scenario: Navigate to child summary from detail page
- **GIVEN** a daily summary detail page is displayed
- **WHEN** a user clicks on a child threshold summary
- **THEN** the system SHALL navigate to `/summary/{child_summary_id}`

### Requirement: Hierarchical Summary API

The system SHALL provide API endpoints for retrieving and managing hierarchical summaries.

#### Scenario: Get hierarchical summary detail via API
- **WHEN** a GET request is made to `/api/hierarchical-summaries/daily/2024-12-30`
- **THEN** the API SHALL return the full summary data including:
  - summary text, explanation, tags, confidence
  - child summary IDs and their basic info (text, time range)
  - analytics data
  - generation metadata (model, inference_time_ms, prompt_text)

#### Scenario: Regenerate hierarchical summary via API
- **WHEN** a POST request is made to `/api/hierarchical-summaries/weekly/2024-W52/regenerate`
- **THEN** the API SHALL queue the regeneration task
- **AND** return status "queued" with the period info

#### Scenario: Delete hierarchical summary via API
- **WHEN** a DELETE request is made to `/api/hierarchical-summaries/monthly/2024-12`
- **THEN** the API SHALL delete the summary from the database
- **AND** return status "deleted"
- **AND** NOT delete any child summaries (they remain independent)

### Requirement: Reports Page Integration

The reports page SHALL use pre-computed hierarchical summaries for instant loading instead of generating reports on-demand.

#### Scenario: Load daily report from cache
- **GIVEN** a user views the reports page with day granularity for "2024-12-30"
- **AND** a daily summary exists for that date
- **THEN** the page SHALL load the cached summary instantly
- **AND** display a "View Details" link to the detail page
- **AND** NOT make any LLM calls

#### Scenario: Handle missing cached summary
- **GIVEN** a user views the reports page for a date without a cached summary
- **THEN** the page SHALL display a message indicating no summary available
- **AND** offer a button to "Generate Now" which triggers generation

### Requirement: Historical Backfill

The system SHALL backfill missing hierarchical summaries for recent periods on startup.

#### Scenario: Backfill weekly summaries on startup
- **WHEN** the summarizer worker starts
- **AND** there are daily summaries for weeks without weekly summaries
- **THEN** the system SHALL generate weekly summaries for the last 4 complete weeks
- **AND** log the backfill progress

#### Scenario: Backfill monthly summaries on startup
- **WHEN** the summarizer worker starts
- **AND** there are weekly summaries for months without monthly summaries
- **THEN** the system SHALL generate monthly summaries for the last 3 complete months
- **AND** log the backfill progress
