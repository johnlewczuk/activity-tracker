"""Report Export Module for Activity Tracker.

This module provides export functionality for activity reports to various
formats including Markdown, HTML, PDF, and JSON.

Supported formats:
- Markdown: Plain text with formatting
- HTML: Standalone HTML with embedded images
- PDF: Generated from HTML (requires weasyprint)
- JSON: Machine-readable data export

Example:
    >>> from tracker.report_export import ReportExporter
    >>> exporter = ReportExporter()
    >>> path = exporter.export(report, format='html')
    >>> print(f"Report saved to: {path}")
"""

from pathlib import Path
from datetime import datetime
import json
import base64
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .reports import Report

logger = logging.getLogger(__name__)


class ReportExporter:
    """Export reports to various formats.

    Handles conversion of Report objects to different file formats
    with appropriate styling and embedded content.

    Attributes:
        output_dir: Directory where exported files are saved.
    """

    def __init__(self, output_dir: Path = None):
        """Initialize ReportExporter.

        Args:
            output_dir: Directory for exported files. Defaults to
                ~/activity-tracker-data/reports.
        """
        self.output_dir = output_dir or Path.home() / 'activity-tracker-data' / 'reports'
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(self, report: "Report", format: str = 'markdown') -> Path:
        """Export report to specified format.

        Args:
            report: Report object to export.
            format: Output format - 'markdown', 'html', 'pdf', or 'json'.

        Returns:
            Path to the exported file.

        Raises:
            ValueError: If format is not supported.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_title = report.title.replace(' ', '_').replace(':', '')[:50]

        if format == 'markdown':
            return self._export_markdown(report, f"{safe_title}_{timestamp}.md")
        elif format == 'html':
            return self._export_html(report, f"{safe_title}_{timestamp}.html")
        elif format == 'pdf':
            return self._export_pdf(report, f"{safe_title}_{timestamp}.pdf")
        elif format == 'json':
            return self._export_json(report, f"{safe_title}_{timestamp}.json")
        else:
            raise ValueError(f"Unknown format: {format}")

    def _export_markdown(self, report: "Report", filename: str) -> Path:
        """Export to Markdown with image references.

        Args:
            report: Report to export.
            filename: Output filename.

        Returns:
            Path to exported file.
        """
        lines = [
            f"# {report.title}",
            "",
            f"*Generated: {report.generated_at.strftime('%B %d, %Y at %I:%M %p')}*",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            report.executive_summary,
            "",
        ]

        # Analytics section
        lines.extend([
            "## Activity Overview",
            "",
            f"- **Total Active Time:** {report.analytics.total_active_minutes // 60}h {report.analytics.total_active_minutes % 60}m",
            f"- **Sessions:** {report.analytics.total_sessions}",
            f"- **Busiest Period:** {report.analytics.busiest_period}",
            "",
            "### Top Applications",
            "",
        ])

        for app in report.analytics.top_apps[:5]:
            lines.append(f"- {app['name']}: {app['minutes']}m ({app['percentage']}%)")

        lines.append("")

        # Sections
        for section in report.sections:
            lines.extend([
                f"## {section.title}",
                "",
                section.content,
                "",
            ])

        # Key screenshots
        if report.key_screenshots:
            lines.extend([
                "## Key Screenshots",
                "",
            ])
            for i, ss in enumerate(report.key_screenshots):
                ts = ss.get('timestamp')
                if isinstance(ts, int):
                    ts_str = datetime.fromtimestamp(ts).strftime('%I:%M %p')
                elif isinstance(ts, datetime):
                    ts_str = ts.strftime('%I:%M %p')
                else:
                    ts_str = str(ts)

                window_title = ss.get('window_title', 'Unknown')[:50]
                lines.append(f"### {ts_str} - {window_title}")
                lines.append("")
                lines.append(f"![Screenshot {i+1}]({ss.get('filepath', '')})")
                lines.append("")

        content = '\n'.join(lines)
        path = self.output_dir / filename
        path.write_text(content)
        logger.info(f"Exported markdown report to {path}")
        return path

    def _export_html(self, report: "Report", filename: str) -> Path:
        """Export to standalone HTML with embedded images.

        Args:
            report: Report to export.
            filename: Output filename.

        Returns:
            Path to exported file.
        """
        # Convert screenshots to base64 for embedding
        screenshot_embeds = []
        data_dir = Path.home() / 'activity-tracker-data'

        for ss in report.key_screenshots:
            try:
                filepath = ss.get('filepath', '')
                if filepath:
                    full_path = data_dir / 'screenshots' / filepath
                    if full_path.exists():
                        with open(full_path, 'rb') as f:
                            data = base64.b64encode(f.read()).decode()

                        ts = ss.get('timestamp')
                        if isinstance(ts, int):
                            ts_str = datetime.fromtimestamp(ts).strftime('%I:%M %p')
                        elif isinstance(ts, datetime):
                            ts_str = ts.strftime('%I:%M %p')
                        else:
                            ts_str = str(ts)

                        screenshot_embeds.append({
                            'data': data,
                            'time': ts_str,
                            'title': ss.get('window_title', 'Unknown')[:50]
                        })
            except Exception as e:
                logger.debug(f"Failed to embed screenshot: {e}")

        # Generate sections HTML
        sections_html = ''.join(
            f'<div class="section"><h2>{s.title}</h2><p>{s.content}</p></div>'
            for s in report.sections
        )

        # Generate screenshots HTML
        screenshots_html = ''.join(
            f'''
            <div class="screenshot">
                <img src="data:image/webp;base64,{s["data"]}" alt="Screenshot">
                <div class="screenshot-caption">{s["time"]} - {s["title"]}</div>
            </div>
            '''
            for s in screenshot_embeds
        )

        # Generate top apps HTML
        top_apps_html = ''.join(
            f'<li><span>{a["name"]}</span><span>{a["minutes"]}m ({a["percentage"]}%)</span></li>'
            for a in report.analytics.top_apps[:5]
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report.title}</title>
    <style>
        :root {{
            --bg: #1a1a2e;
            --surface: #16213e;
            --text: #eee;
            --muted: #888;
            --accent: #4f8cff;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem;
        }}
        h1 {{ color: var(--accent); border-bottom: 2px solid var(--accent); padding-bottom: 0.5rem; }}
        h2 {{ color: var(--text); margin-top: 2rem; }}
        .meta {{ color: var(--muted); font-size: 0.9rem; }}
        .summary {{ background: var(--surface); padding: 1.5rem; border-radius: 8px; margin: 1rem 0; white-space: pre-line; }}
        .analytics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
        .stat {{ background: var(--surface); padding: 1rem; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 2rem; font-weight: bold; color: var(--accent); }}
        .stat-label {{ color: var(--muted); font-size: 0.9rem; }}
        .app-list {{ list-style: none; padding: 0; }}
        .app-list li {{ display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid var(--surface); }}
        .screenshot {{ margin: 1rem 0; }}
        .screenshot img {{ max-width: 100%; border-radius: 8px; border: 1px solid var(--surface); }}
        .screenshot-caption {{ color: var(--muted); font-size: 0.9rem; margin-top: 0.5rem; }}
        .section {{ background: var(--surface); padding: 1.5rem; border-radius: 8px; margin: 1rem 0; }}
    </style>
</head>
<body>
    <h1>{report.title}</h1>
    <p class="meta">Generated: {report.generated_at.strftime('%B %d, %Y at %I:%M %p')}</p>

    <div class="summary">
        <h2>Executive Summary</h2>
        <p>{report.executive_summary}</p>
    </div>

    <h2>Activity Overview</h2>
    <div class="analytics">
        <div class="stat">
            <div class="stat-value">{report.analytics.total_active_minutes // 60}h {report.analytics.total_active_minutes % 60}m</div>
            <div class="stat-label">Active Time</div>
        </div>
        <div class="stat">
            <div class="stat-value">{report.analytics.total_sessions}</div>
            <div class="stat-label">Sessions</div>
        </div>
        <div class="stat">
            <div class="stat-value">{len(report.analytics.top_apps)}</div>
            <div class="stat-label">Applications Used</div>
        </div>
    </div>

    <h3>Top Applications</h3>
    <ul class="app-list">
        {top_apps_html}
    </ul>

    {sections_html}

    <h2>Key Screenshots</h2>
    {screenshots_html}

</body>
</html>"""

        path = self.output_dir / filename
        path.write_text(html)
        logger.info(f"Exported HTML report to {path}")
        return path

    def _export_pdf(self, report: "Report", filename: str) -> Path:
        """Export to PDF using weasyprint.

        Falls back to HTML if weasyprint is not available.

        Args:
            report: Report to export.
            filename: Output filename.

        Returns:
            Path to exported file.
        """
        # First generate HTML
        html_filename = filename.replace('.pdf', '_temp.html')
        html_path = self._export_html(report, html_filename)
        pdf_path = self.output_dir / filename

        try:
            from weasyprint import HTML
            HTML(str(html_path)).write_pdf(str(pdf_path))
            html_path.unlink()  # Clean up temp HTML
            logger.info(f"Exported PDF report to {pdf_path}")
            return pdf_path
        except ImportError:
            logger.warning("weasyprint not available, keeping HTML file")
            # Rename HTML to final name
            final_html_path = pdf_path.with_suffix('.html')
            html_path.rename(final_html_path)
            return final_html_path

    def _export_json(self, report: "Report", filename: str) -> Path:
        """Export raw report data as JSON.

        Args:
            report: Report to export.
            filename: Output filename.

        Returns:
            Path to exported file.
        """
        # Convert screenshots to serializable format
        key_screenshots = []
        for ss in report.key_screenshots:
            ts = ss.get('timestamp')
            if isinstance(ts, int):
                ts_str = datetime.fromtimestamp(ts).isoformat()
            elif isinstance(ts, datetime):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)

            key_screenshots.append({
                'id': ss.get('id'),
                'filepath': ss.get('filepath'),
                'timestamp': ts_str,
                'window_title': ss.get('window_title', ''),
                'app_name': ss.get('app_name', '')
            })

        data = {
            'title': report.title,
            'time_range': report.time_range,
            'generated_at': report.generated_at.isoformat(),
            'executive_summary': report.executive_summary,
            'sections': [
                {'title': s.title, 'content': s.content}
                for s in report.sections
            ],
            'analytics': {
                'total_active_minutes': report.analytics.total_active_minutes,
                'total_sessions': report.analytics.total_sessions,
                'top_apps': report.analytics.top_apps,
                'top_windows': report.analytics.top_windows,
                'activity_by_hour': report.analytics.activity_by_hour,
                'activity_by_day': report.analytics.activity_by_day,
                'busiest_period': report.analytics.busiest_period,
            },
            'key_screenshots': key_screenshots
        }

        path = self.output_dir / filename
        path.write_text(json.dumps(data, indent=2))
        logger.info(f"Exported JSON report to {path}")
        return path
