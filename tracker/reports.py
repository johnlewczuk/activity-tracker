"""Activity Report Generator for Activity Tracker.

This module generates comprehensive activity reports for specified time ranges.
It synthesizes existing summaries into cohesive narratives and computes
analytics from screenshot and session data.

Report types supported:
- Summary: High-level overview with executive summary
- Detailed: Day-by-day breakdown
- Standup: Brief bullet points for standup meetings

Note: Project detection was deprecated in Phase 8. Reports now rely on
LLM-generated summaries which already contain interpreted activity context.
The LLM interprets raw app/window usage data during summarization, so
report generation simply synthesizes these existing summaries.

Example:
    >>> from tracker.reports import ReportGenerator
    >>> generator = ReportGenerator(storage, summarizer, config)
    >>> report = generator.generate("last week", report_type="summary")
    >>> print(report.executive_summary)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, TYPE_CHECKING
import json
import logging

from .timeparser import TimeParser

if TYPE_CHECKING:
    from .storage import ActivityStorage
    from .vision import HybridSummarizer
    from .config import ConfigManager

logger = logging.getLogger(__name__)


@dataclass
class ReportSection:
    """A thematic section within a report.

    Attributes:
        title: Section heading.
        content: Section body text.
        screenshots: Optional list of screenshots for this section.
    """
    title: str
    content: str
    screenshots: List[dict] = field(default_factory=list)


@dataclass
class ReportAnalytics:
    """Analytics data for a report.

    Attributes:
        total_active_minutes: Total minutes of activity.
        total_sessions: Number of activity sessions.
        top_apps: Top applications by usage time.
        top_windows: Top window titles by usage time.
        activity_by_hour: Minutes of activity per hour (24 values).
        activity_by_day: Activity by day with date and minutes.
        busiest_period: Description of the busiest period.
    """
    total_active_minutes: int
    total_sessions: int
    top_apps: List[dict]
    top_windows: List[dict]
    activity_by_hour: List[int]
    activity_by_day: List[dict]
    busiest_period: str


@dataclass
class Report:
    """A complete activity report.

    Attributes:
        title: Report title.
        time_range: Human-readable time range description.
        generated_at: When the report was generated.
        executive_summary: High-level summary text.
        sections: List of thematic sections.
        analytics: Analytics data.
        key_screenshots: Representative screenshots.
        raw_summaries: Original summaries used to generate report.
    """
    title: str
    time_range: str
    generated_at: datetime
    executive_summary: str
    sections: List[ReportSection]
    analytics: ReportAnalytics
    key_screenshots: List[dict]
    raw_summaries: List[dict]


class ReportGenerator:
    """Generate activity reports for time ranges.

    Combines existing summaries with analytics to produce comprehensive
    reports in various formats.

    Attributes:
        storage: ActivityStorage instance for database access.
        summarizer: HybridSummarizer for generating report text.
        config: ConfigManager for configuration settings.
        time_parser: TimeParser for parsing natural language time ranges.
    """

    def __init__(
        self,
        storage: "ActivityStorage",
        summarizer: "HybridSummarizer",
        config: "ConfigManager"
    ):
        """Initialize ReportGenerator.

        Args:
            storage: ActivityStorage instance.
            summarizer: HybridSummarizer instance.
            config: ConfigManager instance.
        """
        self.storage = storage
        self.summarizer = summarizer
        self.config = config
        self.time_parser = TimeParser()

    def generate(
        self,
        time_range: str,
        report_type: str = "summary",
        include_screenshots: bool = True,
        max_screenshots: int = 10
    ) -> Report:
        """Generate a report for the given time range.

        Args:
            time_range: Natural language time range (e.g., "last week").
            report_type: Type of report - "summary", "detailed", or "standup".
            include_screenshots: Whether to include key screenshots.
            max_screenshots: Maximum number of screenshots to include.

        Returns:
            Report object with all data populated.

        Raises:
            ValueError: If time_range cannot be parsed.
        """
        # Parse time range
        start, end = self.time_parser.parse(time_range)

        # Validate time range
        self._validate_time_range(start, end)

        range_description = self.time_parser.describe_range(start, end)

        logger.info(f"Generating {report_type} report for {range_description}")

        # Gather data
        summaries = self.storage.get_summaries_in_range(start, end)
        screenshots = self.storage.get_screenshots_in_range(start, end)
        sessions = self.storage.get_sessions_in_range(start, end)

        # Get focus events for app/window usage analytics
        # First try with require_session=True to exclude AFK periods
        focus_events = self.storage.get_focus_events_in_range(start, end, require_session=True)

        # If no focus events found, try without session filter for older data
        # (pre-Phase 15 data doesn't have session_id assigned)
        if not focus_events and screenshots:
            focus_events = self.storage.get_focus_events_in_range(start, end, require_session=False)
            if focus_events:
                logger.info(
                    f"Using {len(focus_events)} focus events without session filter "
                    "(older data before session tracking was added)"
                )

        logger.debug(
            f"Found {len(summaries)} summaries, "
            f"{len(screenshots)} screenshots, {len(sessions)} sessions, "
            f"{len(focus_events)} focus events"
        )

        # Compute analytics
        analytics = self._compute_analytics(screenshots, sessions, start, end)

        # Select key screenshots
        key_screenshots = []
        if include_screenshots:
            key_screenshots = self._select_key_screenshots(
                screenshots, summaries, max_screenshots
            )

        # Generate report content based on type
        if report_type == "standup":
            report = self._generate_standup(summaries, analytics, range_description, focus_events)
        elif report_type == "detailed":
            report = self._generate_detailed(summaries, analytics, range_description, start, end, focus_events)
        else:
            report = self._generate_summary(summaries, analytics, range_description, focus_events)

        report.key_screenshots = key_screenshots
        report.raw_summaries = summaries
        report.analytics = analytics

        return report

    def _validate_time_range(self, start: datetime, end: datetime) -> None:
        """Validate time range for report generation.

        Args:
            start: Start datetime.
            end: End datetime.

        Raises:
            ValueError: If the time range is invalid.
        """
        now = datetime.now()

        # Check if end is before start
        if end < start:
            raise ValueError(
                f"Invalid time range: end ({end.strftime('%Y-%m-%d %H:%M')}) "
                f"is before start ({start.strftime('%Y-%m-%d %H:%M')})"
            )

        # Check if range is too large (> 365 days)
        max_days = 365
        range_days = (end - start).days
        if range_days > max_days:
            raise ValueError(
                f"Time range too large: {range_days} days. "
                f"Maximum allowed is {max_days} days."
            )

        # Check if entire range is in the future
        if start > now:
            raise ValueError(
                f"Time range is entirely in the future. "
                f"No data available for {start.strftime('%Y-%m-%d')} onwards."
            )

        # Warn but don't error if end extends into future (just clip to now)
        # The storage queries will naturally return no data for future timestamps

    def _compute_analytics(
        self,
        screenshots: List[dict],
        sessions: List[dict],
        start: datetime,
        end: datetime
    ) -> ReportAnalytics:
        """Compute analytics from raw data.

        Args:
            screenshots: List of screenshot dicts.
            sessions: List of session dicts.
            start: Start of time range.
            end: End of time range.

        Returns:
            ReportAnalytics with all metrics computed.
        """
        # Active time from sessions (handle None values)
        total_minutes = sum((s.get('duration_seconds') or 0) // 60 for s in sessions)

        # App usage from screenshots
        interval_minutes = self.config.config.capture.interval_seconds / 60
        app_minutes = {}
        for ss in screenshots:
            app = ss.get('app_name', 'Unknown') or 'Unknown'
            app_minutes[app] = app_minutes.get(app, 0) + interval_minutes

        total_app_minutes = sum(app_minutes.values()) or 1
        top_apps = sorted([
            {
                'name': app,
                'minutes': int(mins),
                'percentage': round(mins / total_app_minutes * 100, 1)
            }
            for app, mins in app_minutes.items()
        ], key=lambda x: -x['minutes'])[:10]

        # Window usage
        window_minutes = {}
        for ss in screenshots:
            title = ss.get('window_title', 'Unknown') or 'Unknown'
            title = title[:50] + '...' if len(title) > 50 else title
            window_minutes[title] = window_minutes.get(title, 0) + interval_minutes

        top_windows = sorted([
            {'title': title, 'minutes': int(mins)}
            for title, mins in window_minutes.items()
        ], key=lambda x: -x['minutes'])[:10]

        # Activity by hour
        activity_by_hour = [0] * 24
        for ss in screenshots:
            ts = ss['timestamp']
            if isinstance(ts, int):
                hour = datetime.fromtimestamp(ts).hour
            else:
                hour = datetime.fromisoformat(str(ts)).hour
            activity_by_hour[hour] += interval_minutes

        # Activity by day
        day_minutes = {}
        for ss in screenshots:
            ts = ss['timestamp']
            if isinstance(ts, int):
                date_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            else:
                date_str = datetime.fromisoformat(str(ts)).strftime('%Y-%m-%d')
            day_minutes[date_str] = day_minutes.get(date_str, 0) + interval_minutes

        activity_by_day = [
            {'date': date, 'minutes': int(mins)}
            for date, mins in sorted(day_minutes.items())
        ]

        # Find busiest period
        busiest_period = self._find_busiest_period(screenshots)

        return ReportAnalytics(
            total_active_minutes=total_minutes or int(sum(app_minutes.values())),
            total_sessions=len(sessions),
            top_apps=top_apps,
            top_windows=top_windows,
            activity_by_hour=[int(h) for h in activity_by_hour],
            activity_by_day=activity_by_day,
            busiest_period=busiest_period
        )

    def _find_busiest_period(self, screenshots: List[dict]) -> str:
        """Find the busiest day/time period.

        Args:
            screenshots: List of screenshot dicts.

        Returns:
            Description string like "Tuesday afternoon".
        """
        if not screenshots:
            return "No activity"

        period_counts = {}
        for ss in screenshots:
            ts = ss['timestamp']
            if isinstance(ts, int):
                dt = datetime.fromtimestamp(ts)
            else:
                dt = datetime.fromisoformat(str(ts))

            day = dt.strftime('%A')
            if dt.hour < 12:
                period = 'morning'
            elif dt.hour < 17:
                period = 'afternoon'
            else:
                period = 'evening'

            key = f"{day} {period}"
            period_counts[key] = period_counts.get(key, 0) + 1

        if not period_counts:
            return "No activity"

        busiest = max(period_counts.items(), key=lambda x: x[1])
        return busiest[0]

    def _select_key_screenshots(
        self,
        screenshots: List[dict],
        summaries: List[dict],
        max_count: int
    ) -> List[dict]:
        """Select representative screenshots for the report.

        Strategy: Pick one screenshot per summary, preferring unique apps.

        Args:
            screenshots: All screenshots in range.
            summaries: All summaries in range.
            max_count: Maximum screenshots to select.

        Returns:
            List of selected screenshot dicts.
        """
        if not screenshots:
            return []

        selected = []
        seen_apps = set()

        # Pick one from each summary, preferring unique apps
        for summary in summaries:
            ss_ids = summary.get('screenshot_ids', [])
            if isinstance(ss_ids, str):
                ss_ids = json.loads(ss_ids)

            for ss in screenshots:
                if ss['id'] in ss_ids:
                    app = ss.get('app_name', 'Unknown')
                    if app not in seen_apps or len(selected) < max_count // 2:
                        selected.append(ss)
                        seen_apps.add(app)
                        break

            if len(selected) >= max_count:
                break

        # Fill remaining with evenly spaced screenshots
        if len(selected) < max_count and screenshots:
            remaining = max_count - len(selected)
            step = len(screenshots) // max(remaining, 1)
            for i in range(0, len(screenshots), max(step, 1)):
                if screenshots[i] not in selected:
                    selected.append(screenshots[i])
                if len(selected) >= max_count:
                    break

        return selected[:max_count]

    def _generate_summary(
        self,
        summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str,
        focus_events: List[dict] = None
    ) -> Report:
        """Generate high-level summary report.

        Args:
            summaries: Existing summaries to synthesize.
            analytics: Computed analytics.
            range_description: Human-readable time range.
            focus_events: Focus events for app/window context.

        Returns:
            Report with executive summary and sections.
        """
        if not summaries:
            return Report(
                title=f"Activity Report: {range_description}",
                time_range=range_description,
                generated_at=datetime.now(),
                executive_summary="No activity recorded during this period.",
                sections=[],
                analytics=analytics,
                key_screenshots=[],
                raw_summaries=[]
            )

        summary_texts = [s['summary'] for s in summaries if s.get('summary')]

        # Build app/window usage context from focus events
        app_usage_context = self._build_focus_context(focus_events) if focus_events else ""

        # Generate executive summary using LLM
        if self.summarizer and self.summarizer.is_available():
            prompt = f"""Synthesize these activity summaries into a BRIEF executive summary.
