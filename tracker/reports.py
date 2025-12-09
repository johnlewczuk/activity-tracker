"""Activity Report Generator for Activity Tracker.

This module generates comprehensive activity reports for specified time ranges.
It synthesizes existing summaries into cohesive narratives and computes
analytics from screenshot and session data.

Report types supported:
- Summary: High-level overview with executive summary
- Detailed: Day-by-day breakdown
- Standup: Brief bullet points for standup meetings

Example:
    >>> from tracker.reports import ReportGenerator
    >>> generator = ReportGenerator(storage, summarizer, config)
    >>> report = generator.generate("last week", report_type="summary")
    >>> print(report.executive_summary)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, TYPE_CHECKING
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
        range_description = self.time_parser.describe_range(start, end)

        logger.info(f"Generating {report_type} report for {range_description}")

        # Gather data
        summaries = self.storage.get_summaries_in_range(start, end)
        screenshots = self.storage.get_screenshots_in_range(start, end)
        sessions = self.storage.get_sessions_in_range(start, end)

        logger.debug(
            f"Found {len(summaries)} summaries, {len(screenshots)} screenshots, "
            f"{len(sessions)} sessions"
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
            report = self._generate_standup(summaries, analytics, range_description)
        elif report_type == "detailed":
            report = self._generate_detailed(summaries, analytics, range_description, start, end)
        else:
            report = self._generate_summary(summaries, analytics, range_description)

        report.key_screenshots = key_screenshots
        report.raw_summaries = summaries
        report.analytics = analytics

        return report

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
        # Active time from sessions
        total_minutes = sum(s.get('duration_seconds', 0) // 60 for s in sessions)

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
        range_description: str
    ) -> Report:
        """Generate high-level summary report.

        Args:
            summaries: Existing summaries to synthesize.
            analytics: Computed analytics.
            range_description: Human-readable time range.

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

        # Extract summary texts
        summary_texts = [s['summary'] for s in summaries if s.get('summary')]

        # Generate executive summary using LLM
        if self.summarizer and self.summarizer.is_available():
            prompt = f"""Synthesize these activity summaries into a coherent executive summary.
Time period: {range_description}
Total active time: {analytics.total_active_minutes} minutes
Top applications: {', '.join(a['name'] for a in analytics.top_apps[:5])}

Individual activity summaries:
{chr(10).join(f"- {s}" for s in summary_texts)}

Write a 2-3 paragraph executive summary covering:
1. Main focus areas and accomplishments
2. Key projects or tasks worked on
3. Notable patterns (if any)

Be specific and use actual project names and technical terms from the summaries."""

            executive_summary = self.summarizer.generate_text(prompt)
        else:
            # Fallback if summarizer unavailable
            executive_summary = self._fallback_executive_summary(summary_texts, analytics)

        # Group summaries by theme for sections
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

    def _generate_detailed(
        self,
        summaries: List[dict],
        analytics: ReportAnalytics,
        range_description: str,
        start: datetime,
        end: datetime
    ) -> Report:
        """Generate day-by-day detailed report.

        Args:
            summaries: Existing summaries.
            analytics: Computed analytics.
            range_description: Human-readable time range.
            start: Start datetime.
            end: End datetime.

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
        range_description: str
    ) -> Report:
        """Generate brief standup-style report.

        Args:
            summaries: Existing summaries.
            analytics: Computed analytics.
            range_description: Human-readable time range.

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
