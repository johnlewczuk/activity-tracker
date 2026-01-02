# Change: Add Hierarchical Summaries (Daily/Weekly/Monthly)

## Why

Currently, daily/weekly/monthly reports are generated on-demand via LLM synthesis, which is:
1. **Slow**: Each report page load triggers fresh LLM calls
2. **Inconsistent**: Report content varies between loads
3. **Not inspectable**: No way to see the prompt, regenerate, or drill into details
4. **Not persistent**: Reports aren't stored or cached effectively

The 30-minute interval summaries already solve these problems - they are auto-generated, persisted, have detail pages with prompts, and can be regenerated. This change extends that same pattern to daily, weekly, and monthly granularities.

## What Changes

### New Capabilities
- **Auto-generated daily summaries**: Generated overnight, synthesizing that day's 30-minute summaries
- **Auto-generated weekly summaries**: Generated weekly (Sunday night), synthesizing daily summaries
- **Auto-generated monthly summaries**: Generated on the 1st, synthesizing weekly summaries
- **Hierarchical synthesis**: Each level builds on the level below (30min → daily → weekly → monthly)
- **Detail pages**: Each summary level has its own detail page showing prompt, regeneration, etc.
- **Regeneration**: Any summary at any level can be regenerated with current settings
- **Instant loading**: Reports page loads pre-computed summaries instead of generating on-the-fly

### Database Changes
- Extend `cached_reports` table with additional fields: `prompt_text`, `explanation`, `tags`, `confidence`
- Add `regenerated_at` timestamp for tracking regeneration history
- **BREAKING**: Schema migration required for existing `cached_reports` table

### API Changes
- New endpoints for hierarchical summary detail and regeneration
- Reports page switches from on-demand generation to cached summary lookup
- `/api/hierarchical-summaries/{period_type}/{period_date}` - Get summary detail
- `/api/hierarchical-summaries/{period_type}/{period_date}/regenerate` - Regenerate

### UI Changes
- New detail pages for daily/weekly/monthly summaries (similar to `summary_detail.html`)
- Reports page updated to link to detail pages instead of regenerating
- Timeline page may show daily summaries alongside interval summaries

## Impact

- **Affected specs**: reports (new capability)
- **Affected code**:
  - `tracker/summarizer_worker.py` - Add weekly/monthly generation to run loop
  - `tracker/reports.py` - Refactor to use hierarchical synthesis
  - `tracker/storage.py` - Extend cached_reports schema
  - `web/app.py` - Add new API endpoints and routes
  - `web/templates/` - New detail pages, update reports.html
- **Migration**: Existing `cached_reports` table needs schema migration
- **Performance**: Reduced LLM calls at report load time (pre-computed)

## Design Decisions

### Hierarchy Structure
```
30-minute interval summaries (threshold_summaries)
    ↓ synthesizes into
Daily summaries (cached_reports, period_type='daily')
    ↓ synthesizes into
Weekly summaries (cached_reports, period_type='weekly')
    ↓ synthesizes into
Monthly summaries (cached_reports, period_type='monthly')
```

### Generation Schedule
- **Daily**: Generated at midnight for the previous day (already exists but needs enhancement)
- **Weekly**: Generated Sunday 00:05 for the previous week (Mon-Sun)
- **Monthly**: Generated 1st of month 00:10 for the previous month

### Prompt Storage
Each hierarchical summary stores its full prompt text for transparency and debugging, matching the pattern established by threshold_summaries.

### Regeneration Behavior
- Regeneration updates the existing record in-place (like threshold_summaries)
- Stores `regenerated_at` timestamp to track history
- Uses current LLM settings but same input data (child summaries)