Time period: {range_description}
Total active time: {analytics.total_active_minutes} minutes
Top applications: {', '.join(a['name'] for a in analytics.top_apps[:5])}
{app_usage_context}

Individual activity summaries:
{chr(10).join(f"- {s}" for s in summary_texts[:20])}

Write 2-4 sentences total covering:
- Main focus areas and key accomplishments
- Specific project names (if identifiable)

RULES:
- Be extremely concise - no fluff or filler words
- Use specific project/file names from the summaries
- Do NOT assume unrelated activities are connected"""

            executive_summary = self.summarizer.generate_text(prompt)
        else:
            logger.warning(
                "LLM not available for executive summary, using fallback. "
                "Check Ollama service or model configuration."
            )
            executive_summary = self._fallback_executive_summary(summary_texts, analytics)

        sections = self._group_into_sections(summaries)

        return Report(
            title=f"Activity Report: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=executive_summary,
            sections=sections,
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=summaries
        )

    def _build_focus_context(self, focus_events: List[dict]) -> str:
        """Build app/window usage context string from focus events.

        Args:
            focus_events: List of focus event dicts.

        Returns:
            Formatted string with app/window time breakdown.
        """
        if not focus_events:
            return ""

        # Aggregate time by app
        app_time = {}
        for event in focus_events:
            app = event.get('app_name', 'Unknown') or 'Unknown'
            duration = event.get('duration_seconds', 0) or 0
            app_time[app] = app_time.get(app, 0) + duration

        if not app_time:
            return ""

        # Sort by time spent
        sorted_apps = sorted(app_time.items(), key=lambda x: -x[1])[:10]

        lines = ["\nApp/window usage breakdown:"]
        for app, seconds in sorted_apps:
            mins = seconds // 60
            if mins > 0:
                lines.append(f"  - {app}: {mins} min")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _summary_duration_seconds(self, summary: dict) -> int:
        """Calculate duration of a summary in seconds."""
        start = summary.get('start_time')
        end = summary.get('end_time')

        if not start or not end:
            return 0

        try:
            if isinstance(start, datetime):
                start_dt = start
            else:
                start_dt = datetime.fromisoformat(str(start))

            if isinstance(end, datetime):
                end_dt = end
            else:
                end_dt = datetime.fromisoformat(str(end))

            return int((end_dt - start_dt).total_seconds())
        except Exception:
            return 0

    def _generate_detailed(
        self,
        summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str,
        start: datetime,
        end: datetime,
        focus_events: List[dict] = None
    ) -> Report:
        """Generate day-by-day detailed report.

        Args:
            summaries: Existing summaries.
            analytics: Computed analytics.
            range_description: Human-readable time range.
            start: Start datetime.
            end: End datetime.
            focus_events: Focus events for app/window context.

        Returns:
            Report with sections for each day.
        """
        # Group summaries by day
        by_day = {}
        for s in summaries:
            ts = s.get('start_time', '')
            if isinstance(ts, datetime):
                day_key = ts.strftime('%Y-%m-%d')
            else:
                try:
                    day_key = datetime.fromisoformat(str(ts)).strftime('%Y-%m-%d')
                except Exception:
                    continue
            if day_key not in by_day:
                by_day[day_key] = []
            by_day[day_key].append(s)

        sections = []
        for day in sorted(by_day.keys()):
            day_summaries = by_day[day]
            day_dt = datetime.strptime(day, '%Y-%m-%d')

            summary_texts = [s['summary'] for s in day_summaries if s.get('summary')]

            if summary_texts:
                if self.summarizer and self.summarizer.is_available():
                    day_content = self.summarizer.generate_text(
                        f"Summarize this day's activities in 2-3 sentences:\n" +
                        "\n".join(f"- {s}" for s in summary_texts)
                    )
                else:
                    day_content = " ".join(summary_texts[:3])
            else:
                day_content = "No detailed activity recorded."

            sections.append(ReportSection(
                title=day_dt.strftime('%A, %B %d'),
                content=day_content,
                screenshots=[]
            ))

        # Executive summary for detailed report
        all_texts = [s['summary'] for s in summaries if s.get('summary')]
        if all_texts and self.summarizer and self.summarizer.is_available():
            executive_summary = self.summarizer.generate_text(
                f"Write a brief overview of the week based on these activities:\n" +
                "\n".join(f"- {s}" for s in all_texts[:20])
            )
        else:
            if all_texts and (not self.summarizer or not self.summarizer.is_available()):
                logger.warning(
                    "LLM not available for detailed report summary, using fallback. "
                    "Check Ollama service or model configuration."
                )
            executive_summary = self._fallback_executive_summary(all_texts, analytics)

        return Report(
            title=f"Detailed Report: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=executive_summary,
            sections=sections,
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=summaries
        )

    def _generate_standup(
        self,
        summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str,
        focus_events: List[dict] = None
    ) -> Report:
        """Generate brief standup-style report.

        Args:
            summaries: Existing summaries.
            analytics: Computed analytics.
            range_description: Human-readable time range.
            focus_events: Focus events for app/window context.

        Returns:
            Report with standup-formatted content.
        """
        summary_texts = [s['summary'] for s in summaries if s.get('summary')]

        if not summary_texts:
            content = "No activity to report."
        elif self.summarizer and self.summarizer.is_available():
            prompt = f"""Convert these activity summaries into a brief standup update.
