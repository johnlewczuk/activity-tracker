# Design: Hierarchical Summaries

## Context

The Activity Tracker currently has two separate summarization systems:

1. **Threshold Summaries** (`threshold_summaries` table): Auto-generated every 30 minutes, stored with full metadata (prompt, explanation, confidence, tags), has detail page, supports regeneration.

2. **Cached Reports** (`cached_reports` table): Daily summaries auto-generated at midnight, but missing key features (no prompt storage, no detail page, no regeneration UI). Weekly/monthly reports are generated on-demand (slow, inconsistent).

This design unifies both systems under a single hierarchical model where each level synthesizes from the level below.

## Goals

1. **Instant report loading**: All granularities load pre-computed summaries
2. **Full transparency**: All summaries show prompts, can be inspected and regenerated
3. **Hierarchical synthesis**: Daily uses 30-min, weekly uses daily, monthly uses weekly
4. **Consistent UX**: Same detail page pattern across all summary types
5. **Backward compatibility**: Existing threshold_summaries and cached_reports continue working

## Non-Goals

1. Custom time ranges (e.g., "last 3 days") - still generated on-demand
2. Real-time summary updates during the day
3. Quarterly or yearly summary aggregation
4. Cross-week or cross-month synthesis

## Decisions

### Decision 1: Extend cached_reports table (not create new table)

**Choice**: Add columns to existing `cached_reports` table rather than creating a new table.

**Rationale**:
- Daily reports already use this table
- Keeps all hierarchical summaries in one place
- Simpler queries (one table to query for any period type)
- Easy schema migration with ALTER TABLE

**Alternatives considered**:
- Separate `hierarchical_summaries` table: More normalized but adds complexity
- Merge with `threshold_summaries`: Different semantics (interval vs. calendar-based)

### Decision 2: Generate weekly/monthly in summarizer_worker

**Choice**: Extend `SummarizerWorker._run_loop()` to check for and generate weekly/monthly summaries.

**Rationale**:
- Consistent with existing daily generation pattern
- Reuses existing worker infrastructure
- Single place for all scheduled summarization

**Alternatives considered**:
- Separate cron job: More moving parts, harder to coordinate
- Generate on first request: Slow first load, doesn't solve the core problem

### Decision 3: Use ISO week numbers for weekly summaries

**Choice**: `period_date` for weekly summaries uses ISO format `YYYY-Www` (e.g., "2024-W52").

**Rationale**:
- Standard, unambiguous representation
- Aligns with international week numbering
- Easy to parse and validate

**Alternatives considered**:
- Week start date: Ambiguous (what day does week start?)
- Custom format: Non-standard, harder to work with

### Decision 4: Create separate detail page template

**Choice**: Create `hierarchical_summary_detail.html` template, not reuse `summary_detail.html`.

**Rationale**:
- Different data structure (synthesizes child summaries vs. screenshots)
- Can show child summaries in the detail view
- Cleaner separation of concerns

**Alternatives considered**:
- Parameterized single template: Too complex with conditionals
- Client-side rendering: Inconsistent with Flask+Jinja pattern

### Decision 5: Store child summary IDs as JSON array

**Choice**: Store `child_summary_ids` as JSON array in the database (like existing `summary_ids_json`).

**Rationale**:
- Consistent with existing pattern
- Easy to query and update
- SQLite handles JSON well

**Schema additions**:
```sql
ALTER TABLE cached_reports ADD COLUMN prompt_text TEXT;
ALTER TABLE cached_reports ADD COLUMN explanation TEXT;
ALTER TABLE cached_reports ADD COLUMN tags TEXT;  -- JSON array
ALTER TABLE cached_reports ADD COLUMN confidence REAL;
ALTER TABLE cached_reports ADD COLUMN child_summary_ids TEXT;  -- JSON array of child IDs
ALTER TABLE cached_reports ADD COLUMN regenerated_at TIMESTAMP;
```

### Decision 6: Prompt structure for hierarchical synthesis

**Choice**: Prompts include child summary texts with their dates/times, analytics roll-up, and clear synthesis instructions.

**Daily prompt structure**:
```
Synthesize these 30-minute activity summaries into a daily summary.
Date: {date}
Total active time: {minutes} minutes
Top apps: {apps}

Individual summaries (chronological):
- 09:00-09:30: {summary_text}
- 09:30-10:00: {summary_text}
...

Write 3-5 sentences covering main themes, projects, and accomplishments.
Be concise. Use specific project names from summaries.
```

**Weekly prompt structure**:
```
Synthesize these daily summaries into a weekly summary.
Week: {week_start} to {week_end}
Total active time: {hours} hours across {days} days
Top apps: {apps}

Daily summaries:
- Monday Dec 30: {summary_text}
- Tuesday Dec 31: {summary_text}
...

Write 4-6 sentences covering main themes, patterns, and key accomplishments.
Identify any recurring work patterns or project focus areas.
```

### Decision 7: Regeneration updates in-place

**Choice**: Regeneration updates the existing row rather than creating a new one.

**Rationale**:
- Consistent with threshold_summary regeneration behavior
- Avoids orphan records
- Simpler to understand (one summary per period)

**Tracking**: Add `regenerated_at` timestamp to track when last regenerated.

## Risks / Trade-offs

### Risk: Large monthly summaries exceed context window
**Mitigation**: Limit to top N daily summaries (30 max) or summarize in chunks.

### Risk: Missing child summaries cause gaps
**Mitigation**: Generate summaries even with partial data; note in explanation.

### Risk: Schema migration on production data
**Mitigation**: Use nullable columns with ALTER TABLE (safe for SQLite).

## Migration Plan

1. Schema migration: Add new columns to `cached_reports` (nullable, no data loss)
2. Deploy code changes
3. Existing daily reports continue working (new columns are optional)
4. Weekly/monthly generation starts populating new periods
5. Reports page updated to use cached summaries
6. Detail page routes added

**Rollback**: New columns can be ignored; old behavior still works.

## Open Questions

1. Should we backfill historical weekly/monthly summaries on first run?
   - **Suggested**: Yes, for last 4 weeks and 3 months
2. Should monthly summaries synthesize from weekly or daily?
   - **Suggested**: Weekly (fewer inputs, already synthesized)
3. Should the timeline page show daily summary cards?
   - **Suggested**: Optional, add as future enhancement
