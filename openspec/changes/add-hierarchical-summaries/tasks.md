# Tasks: Add Hierarchical Summaries

## 1. Database Schema

- [ ] 1.1 Add `prompt_text` column to `cached_reports` table
- [ ] 1.2 Add `explanation` column to `cached_reports` table
- [ ] 1.3 Add `tags` column to `cached_reports` table (JSON array)
- [ ] 1.4 Add `confidence` column to `cached_reports` table (REAL)
- [ ] 1.5 Add `child_summary_ids` column to `cached_reports` table (JSON array)
- [ ] 1.6 Add `regenerated_at` column to `cached_reports` table (TIMESTAMP)
- [ ] 1.7 Update `save_cached_report()` to accept and store new fields
- [ ] 1.8 Update `get_cached_report()` to return new fields

## 2. Weekly Summary Generation

- [ ] 2.1 Add `_get_week_period_date()` helper to format ISO week string (YYYY-Www)
- [ ] 2.2 Add `_maybe_generate_weekly_summaries()` method to SummarizerWorker
- [ ] 2.3 Implement weekly summary generation logic in ReportGenerator
- [ ] 2.4 Add `generate_weekly_report()` method to ReportGenerator
- [ ] 2.5 Store weekly summaries with proper prompt_text and child_summary_ids
- [ ] 2.6 Call weekly generation from run loop (check on Sundays)

## 3. Monthly Summary Generation

- [ ] 3.1 Add `_get_month_period_date()` helper to format month string (YYYY-MM)
- [ ] 3.2 Add `_maybe_generate_monthly_summaries()` method to SummarizerWorker
- [ ] 3.3 Implement monthly summary generation logic in ReportGenerator
- [ ] 3.4 Add `generate_monthly_report()` method to ReportGenerator
- [ ] 3.5 Store monthly summaries with proper prompt_text and child_summary_ids
- [ ] 3.6 Call monthly generation from run loop (check on 1st of month)

## 4. Enhance Daily Summary Generation

- [ ] 4.1 Update `generate_daily_report()` to store prompt_text
- [ ] 4.2 Update `generate_daily_report()` to store explanation field
- [ ] 4.3 Update `generate_daily_report()` to store tags (extracted from child summaries)
- [ ] 4.4 Update `generate_daily_report()` to store confidence score
- [ ] 4.5 Update `generate_daily_report()` to store child_summary_ids (threshold_summary IDs)

## 5. Regeneration Support

- [ ] 5.1 Add `regenerate_cached_report()` method to ReportGenerator
- [ ] 5.2 Update storage to support in-place update of cached_reports
- [ ] 5.3 Set `regenerated_at` timestamp on regeneration
- [ ] 5.4 Queue regeneration through SummarizerWorker (like threshold summaries)

## 6. API Endpoints

- [ ] 6.1 Add `/api/hierarchical-summaries/<period_type>/<period_date>` GET endpoint
- [ ] 6.2 Add `/api/hierarchical-summaries/<period_type>/<period_date>/regenerate` POST endpoint
- [ ] 6.3 Add `/api/hierarchical-summaries/<period_type>/<period_date>` DELETE endpoint
- [ ] 6.4 Return child summaries in detail response (nested data)

## 7. Detail Page Template

- [ ] 7.1 Create `hierarchical_summary_detail.html` template
- [ ] 7.2 Show summary text with explanation and tags
- [ ] 7.3 Show child summaries list with links to their detail pages
- [ ] 7.4 Show generation details (model, inference time, prompt)
- [ ] 7.5 Add regenerate and delete buttons
- [ ] 7.6 Show analytics roll-up (total time, top apps)

## 8. Route and Navigation

- [ ] 8.1 Add `/summary/daily/<date>` route
- [ ] 8.2 Add `/summary/weekly/<week>` route
- [ ] 8.3 Add `/summary/monthly/<month>` route
- [ ] 8.4 Update reports.html to link to detail pages instead of regenerating

## 9. Reports Page Integration

- [ ] 9.1 Update reports page to load cached summaries for day/week/month views
- [ ] 9.2 Add "View Details" link on each summary in reports page
- [ ] 9.3 Show regeneration status indicator if summary is stale
- [ ] 9.4 Add manual regenerate button in reports page UI

## 10. Backfill Historical Data

- [ ] 10.1 Add `backfill_hierarchical_summaries()` method
- [ ] 10.2 Generate missing weekly summaries for last 4 weeks on startup
- [ ] 10.3 Generate missing monthly summaries for last 3 months on startup
- [ ] 10.4 Log backfill progress

## 11. Testing

- [ ] 11.1 Add unit tests for weekly summary generation
- [ ] 11.2 Add unit tests for monthly summary generation
- [ ] 11.3 Add unit tests for regeneration workflow
- [ ] 11.4 Add integration tests for API endpoints
- [ ] 11.5 Test schema migration on existing database