Format:
- What I worked on: (2-3 bullet points)
- Key accomplishments: (1-2 items)
- Currently focused on: (1 item)

Activities:
{chr(10).join(f"- {s}" for s in summary_texts)}

Keep it concise and actionable."""

            content = self.summarizer.generate_text(prompt)
        else:
            # Fallback standup format
            content = "What I worked on:\n"
            for text in summary_texts[:3]:
                content += f"- {text[:100]}...\n" if len(text) > 100 else f"- {text}\n"

        return Report(
            title=f"Standup: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=content,
            sections=[],
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=summaries
        )

    def _group_into_sections(self, summaries: List[dict]) -> List[ReportSection]:
        """Group related summaries into thematic sections.

        Args:
            summaries: List of summary dicts.

        Returns:
            List of ReportSection objects.
        """
        if len(summaries) < 3:
            return []

        summary_texts = [s['summary'] for s in summaries if s.get('summary')]

        if not summary_texts or not self.summarizer or not self.summarizer.is_available():
            return []

        prompt = f"""Group these activities into 2-4 thematic categories.
For each category, provide a title and 1-sentence summary.

Activities:
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(summary_texts))}

Format your response as:
## Category Name
Brief description of work in this category.

## Another Category
Brief description."""

        response = self.summarizer.generate_text(prompt)

        # Parse response into sections
        sections = []
        current_title = None
        current_content = []

        for line in response.split('\n'):
            if line.startswith('## '):
                if current_title:
                    sections.append(ReportSection(
                        title=current_title,
                        content=' '.join(current_content)
                    ))
                current_title = line[3:].strip()
                current_content = []
            elif line.strip() and current_title:
                current_content.append(line.strip())

        if current_title:
            sections.append(ReportSection(
                title=current_title,
                content=' '.join(current_content)
            ))

        return sections

    def _fallback_executive_summary(
        self,
        summary_texts: List[str],
        analytics: ReportAnalytics
    ) -> str:
        """Generate a fallback executive summary without LLM.

        Args:
            summary_texts: Individual summary texts.
            analytics: Computed analytics.

        Returns:
            Simple formatted summary string.
        """
        if not summary_texts:
            return "No activity recorded during this period."

        lines = [
            f"During this period, {analytics.total_active_minutes} minutes of activity were recorded "
            f"across {analytics.total_sessions} sessions.",
            "",
            f"Top applications used: {', '.join(a['name'] for a in analytics.top_apps[:3])}.",
            "",
            "Key activities:",
        ]

        for text in summary_texts[:5]:
            snippet = text[:150] + '...' if len(text) > 150 else text
            lines.append(f"- {snippet}")

        return "\n".join(lines)

    def generate_daily_report(
        self,
        date_str: str,
        is_regeneration: bool = False
    ) -> Optional[dict]:
        """Generate and cache a daily report for a specific date.

        This generates a summary report for a single day and caches it
        for fast synthesis into larger reports (weekly, monthly, custom).

        Args:
            date_str: Date in YYYY-MM-DD format.
            is_regeneration: If True, regenerate even if exists.

        Returns:
            Cached report dict if generated successfully, None if no activity.
        """
        import time

        # Parse date and create time range
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            logger.error(f"Invalid date format: {date_str}")
            return None

        start = datetime.combine(date.date(), datetime.min.time())
        end = datetime.combine(date.date(), datetime.max.time())

        # Check if already cached (skip if regenerating)
        if not is_regeneration:
            existing = self.storage.get_cached_report('daily', date_str)
            if existing:
                logger.debug(f"Daily report for {date_str} already cached")
                return existing

        # Get summaries for this day
        summaries = self.storage.get_summaries_in_range(start, end)
        if not summaries:
            logger.debug(f"No summaries for {date_str}, skipping")
            return None

        logger.info(f"Generating daily report for {date_str} ({len(summaries)} summaries)")

        start_time = time.time()

        # Get other data for analytics
        screenshots = self.storage.get_screenshots_in_range(start, end)
        sessions = self.storage.get_sessions_in_range(start, end)

        # Get focus events, with fallback for older data without session_id
        focus_events = self.storage.get_focus_events_in_range(start, end, require_session=True)
        if not focus_events and screenshots:
            focus_events = self.storage.get_focus_events_in_range(start, end, require_session=False)

        # Compute analytics
        analytics = self._compute_analytics(screenshots, sessions, start, end)

        # Build prompt for executive summary
        summary_texts = [s['summary'] for s in summaries if s.get('summary')]
        app_usage_context = self._build_focus_context(focus_events) if focus_events else ""

        prompt_text = None
        explanation = None
        tags = []
        confidence = None

        if self.summarizer and self.summarizer.is_available() and summary_texts:
            # Build the prompt (stored for transparency)
            prompt_text = f"""Summarize the day's activities BRIEFLY.
Date: {date.strftime('%A, %B %d, %Y')}
Total active time: {analytics.total_active_minutes} minutes
Top apps: {', '.join(a['name'] for a in analytics.top_apps[:5])}
{app_usage_context}

Activity summaries:
{chr(10).join(f"- {s}" for s in summary_texts[:15])}

Write 2-4 sentences covering main accomplishments and key projects.
Be extremely concise. Use specific project/file names from the summaries.
Do NOT assume unrelated activities are connected.

After your summary, provide:
EXPLANATION: Brief explanation of your reasoning
CONFIDENCE: A number 0.0-1.0 indicating confidence
TAGS: Comma-separated activity tags (e.g., coding, research, meetings)"""

            response = self.summarizer.generate_text(prompt_text)
            executive_summary, explanation, confidence, tags = self._parse_structured_response(response)
            model_used = self.config.config.summarization.model
        else:
            if summary_texts and (not self.summarizer or not self.summarizer.is_available()):
                logger.warning(
                    "LLM not available for daily report summary, using fallback. "
                    "Check Ollama service or model configuration."
                )
            executive_summary = self._fallback_executive_summary(summary_texts, analytics)
            model_used = None

        inference_time_ms = int((time.time() - start_time) * 1000)

        # Convert analytics to dict for storage
        analytics_dict = {
            'total_active_minutes': analytics.total_active_minutes,
            'total_sessions': analytics.total_sessions,
            'top_apps': analytics.top_apps,
            'top_windows': analytics.top_windows,
            'activity_by_hour': analytics.activity_by_hour,
            'activity_by_day': analytics.activity_by_day,
            'busiest_period': analytics.busiest_period,
        }

        # Save to cache with all metadata
        summary_ids = [s['id'] for s in summaries]
        child_summary_ids = [s['id'] for s in summaries]  # For daily, children are threshold summaries

        self.storage.save_cached_report(
            period_type='daily',
            period_date=date_str,
            start_time=start,
            end_time=end,
            executive_summary=executive_summary,
            sections=[],  # Daily reports are just summaries, no sections
            analytics=analytics_dict,
            summary_ids=summary_ids,
            model_used=model_used,
            inference_time_ms=inference_time_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags if tags else None,
            confidence=confidence,
            child_summary_ids=child_summary_ids,
            is_regeneration=is_regeneration,
        )

        logger.info(f"Cached daily report for {date_str} ({inference_time_ms}ms)")

        return self.storage.get_cached_report('daily', date_str)

    def _parse_structured_response(self, response: str) -> tuple:
        """Parse LLM response with optional structured fields.

        Args:
            response: Raw LLM response text.

        Returns:
            Tuple of (summary, explanation, confidence, tags)
        """
        lines = response.strip().split('\n')
        summary_lines = []
        explanation = None
        confidence = None
        tags = []

        for line in lines:
            line_upper = line.upper().strip()
            if line_upper.startswith('EXPLANATION:'):
                explanation = line.split(':', 1)[1].strip() if ':' in line else None
            elif line_upper.startswith('CONFIDENCE:'):
                try:
                    conf_str = line.split(':', 1)[1].strip()
                    confidence = float(conf_str)
                    confidence = max(0.0, min(1.0, confidence))
                except (ValueError, IndexError):
                    confidence = None
            elif line_upper.startswith('TAGS:'):
                try:
                    tags_str = line.split(':', 1)[1].strip()
                    tags = [t.strip() for t in tags_str.split(',') if t.strip()]
                except (ValueError, IndexError):
                    tags = []
            else:
                summary_lines.append(line)

        summary = '\n'.join(summary_lines).strip()
        return summary, explanation, confidence, tags

    def generate_missing_daily_reports(self, days_back: int = 7) -> int:
        """Generate cached daily reports for recent days that are missing.

        Args:
            days_back: How many days to look back.

        Returns:
            Number of reports generated.
        """
        missing_dates = self.storage.get_missing_daily_reports(days_back)
        if not missing_dates:
            logger.debug("No missing daily reports")
            return 0

        logger.info(f"Generating {len(missing_dates)} missing daily reports")
        count = 0
        for date_str in missing_dates:
            result = self.generate_daily_report(date_str)
            if result:
                count += 1

        return count

    def generate_weekly_report(
        self,
        week_str: str,
        is_regeneration: bool = False
    ) -> Optional[dict]:
        """Generate and cache a weekly report for a specific ISO week.

        This synthesizes daily summaries from the week into a weekly summary.

        Args:
            week_str: ISO week in YYYY-Www format (e.g., "2024-W52").
            is_regeneration: If True, regenerate even if exists.

        Returns:
            Cached report dict if generated successfully, None if no data.
        """
        import time

        # Parse ISO week string
        try:
            # Extract year and week number
            parts = week_str.split('-W')
            if len(parts) != 2:
                raise ValueError("Invalid week format")
            year = int(parts[0])
            week_num = int(parts[1])

            # Calculate Monday of the given ISO week
            from datetime import date
            jan_4 = date(year, 1, 4)  # Jan 4 is always in week 1
            week_1_monday = jan_4 - timedelta(days=jan_4.weekday())
            week_start = week_1_monday + timedelta(weeks=week_num - 1)
            week_end = week_start + timedelta(days=6)

            start = datetime.combine(week_start, datetime.min.time())
            end = datetime.combine(week_end, datetime.max.time())
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid week format: {week_str} ({e})")
            return None

        # Check if already cached (skip if regenerating)
        if not is_regeneration:
            existing = self.storage.get_cached_report('weekly', week_str)
            if existing:
                logger.debug(f"Weekly report for {week_str} already cached")
                return existing

        # Get daily reports for this week
        start_date_str = week_start.strftime('%Y-%m-%d')
        end_date_str = week_end.strftime('%Y-%m-%d')
        daily_reports = self.storage.get_cached_reports_in_range('daily', start_date_str, end_date_str)

        if not daily_reports:
            logger.debug(f"No daily reports for {week_str}, skipping")
            return None

        logger.info(f"Generating weekly report for {week_str} ({len(daily_reports)} daily reports)")

        start_time = time.time()

        # Aggregate analytics from daily reports
        analytics = self._aggregate_cached_analytics(daily_reports)

        # Build prompt for weekly synthesis
        daily_summaries = []
        child_ids = []
        all_tags = []
        for dr in sorted(daily_reports, key=lambda x: x['period_date']):
            if dr.get('executive_summary'):
                date = datetime.strptime(dr['period_date'], '%Y-%m-%d')
                daily_summaries.append({
                    'date': date,
                    'date_str': date.strftime('%A, %B %d'),
                    'summary': dr['executive_summary']
                })
                child_ids.append(dr['id'])
                if dr.get('tags'):
                    all_tags.extend(dr['tags'])

        prompt_text = None
        explanation = None
        tags = list(set(all_tags))[:10]  # Deduplicate, limit to 10
        confidence = None

        if self.summarizer and self.summarizer.is_available() and daily_summaries:
            prompt_text = f"""Synthesize these daily summaries into a weekly summary.
Week: {week_start.strftime('%B %d')} to {week_end.strftime('%B %d, %Y')}
Total active time: {analytics.total_active_minutes // 60} hours across {len(daily_reports)} days
Top apps: {', '.join(a['name'] for a in analytics.top_apps[:5])}

Daily summaries:
{chr(10).join(f"**{d['date_str']}**: {d['summary'][:300]}" for d in daily_summaries)}

Write 4-6 sentences covering main themes, patterns, and key accomplishments.
Identify any recurring work patterns or project focus areas.
Use specific project names from summaries.

After your summary, provide:
EXPLANATION: Brief explanation of your reasoning
CONFIDENCE: A number 0.0-1.0 indicating confidence
TAGS: Comma-separated activity tags (e.g., coding, research, meetings)"""

            response = self.summarizer.generate_text(prompt_text)
            executive_summary, explanation, confidence, parsed_tags = self._parse_structured_response(response)
            if parsed_tags:
                tags = list(set(tags + parsed_tags))[:10]
            model_used = self.config.config.summarization.model
        else:
            executive_summary = self._fallback_synthesized_summary(daily_summaries, analytics)
            model_used = None

        inference_time_ms = int((time.time() - start_time) * 1000)

        # Convert analytics to dict for storage
        analytics_dict = {
            'total_active_minutes': analytics.total_active_minutes,
            'total_sessions': analytics.total_sessions,
            'top_apps': analytics.top_apps,
            'top_windows': analytics.top_windows,
            'activity_by_hour': analytics.activity_by_hour,
            'activity_by_day': analytics.activity_by_day,
            'busiest_period': analytics.busiest_period,
        }

        # Save to cache
        self.storage.save_cached_report(
            period_type='weekly',
            period_date=week_str,
            start_time=start,
            end_time=end,
            executive_summary=executive_summary,
            sections=[],
            analytics=analytics_dict,
            summary_ids=None,
            model_used=model_used,
            inference_time_ms=inference_time_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags if tags else None,
            confidence=confidence,
            child_summary_ids=child_ids,
            is_regeneration=is_regeneration,
        )

        logger.info(f"Cached weekly report for {week_str} ({inference_time_ms}ms)")

        return self.storage.get_cached_report('weekly', week_str)

    def generate_monthly_report(
        self,
        month_str: str,
        is_regeneration: bool = False
    ) -> Optional[dict]:
        """Generate and cache a monthly report for a specific month.

        This synthesizes weekly summaries from the month into a monthly summary.

        Args:
            month_str: Month in YYYY-MM format (e.g., "2024-12").
            is_regeneration: If True, regenerate even if exists.

        Returns:
            Cached report dict if generated successfully, None if no data.
        """
        import time
        import calendar

        # Parse month string
        try:
            year, month = map(int, month_str.split('-'))
            _, last_day = calendar.monthrange(year, month)
            month_start = datetime(year, month, 1).date()
            month_end = datetime(year, month, last_day).date()

            start = datetime.combine(month_start, datetime.min.time())
            end = datetime.combine(month_end, datetime.max.time())
        except (ValueError, IndexError) as e:
            logger.error(f"Invalid month format: {month_str} ({e})")
            return None

        # Check if already cached (skip if regenerating)
        if not is_regeneration:
            existing = self.storage.get_cached_report('monthly', month_str)
            if existing:
                logger.debug(f"Monthly report for {month_str} already cached")
                return existing

        # Get weekly reports that overlap with this month
        # Calculate all ISO weeks in this month
        weekly_reports = []
        current_date = month_start
        seen_weeks = set()
        while current_date <= month_end:
            iso_year, iso_week, _ = current_date.isocalendar()
            week_str = f"{iso_year}-W{iso_week:02d}"
            if week_str not in seen_weeks:
                seen_weeks.add(week_str)
                report = self.storage.get_cached_report('weekly', week_str)
                if report:
                    weekly_reports.append(report)
            current_date += timedelta(days=7)

        if not weekly_reports:
            # Fall back to daily reports if no weekly reports
            logger.debug(f"No weekly reports for {month_str}, trying daily reports")
            start_date_str = month_start.strftime('%Y-%m-%d')
            end_date_str = month_end.strftime('%Y-%m-%d')
            daily_reports = self.storage.get_cached_reports_in_range('daily', start_date_str, end_date_str)
            if not daily_reports:
                logger.debug(f"No reports for {month_str}, skipping")
                return None

            # Synthesize from daily reports instead
            return self._generate_monthly_from_daily(month_str, start, end, daily_reports, is_regeneration)

        logger.info(f"Generating monthly report for {month_str} ({len(weekly_reports)} weekly reports)")

        start_time = time.time()

        # Aggregate analytics from weekly reports
        analytics = self._aggregate_cached_analytics(weekly_reports)

        # Build prompt for monthly synthesis
        week_summaries = []
        child_ids = []
        all_tags = []
        for wr in sorted(weekly_reports, key=lambda x: x['period_date']):
            if wr.get('executive_summary'):
                week_summaries.append({
                    'week': wr['period_date'],
                    'summary': wr['executive_summary']
                })
                child_ids.append(wr['id'])
                if wr.get('tags'):
                    all_tags.extend(wr['tags'])

        prompt_text = None
        explanation = None
        tags = list(set(all_tags))[:10]
        confidence = None

        month_name = datetime(year, month, 1).strftime('%B %Y')

        if self.summarizer and self.summarizer.is_available() and week_summaries:
            prompt_text = f"""Synthesize these weekly summaries into a monthly summary.
Month: {month_name}
Total active time: {analytics.total_active_minutes // 60} hours across {len(weekly_reports)} weeks
Top apps: {', '.join(a['name'] for a in analytics.top_apps[:5])}

Weekly summaries:
{chr(10).join(f"**{w['week']}**: {w['summary'][:400]}" for w in week_summaries)}

Write 5-8 sentences covering:
- Major themes and recurring patterns
- Key accomplishments and milestones
- Project focus areas across the month

Use specific project names from summaries.

After your summary, provide:
EXPLANATION: Brief explanation of your reasoning
CONFIDENCE: A number 0.0-1.0 indicating confidence
TAGS: Comma-separated activity tags (e.g., coding, research, meetings)"""

            response = self.summarizer.generate_text(prompt_text)
            executive_summary, explanation, confidence, parsed_tags = self._parse_structured_response(response)
            if parsed_tags:
                tags = list(set(tags + parsed_tags))[:10]
            model_used = self.config.config.summarization.model
        else:
            executive_summary = f"Monthly activity for {month_name}: {analytics.total_active_minutes // 60} hours across {analytics.total_sessions} sessions."
            model_used = None

        inference_time_ms = int((time.time() - start_time) * 1000)

        # Convert analytics to dict for storage
        analytics_dict = {
            'total_active_minutes': analytics.total_active_minutes,
            'total_sessions': analytics.total_sessions,
            'top_apps': analytics.top_apps,
            'top_windows': analytics.top_windows,
            'activity_by_hour': analytics.activity_by_hour,
            'activity_by_day': analytics.activity_by_day,
            'busiest_period': analytics.busiest_period,
        }

        # Save to cache
        self.storage.save_cached_report(
            period_type='monthly',
            period_date=month_str,
            start_time=start,
            end_time=end,
            executive_summary=executive_summary,
            sections=[],
            analytics=analytics_dict,
            summary_ids=None,
            model_used=model_used,
            inference_time_ms=inference_time_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags if tags else None,
            confidence=confidence,
            child_summary_ids=child_ids,
            is_regeneration=is_regeneration,
        )

        logger.info(f"Cached monthly report for {month_str} ({inference_time_ms}ms)")

        return self.storage.get_cached_report('monthly', month_str)

    def _generate_monthly_from_daily(
        self,
        month_str: str,
        start: datetime,
        end: datetime,
        daily_reports: List[dict],
        is_regeneration: bool = False
    ) -> Optional[dict]:
        """Generate monthly report directly from daily reports.

        Used as fallback when weekly reports aren't available.
        """
        import time

        logger.info(f"Generating monthly report for {month_str} from {len(daily_reports)} daily reports")

        start_time = time.time()

        # Aggregate analytics from daily reports
        analytics = self._aggregate_cached_analytics(daily_reports)

        # Build prompt for monthly synthesis
        daily_summaries = []
        child_ids = []
        all_tags = []
        for dr in sorted(daily_reports, key=lambda x: x['period_date']):
            if dr.get('executive_summary'):
                date = datetime.strptime(dr['period_date'], '%Y-%m-%d')
                daily_summaries.append({
                    'date': date,
                    'date_str': date.strftime('%b %d'),
                    'summary': dr['executive_summary']
                })
                child_ids.append(dr['id'])
                if dr.get('tags'):
                    all_tags.extend(dr['tags'])

        prompt_text = None
        explanation = None
        tags = list(set(all_tags))[:10]
        confidence = None

        year, month = map(int, month_str.split('-'))
        month_name = datetime(year, month, 1).strftime('%B %Y')

        if self.summarizer and self.summarizer.is_available() and daily_summaries:
            # Limit to most significant days to avoid context overflow
            top_summaries = sorted(daily_summaries, key=lambda x: len(x['summary']), reverse=True)[:15]
            top_summaries = sorted(top_summaries, key=lambda x: x['date'])

            prompt_text = f"""Synthesize these daily summaries into a monthly summary.
Month: {month_name}
Total active time: {analytics.total_active_minutes // 60} hours across {len(daily_reports)} days
Top apps: {', '.join(a['name'] for a in analytics.top_apps[:5])}

Daily summaries (representative days):
{chr(10).join(f"**{d['date_str']}**: {d['summary'][:200]}" for d in top_summaries)}

Write 5-8 sentences covering major themes, key accomplishments, and project focus areas.
Use specific project names from summaries.

After your summary, provide:
EXPLANATION: Brief explanation of your reasoning
CONFIDENCE: A number 0.0-1.0 indicating confidence
TAGS: Comma-separated activity tags (e.g., coding, research, meetings)"""

            response = self.summarizer.generate_text(prompt_text)
            executive_summary, explanation, confidence, parsed_tags = self._parse_structured_response(response)
            if parsed_tags:
                tags = list(set(tags + parsed_tags))[:10]
            model_used = self.config.config.summarization.model
        else:
            executive_summary = f"Monthly activity for {month_name}: {analytics.total_active_minutes // 60} hours across {analytics.total_sessions} sessions."
            model_used = None

        inference_time_ms = int((time.time() - start_time) * 1000)

        # Convert analytics to dict for storage
        analytics_dict = {
            'total_active_minutes': analytics.total_active_minutes,
            'total_sessions': analytics.total_sessions,
            'top_apps': analytics.top_apps,
            'top_windows': analytics.top_windows,
            'activity_by_hour': analytics.activity_by_hour,
            'activity_by_day': analytics.activity_by_day,
            'busiest_period': analytics.busiest_period,
        }

        # Save to cache
        self.storage.save_cached_report(
            period_type='monthly',
            period_date=month_str,
            start_time=start,
            end_time=end,
            executive_summary=executive_summary,
            sections=[],
            analytics=analytics_dict,
            summary_ids=None,
            model_used=model_used,
            inference_time_ms=inference_time_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags if tags else None,
            confidence=confidence,
            child_summary_ids=child_ids,
            is_regeneration=is_regeneration,
        )

        logger.info(f"Cached monthly report for {month_str} ({inference_time_ms}ms)")

        return self.storage.get_cached_report('monthly', month_str)

    def generate_missing_weekly_reports(self, weeks_back: int = 4) -> int:
        """Generate cached weekly reports for recent weeks that are missing.

        Args:
            weeks_back: How many weeks to look back.

        Returns:
            Number of reports generated.
        """
        missing_weeks = self.storage.get_missing_weekly_reports(weeks_back)
        if not missing_weeks:
            logger.debug("No missing weekly reports")
            return 0

        logger.info(f"Generating {len(missing_weeks)} missing weekly reports")
        count = 0
        for week_str in missing_weeks:
            result = self.generate_weekly_report(week_str)
            if result:
                count += 1

        return count

    def generate_missing_monthly_reports(self, months_back: int = 3) -> int:
        """Generate cached monthly reports for recent months that are missing.

        Args:
            months_back: How many months to look back.

        Returns:
            Number of reports generated.
        """
        missing_months = self.storage.get_missing_monthly_reports(months_back)
        if not missing_months:
            logger.debug("No missing monthly reports")
            return 0

        logger.info(f"Generating {len(missing_months)} missing monthly reports")
        count = 0
        for month_str in missing_months:
            result = self.generate_monthly_report(month_str)
            if result:
                count += 1

        return count

    def generate_from_cached(
        self,
        time_range: str,
        report_type: str = "summary",
        include_screenshots: bool = True,
        max_screenshots: int = 10
    ) -> Optional[Report]:
        """Generate a report by synthesizing cached daily reports.

        This is much faster than generate() as it uses pre-computed daily
        summaries instead of re-processing all individual summaries.

        Falls back to None if cached daily reports are not available for
        the requested range. Caller should then use generate() instead.

        Args:
            time_range: Natural language time range (e.g., "last week").
            report_type: Type of report - "summary", "detailed", or "standup".
            include_screenshots: Whether to include key screenshots.
            max_screenshots: Maximum number of screenshots to include.

        Returns:
            Report object if cached data available, None otherwise.
        """
        # Parse time range
        start, end = self.time_parser.parse(time_range)

        # Validate time range
        self._validate_time_range(start, end)

        range_description = self.time_parser.describe_range(start, end)

        # Get all days in range
        days_in_range = []
        current = start.date()
        end_date = end.date()
        while current <= end_date:
            days_in_range.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

        if not days_in_range:
            return None

        # Get cached daily reports for these days
        cached_reports = self.storage.get_cached_reports_in_range('daily', start, end)
        if not cached_reports:
            logger.debug(f"No cached daily reports for {range_description}")
            return None

        # Check coverage - we need reports for most days
        cached_dates = {r['period_date'] for r in cached_reports}
        coverage = len(cached_dates) / len(days_in_range)

        if coverage < 0.7:  # Require at least 70% coverage
            logger.debug(
                f"Insufficient cache coverage ({coverage:.0%}) for {range_description}, "
                f"have {len(cached_dates)}/{len(days_in_range)} days"
            )
            return None

        logger.info(
            f"Synthesizing {report_type} report from {len(cached_reports)} cached daily reports "
            f"({coverage:.0%} coverage)"
        )

        # Aggregate analytics from cached reports
        aggregated_analytics = self._aggregate_cached_analytics(cached_reports)

        # Collect daily summaries
        daily_summaries = []
        for cr in sorted(cached_reports, key=lambda x: x['period_date']):
            if cr.get('executive_summary'):
                date = datetime.strptime(cr['period_date'], '%Y-%m-%d')
                daily_summaries.append({
                    'date': date,
                    'date_str': date.strftime('%A, %B %d'),
                    'summary': cr['executive_summary']
                })

        # Generate report based on type
        if report_type == "standup":
            report = self._synthesize_standup(daily_summaries, aggregated_analytics, range_description)
        elif report_type == "detailed":
            report = self._synthesize_detailed(daily_summaries, aggregated_analytics, range_description)
        else:
            report = self._synthesize_summary(daily_summaries, aggregated_analytics, range_description)

        # Add screenshots if requested
        if include_screenshots:
            screenshots = self.storage.get_screenshots_in_range(start, end)
            # Get all summaries referenced by cached reports
            all_summary_ids = []
            for cr in cached_reports:
                ids = cr.get('summary_ids_json', '[]')
                if isinstance(ids, str):
                    ids = json.loads(ids)
                all_summary_ids.extend(ids)

            summaries = [
                self.storage.get_threshold_summary(sid)
                for sid in all_summary_ids[:50]  # Limit for performance
            ]
            summaries = [s for s in summaries if s]

            report.key_screenshots = self._select_key_screenshots(
                screenshots, summaries, max_screenshots
            )

        return report

    def _aggregate_cached_analytics(self, cached_reports: List[dict]) -> ReportAnalytics:
        """Aggregate analytics from multiple cached daily reports.

        Args:
            cached_reports: List of cached report dicts.

        Returns:
            Combined ReportAnalytics.
        """
        total_minutes = 0
        total_sessions = 0
        app_minutes = {}
        window_minutes = {}
        activity_by_hour = [0] * 24
        activity_by_day = []

        for cr in cached_reports:
            analytics_json = cr.get('analytics_json', '{}')
            if isinstance(analytics_json, str):
                analytics = json.loads(analytics_json)
            else:
                analytics = analytics_json

            total_minutes += analytics.get('total_active_minutes', 0)
            total_sessions += analytics.get('total_sessions', 0)

            # Aggregate app usage
            for app in analytics.get('top_apps', []):
                name = app.get('name', 'Unknown')
                mins = app.get('minutes', 0)
                app_minutes[name] = app_minutes.get(name, 0) + mins

            # Aggregate window usage
            for win in analytics.get('top_windows', []):
                title = win.get('title', 'Unknown')
                mins = win.get('minutes', 0)
                window_minutes[title] = window_minutes.get(title, 0) + mins

            # Aggregate hourly activity
            for i, mins in enumerate(analytics.get('activity_by_hour', [])):
                if i < 24:
                    activity_by_hour[i] += mins

            # Collect daily activity
            for day in analytics.get('activity_by_day', []):
                activity_by_day.append(day)

        # Sort and limit top apps/windows
        total_app_mins = sum(app_minutes.values()) or 1
        top_apps = sorted([
            {
                'name': app,
                'minutes': int(mins),
                'percentage': round(mins / total_app_mins * 100, 1)
            }
            for app, mins in app_minutes.items()
        ], key=lambda x: -x['minutes'])[:10]

        top_windows = sorted([
            {'title': title, 'minutes': int(mins)}
            for title, mins in window_minutes.items()
        ], key=lambda x: -x['minutes'])[:10]

        # Find busiest period from aggregated data
        busiest_period = "No activity"
        if activity_by_day:
            busiest_day = max(activity_by_day, key=lambda x: x.get('minutes', 0))
            busiest_period = busiest_day.get('date', 'Unknown')

        return ReportAnalytics(
            total_active_minutes=total_minutes,
            total_sessions=total_sessions,
            top_apps=top_apps,
            top_windows=top_windows,
            activity_by_hour=[int(h) for h in activity_by_hour],
            activity_by_day=sorted(activity_by_day, key=lambda x: x.get('date', '')),
            busiest_period=busiest_period
        )

    def _synthesize_summary(
        self,
        daily_summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str
    ) -> Report:
        """Synthesize a summary report from cached daily summaries.

        Args:
            daily_summaries: List of daily summary dicts with date and summary.
            analytics: Aggregated analytics.
            range_description: Human-readable time range.

        Returns:
            Report with executive summary synthesized from daily reports.
        """
        if not daily_summaries:
            return Report(
                title=f"Activity Report: {range_description}",
                time_range=range_description,
                generated_at=datetime.now(),
                executive_summary="No activity recorded during this period.",
                sections=[],
                analytics=analytics,
                key_screenshots=[],
                raw_summaries=[]
            )

        # Build prompt for synthesizing daily summaries
        if self.summarizer and self.summarizer.is_available():
            prompt = f"""Synthesize these daily summaries into a BRIEF executive summary.
Time period: {range_description}
Total active time: {analytics.total_active_minutes} minutes across {len(daily_summaries)} days
Top apps: {', '.join(a['name'] for a in analytics.top_apps[:5])}

Daily summaries:
{chr(10).join(f"**{d['date_str']}**: {d['summary'][:200]}" for d in daily_summaries)}

Write 3-5 sentences covering main themes and key projects.
Be extremely concise. Use actual project names from summaries.
Do NOT assume different days are related unless clearly same project."""

            executive_summary = self.summarizer.generate_text(prompt)
        else:
            logger.warning(
                "LLM not available for synthesized report summary, using fallback. "
                "Check Ollama service or model configuration."
            )
            executive_summary = self._fallback_synthesized_summary(daily_summaries, analytics)

        # Create sections from daily summaries
        sections = [
            ReportSection(
                title=d['date_str'],
                content=d['summary'][:500] + '...' if len(d['summary']) > 500 else d['summary']
            )
            for d in daily_summaries
        ]

        return Report(
            title=f"Activity Report: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=executive_summary,
            sections=sections,
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=[]
        )

    def _synthesize_detailed(
        self,
        daily_summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str
    ) -> Report:
        """Synthesize a detailed report from cached daily summaries.

        Args:
            daily_summaries: List of daily summary dicts.
            analytics: Aggregated analytics.
            range_description: Human-readable time range.

        Returns:
            Report with detailed day-by-day breakdown.
        """
        # For detailed reports, include full daily summaries as sections
        sections = [
            ReportSection(
                title=d['date_str'],
                content=d['summary']
            )
            for d in daily_summaries
        ]

        # Brief executive overview
        if self.summarizer and self.summarizer.is_available() and daily_summaries:
            prompt = f"""Write a brief overview paragraph for a detailed activity report.
Time period: {range_description}
Number of days: {len(daily_summaries)}
Total active time: {analytics.total_active_minutes} minutes

Keep it to 2-3 sentences summarizing the overall focus and accomplishments."""

            executive_summary = self.summarizer.generate_text(prompt)
        else:
            executive_summary = f"Detailed activity report covering {len(daily_summaries)} days with {analytics.total_active_minutes} minutes of activity."

        return Report(
            title=f"Detailed Report: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=executive_summary,
            sections=sections,
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=[]
        )

    def _synthesize_standup(
        self,
        daily_summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str
    ) -> Report:
        """Synthesize a standup report from cached daily summaries.

        Args:
            daily_summaries: List of daily summary dicts.
            analytics: Aggregated analytics.
            range_description: Human-readable time range.

        Returns:
            Report in standup format.
        """
        if not daily_summaries:
            content = "No activity to report."
        elif self.summarizer and self.summarizer.is_available():
            # Use most recent day's summary for standup
            recent_summaries = daily_summaries[-3:]  # Last 3 days
            prompt = f"""Convert these recent activity summaries into a standup update.
Format:
- What I worked on: (2-3 bullet points)
- Key accomplishments: (1-2 items)
- Currently focused on: (1 item)

Recent activities:
{chr(10).join(f"**{d['date_str']}**: {d['summary'][:200]}" for d in recent_summaries)}

Keep it concise and actionable."""

            content = self.summarizer.generate_text(prompt)
        else:
            content = "What I worked on:\n"
            for d in daily_summaries[-3:]:
                content += f"- {d['date_str']}: {d['summary'][:100]}...\n"

        return Report(
            title=f"Standup: {range_description}",
            time_range=range_description,
            generated_at=datetime.now(),
            executive_summary=content,
            sections=[],
            analytics=analytics,
            key_screenshots=[],
            raw_summaries=[]
        )

    def _fallback_synthesized_summary(
        self,
        daily_summaries: List[dict],
        analytics: ReportAnalytics
    ) -> str:
        """Generate fallback summary without LLM.

        Args:
            daily_summaries: List of daily summary dicts.
            analytics: Aggregated analytics.

        Returns:
            Simple formatted summary.
        """
        if not daily_summaries:
            return "No activity recorded during this period."

        lines = [
            f"Activity report covering {len(daily_summaries)} days with "
            f"{analytics.total_active_minutes} minutes of activity across "
            f"{analytics.total_sessions} sessions.",
            "",
            f"Top applications: {', '.join(a['name'] for a in analytics.top_apps[:3])}.",
            "",
            "Daily highlights:",
        ]

        for d in daily_summaries[:5]:
            snippet = d['summary'][:150] + '...' if len(d['summary']) > 150 else d['summary']
            lines.append(f"- {d['date_str']}: {snippet}")

        return "\n".join(lines)
