#!/usr/bin/env python3

import json
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Flask, render_template, send_file, jsonify, request, abort

from tracker.analytics import ActivityAnalytics
from tracker.storage import ActivityStorage
from tracker.sessions import SessionManager
from tracker.vision import HybridSummarizer
from tracker.config import get_config_manager, Config
from tracker.monitors import get_monitors
from dataclasses import asdict

app = Flask(__name__)

# Initialize configuration
config_manager = get_config_manager()

DATA_DIR = Path.home() / "activity-tracker-data"
DB_PATH = DATA_DIR / "activity.db"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"

# Global state for background summarization
summarization_state = {
    "running": False,
    "current_hour": None,
    "completed": 0,
    "total": 0,
    "date": None,
    "error": None,
}


def get_db_connection():
    """Get a database connection."""
    if not DB_PATH.exists():
        abort(500, "Database not found. Is the tracker service running?")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_date_param(date_string: str, param_name: str = 'date') -> date:
    """Parse and validate a date string from request parameters.

    Args:
        date_string: Date in YYYY-MM-DD format.
        param_name: Name of the parameter (for error messages).

    Returns:
        Parsed date object.

    Raises:
        400 error if format is invalid.
    """
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        abort(400, f"Invalid date format for {param_name}. Use YYYY-MM-DD.")


def _parse_terminal_context_for_ui(context_json: str) -> str:
    """Parse terminal context JSON and return enriched title for UI display.

    Matches the logic in vision.py._parse_terminal_context to ensure
    UI displays the same enriched titles as the API request.

    Args:
        context_json: JSON string with terminal introspection data.

    Returns:
        Enriched title string like "vim daemon.py in activity-tracker"
        or empty string if parsing fails.
    """
    import json
    try:
        ctx = json.loads(context_json)
        parts = []

        # Main process (skip shells for cleaner display)
        fg_process = ctx.get('foreground_process', '')
        shell = ctx.get('shell', '')
        if fg_process and fg_process not in {'bash', 'zsh', 'fish', 'sh', 'dash'}:
            # Include command args if they add context (e.g., "vim daemon.py")
            full_cmd = ctx.get('full_command', '')
            if full_cmd and ' ' in full_cmd:
                # Get first meaningful arg (skip flags like -m, --version)
                cmd_parts = full_cmd.split()
                arg = None
                for part in cmd_parts[1:]:
                    if not part.startswith('-') and len(part) > 1:
                        arg = part
                        break
                if arg:
                    # Truncate long paths to just filename
                    if '/' in arg:
                        arg = arg.split('/')[-1]
                    if len(arg) < 30:
                        parts.append(f"{fg_process} {arg}")
                    else:
                        parts.append(fg_process)
                else:
                    parts.append(fg_process)
            else:
                parts.append(fg_process)
        elif shell:
            parts.append(f"{shell} (idle)")

        # Working directory (just the project name)
        cwd = ctx.get('working_directory', '')
        if cwd:
            dir_name = Path(cwd).name
            if dir_name and dir_name not in parts:
                parts.append(f"in {dir_name}")

        # SSH indicator
        if ctx.get('is_ssh'):
            parts.append("[ssh]")

        # Tmux session
        tmux = ctx.get('tmux_session')
        if tmux:
            parts.append(f"[tmux:{tmux}]")

        return ' '.join(parts) if parts else ''

    except (json.JSONDecodeError, TypeError, AttributeError):
        return ''


def get_screenshots_for_date(target_date):
    """Get all screenshots for a specific date."""
    conn = get_db_connection()
    
    # Get timestamps for the target date (start of day to start of next day)
    start_timestamp = int(datetime.combine(target_date, datetime.min.time()).timestamp())
    end_timestamp = int(datetime.combine(target_date + timedelta(days=1), datetime.min.time()).timestamp())
    
    cursor = conn.execute("""
        SELECT id, timestamp, filepath, dhash, window_title, app_name
        FROM screenshots
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC
    """, (start_timestamp, end_timestamp))
    
    screenshots = cursor.fetchall()
    conn.close()
    
    # Convert to list of dicts and add formatted time
    result = []
    for row in screenshots:
        screenshot = dict(row)
        screenshot['formatted_time'] = datetime.fromtimestamp(screenshot['timestamp']).strftime('%H:%M:%S')
        result.append(screenshot)
    
    return result


@app.route('/')
def index():
    """Redirect to timeline (primary interface)."""
    from flask import redirect
    return redirect('/timeline')


@app.route('/screenshots')
def screenshots():
    """Redirect to today's screenshot gallery."""
    from flask import redirect
    today = date.today().strftime('%Y-%m-%d')
    return redirect(f'/day/{today}')


@app.route('/day/<date_string>')
def day_view(date_string):
    """Show screenshots for a specific day (YYYY-MM-DD format)."""
    target_date = parse_date_param(date_string)

    screenshots = get_screenshots_for_date(target_date)
    today = date.today()
    return render_template('day.html',
                         screenshots=screenshots,
                         date=target_date,
                         today=today.strftime('%Y-%m-%d'),
                         page='day',
                         timedelta=timedelta)


@app.route('/timeline')
def timeline():
    """Show the timeline view with calendar heatmap and hourly breakdown."""
    today = date.today()
    return render_template('timeline.html',
                         today=today.strftime('%Y-%m-%d'),
                         page='timeline')


@app.route('/analytics')
def analytics():
    """Show the analytics dashboard with charts and statistics."""
    today = date.today()
    return render_template('analytics.html',
                         today=today.strftime('%Y-%m-%d'),
                         page='analytics')


@app.route('/summary/<int:summary_id>')
def summary_detail(summary_id):
    """Show detailed view of a specific AI summary."""
    return render_template('summary_detail.html',
                         summary_id=summary_id,
                         page='timeline')


@app.route('/screenshot/<int:screenshot_id>')
def serve_screenshot(screenshot_id):
    """Serve the actual screenshot image file."""
    conn = get_db_connection()
    
    cursor = conn.execute("""
        SELECT filepath FROM screenshots WHERE id = ?
    """, (screenshot_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        abort(404, "Screenshot not found")

    file_path = SCREENSHOTS_DIR / row['filepath']
    
    if not file_path.exists():
        abort(404, "Screenshot file not found on disk")
    
    return send_file(file_path, mimetype='image/webp')


@app.route('/thumbnail/<int:screenshot_id>')
def serve_thumbnail(screenshot_id):
    """Serve the thumbnail version of a screenshot.

    Falls back to the original screenshot if thumbnail doesn't exist.
    """
    conn = get_db_connection()

    cursor = conn.execute("""
        SELECT filepath FROM screenshots WHERE id = ?
    """, (screenshot_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        abort(404, "Screenshot not found")

    # Try thumbnail first, fall back to original
    thumb_path = THUMBNAILS_DIR / row['filepath']
    if thumb_path.exists():
        return send_file(thumb_path, mimetype='image/webp')

    # Fall back to original screenshot
    file_path = SCREENSHOTS_DIR / row['filepath']
    if not file_path.exists():
        abort(404, "Screenshot file not found on disk")

    return send_file(file_path, mimetype='image/webp')


@app.route('/api/screenshots')
def api_screenshots():
    """JSON API for screenshots in a time range."""
    start_param = request.args.get('start')
    end_param = request.args.get('end')

    if not start_param or not end_param:
        return jsonify({"error": "Both 'start' and 'end' parameters required"}), 400

    try:
        start_timestamp = int(start_param)
        end_timestamp = int(end_param)
    except ValueError:
        return jsonify({"error": "Start and end must be valid Unix timestamps"}), 400

    if start_timestamp >= end_timestamp:
        return jsonify({"error": "Start timestamp must be before end timestamp"}), 400

    conn = get_db_connection()

    cursor = conn.execute("""
        SELECT id, timestamp, filepath, dhash, window_title, app_name
        FROM screenshots
        WHERE timestamp >= ? AND timestamp <= ?
        ORDER BY timestamp ASC
    """, (start_timestamp, end_timestamp))

    screenshots = []
    for row in cursor.fetchall():
        screenshot = dict(row)
        screenshot['iso_time'] = datetime.fromtimestamp(screenshot['timestamp']).isoformat()
        screenshots.append(screenshot)

    conn.close()

    return jsonify({
        "screenshots": screenshots,
        "count": len(screenshots),
        "start": start_timestamp,
        "end": end_timestamp
    })


@app.route('/api/calendar/<int:year>/<int:month>')
def api_calendar_data(year, month):
    """JSON API for calendar heatmap data."""
    # Validate month
    if month < 1 or month > 12:
        return jsonify({"error": "Month must be between 1 and 12"}), 400

    # Validate year (reasonable range)
    if year < 2000 or year > 2100:
        return jsonify({"error": "Year must be between 2000 and 2100"}), 400

    try:
        analytics = ActivityAnalytics()
        calendar_data = analytics.get_calendar_data(year, month)

        return jsonify({
            "year": year,
            "month": month,
            "days": calendar_data
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get calendar data: {str(e)}"}), 500


@app.route('/api/day/<date_string>/hourly')
def api_day_hourly(date_string):
    """JSON API for hourly breakdown of a specific day."""
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        analytics = ActivityAnalytics()
        hourly_data = analytics.get_hourly_breakdown(target_date)

        return jsonify({
            "date": date_string,
            "hourly": hourly_data
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get hourly data: {str(e)}"}), 500


@app.route('/api/day/<date_string>/summary')
def api_day_summary(date_string):
    """JSON API for daily summary statistics."""
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        analytics = ActivityAnalytics()
        summary = analytics.get_daily_summary(target_date)

        return jsonify({
            "date": date_string,
            "summary": summary
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get daily summary: {str(e)}"}), 500


@app.route('/api/day/<date_string>/screenshots')
def api_day_screenshots(date_string):
    """JSON API for all screenshots on a specific day."""
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        screenshots_raw = get_screenshots_for_date(target_date)

        # Convert to format expected by frontend
        screenshots = []
        for row in screenshots_raw:
            screenshot = {
                'id': row['id'],
                'timestamp': row['timestamp'],
                'window_title': row['window_title'],
                'app_name': row['app_name']
            }
            screenshots.append(screenshot)

        return jsonify({
            "date": date_string,
            "count": len(screenshots),
            "screenshots": screenshots
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get screenshots: {str(e)}"}), 500


@app.route('/api/week/<date_string>')
def api_week_stats(date_string):
    """JSON API for weekly statistics starting from a specific date."""
    try:
        start_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        analytics = ActivityAnalytics()
        weekly_stats = analytics.get_weekly_stats(start_date)

        return jsonify({
            "start_date": date_string,
            "end_date": (start_date + timedelta(days=7)).strftime('%Y-%m-%d'),
            "stats": weekly_stats
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get weekly stats: {str(e)}"}), 500


@app.route('/api/screenshots/<date_string>/<int:hour>')
def api_screenshots_by_hour(date_string, hour):
    """JSON API for screenshots in a specific hour of a specific day."""
    # Validate hour
    if hour < 0 or hour > 23:
        return jsonify({"error": "Hour must be between 0 and 23"}), 400

    # Parse and validate date
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        # Calculate timestamp range for the specific hour
        hour_start = datetime.combine(target_date, datetime.min.time()) + timedelta(hours=hour)
        hour_end = hour_start + timedelta(hours=1)

        start_timestamp = int(hour_start.timestamp())
        end_timestamp = int(hour_end.timestamp())

        # Query database
        conn = get_db_connection()
        cursor = conn.execute("""
            SELECT id, timestamp, filepath, dhash, window_title, app_name
            FROM screenshots
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
        """, (start_timestamp, end_timestamp))

        screenshots = []
        for row in cursor.fetchall():
            screenshot = {
                'id': row['id'],
                'timestamp': row['timestamp'],
                'filepath': f"/screenshot/{row['id']}",  # URL to serve the image
                'file_hash': row['dhash'],
                'window_title': row['window_title'],
                'app_name': row['app_name'],
                'iso_time': datetime.fromtimestamp(row['timestamp']).isoformat()
            }
            screenshots.append(screenshot)

        conn.close()

        return jsonify({
            "date": date_string,
            "hour": hour,
            "count": len(screenshots),
            "screenshots": screenshots
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get screenshots: {str(e)}"}), 500


@app.route('/api/screenshots/batch')
def api_screenshots_batch():
    """Get screenshots by a list of IDs.

    Query params:
        ids: Comma-separated list of screenshot IDs

    Returns:
        {"screenshots": [...], "count": N}
    """
    ids_param = request.args.get('ids', '')
    if not ids_param:
        return jsonify({"error": "Missing 'ids' parameter"}), 400

    try:
        ids = [int(id.strip()) for id in ids_param.split(',') if id.strip()]
        if not ids:
            return jsonify({"screenshots": [], "count": 0})

        placeholders = ','.join('?' * len(ids))
        conn = get_db_connection()
        cursor = conn.execute(f"""
            SELECT id, timestamp, filepath, dhash, window_title, app_name
            FROM screenshots
            WHERE id IN ({placeholders})
            ORDER BY timestamp ASC
        """, ids)

        screenshots = []
        for row in cursor.fetchall():
            screenshots.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'filepath': row['filepath'],
                'file_hash': row['dhash'],
                'window_title': row['window_title'],
                'app_name': row['app_name'],
                'iso_time': datetime.fromtimestamp(row['timestamp']).isoformat()
            })

        conn.close()

        return jsonify({
            "count": len(screenshots),
            "screenshots": screenshots
        })
    except ValueError:
        return jsonify({"error": "Invalid ID format"}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to get screenshots: {str(e)}"}), 500


@app.route('/api/screenshots/<date_string>')
def api_screenshots_for_date(date_string):
    """Get all screenshots for a specific date.

    Args:
        date_string: Date in YYYY-MM-DD format

    Returns:
        {"date": "2025-12-10", "count": N, "screenshots": [...]}
    """
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        start_timestamp = int(datetime.combine(target_date, datetime.min.time()).timestamp())
        end_timestamp = int(datetime.combine(target_date + timedelta(days=1), datetime.min.time()).timestamp())

        conn = get_db_connection()
        cursor = conn.execute("""
            SELECT id, timestamp, filepath, dhash, window_title, app_name
            FROM screenshots
            WHERE timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC
        """, (start_timestamp, end_timestamp))

        screenshots = []
        for row in cursor.fetchall():
            screenshots.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'filepath': f"/screenshot/{row['id']}",
                'window_title': row['window_title'],
                'app_name': row['app_name']
            })

        conn.close()

        return jsonify({
            "date": date_string,
            "count": len(screenshots),
            "screenshots": screenshots
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get screenshots: {str(e)}"}), 500


# =============================================================================
# Focus Analytics API endpoints
# =============================================================================

@app.route('/api/analytics/focus/<date>')
def get_focus_analytics(date):
    """Get detailed focus analytics for a specific day.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        {
            "date": "2025-12-09",
            "apps": [...],
            "windows": [...],
            "hourly": [...],
            "metrics": {
                "total_tracked_seconds": 14400,
                "context_switches": 42,
                "longest_focus_sessions": [...],
                "unique_apps": 5,
                "unique_windows": 12
            }
        }
    """
    try:
        start = datetime.strptime(date, '%Y-%m-%d')
        end = start + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        storage = ActivityStorage()

        apps = storage.get_app_durations_in_range(start, end)
        windows = storage.get_window_durations_in_range(start, end, limit=20)
        hourly = storage.get_hourly_app_breakdown(date)
        context_switches = storage.get_context_switch_count(start, end)
        longest_sessions = storage.get_longest_focus_sessions(start, end, min_duration_minutes=10, limit=10)

        total_tracked_seconds = sum(a.get('total_seconds', 0) or 0 for a in apps)

        return jsonify({
            'date': date,
            'apps': apps,
            'windows': windows,
            'hourly': hourly,
            'metrics': {
                'total_tracked_seconds': total_tracked_seconds,
                'context_switches': context_switches,
                'longest_focus_sessions': longest_sessions,
                'unique_apps': len(apps),
                'unique_windows': len(windows),
            }
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get focus analytics: {str(e)}'}), 500


@app.route('/api/analytics/focus/timeline')
def get_focus_timeline():
    """Get focus events for timeline visualization.

    Query params:
        start: ISO datetime string (required)
        end: ISO datetime string (required)

    Returns:
        {
            "events": [...],
            "total_events": 42
        }
    """
    try:
        start_param = request.args.get('start')
        end_param = request.args.get('end')

        if not start_param or not end_param:
            return jsonify({'error': 'start and end parameters required'}), 400

        start = datetime.fromisoformat(start_param)
        end = datetime.fromisoformat(end_param)
    except (TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid start/end parameters: {e}'}), 400

    try:
        storage = ActivityStorage()
        events = storage.get_focus_events_in_range(start, end)

        # Format events for response
        formatted_events = []
        for e in events:
            start_time = e.get('start_time')
            end_time = e.get('end_time')

            # Handle datetime objects or strings
            if isinstance(start_time, datetime):
                start_time = start_time.isoformat()
            if isinstance(end_time, datetime):
                end_time = end_time.isoformat()

            formatted_events.append({
                'app_name': e.get('app_name'),
                'window_title': e.get('window_title'),
                'start_time': start_time,
                'end_time': end_time,
                'duration_seconds': e.get('duration_seconds')
            })

        return jsonify({
            'events': formatted_events,
            'total_events': len(formatted_events)
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get focus timeline: {str(e)}'}), 500


@app.route('/api/analytics/focus/summary')
def get_focus_summary():
    """Get focus summary for a time range (for reports).

    Query params:
        start: ISO datetime string (required)
        end: ISO datetime string (required)

    Returns:
        {
            "total_tracked_time": {"seconds": 14400, "formatted": "4h 0m"},
            "context_switches": 42,
            "top_apps": [...],
            "deep_work_sessions": [...]
        }
    """
    try:
        start_param = request.args.get('start')
        end_param = request.args.get('end')

        if not start_param or not end_param:
            return jsonify({'error': 'start and end parameters required'}), 400

        start = datetime.fromisoformat(start_param)
        end = datetime.fromisoformat(end_param)
    except (TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid start/end parameters: {e}'}), 400

    try:
        storage = ActivityStorage()

        apps = storage.get_app_durations_in_range(start, end)
        total_seconds = sum(a.get('total_seconds', 0) or 0 for a in apps)

        # Format duration
        hours = int(total_seconds // 3600)
        mins = int((total_seconds % 3600) // 60)
        formatted = f"{hours}h {mins}m"

        return jsonify({
            'total_tracked_time': {
                'seconds': total_seconds,
                'formatted': formatted
            },
            'context_switches': storage.get_context_switch_count(start, end),
            'top_apps': apps[:5],
            'deep_work_sessions': storage.get_longest_focus_sessions(start, end, min_duration_minutes=10, limit=5)
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get focus summary: {str(e)}'}), 500


@app.route('/api/analytics/ai')
def get_ai_analytics():
    """Get AI summarization analytics for the dashboard.

    Query params:
        days: Number of days to look back (default: 7)

    Returns:
        {
            "total_summaries": 45,
            "avg_confidence": 0.72,
            "confidence_distribution": {"high": 20, "medium": 18, "low": 7},
            "tag_counts": {"coding": 15, "meetings": 8, ...},
            "recent_summaries": [...],
            "summaries_by_day": {...}
        }
    """
    days = request.args.get('days', 7, type=int)

    try:
        storage = ActivityStorage()

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Get all summaries in range
        all_summaries = []
        for i in range(days):
            date = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
            day_summaries = storage.get_threshold_summaries_for_date(date)
            all_summaries.extend(day_summaries)

        # Calculate statistics
        total = len(all_summaries)
        confidences = [s.get('confidence') for s in all_summaries if s.get('confidence') is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

        # Confidence distribution
        high = sum(1 for c in confidences if c >= 0.8)
        medium = sum(1 for c in confidences if 0.5 <= c < 0.8)
        low = sum(1 for c in confidences if c < 0.5)

        # Tag counts
        tag_counts = {}
        for s in all_summaries:
            tags = s.get('tags', [])
            if tags:
                for tag in tags:
                    tag_lower = tag.lower().strip()
                    if tag_lower:
                        tag_counts[tag_lower] = tag_counts.get(tag_lower, 0) + 1

        # Sort tags by count
        sorted_tags = sorted(tag_counts.items(), key=lambda x: -x[1])

        # Summaries by day for chart
        summaries_by_day = {}
        for s in all_summaries:
            day = s.get('start_time', '')[:10]
            if day:
                summaries_by_day[day] = summaries_by_day.get(day, 0) + 1

        # Recent summaries (last 10)
        recent = sorted(all_summaries, key=lambda x: x.get('created_at', ''), reverse=True)[:10]
        recent_formatted = [{
            'id': s.get('id'),
            'summary': s.get('summary', '')[:150] + ('...' if len(s.get('summary', '')) > 150 else ''),
            'start_time': s.get('start_time'),
            'end_time': s.get('end_time'),
            'confidence': s.get('confidence'),
            'tags': s.get('tags', []),
            'model': s.get('model_used'),
        } for s in recent]

        return jsonify({
            'total_summaries': total,
            'avg_confidence': round(avg_confidence, 2),
            'confidence_distribution': {
                'high': high,
                'medium': medium,
                'low': low
            },
            'tag_counts': dict(sorted_tags[:30]),  # Top 30 tags
            'recent_summaries': recent_formatted,
            'summaries_by_day': summaries_by_day
        })

    except Exception as e:
        return jsonify({'error': f'Failed to get AI analytics: {str(e)}'}), 500


@app.route('/api/summaries/<date_string>')
def api_summaries_for_date(date_string):
    """JSON API for activity summaries for a specific date."""
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        storage = ActivityStorage()

        # Get existing summaries
        summaries = storage.get_summaries_for_date(date_string)
        summaries_by_hour = {s["hour"]: s for s in summaries}

        # Get screenshot counts per hour
        start_timestamp = int(datetime.combine(target_date, datetime.min.time()).timestamp())

        conn = get_db_connection()
        cursor = conn.execute("""
            SELECT CAST((timestamp - ?) / 3600 AS INTEGER) as hour,
                   COUNT(*) as count
            FROM screenshots
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY hour
        """, (start_timestamp, start_timestamp, start_timestamp + 86400))

        hour_counts = {row["hour"]: row["count"] for row in cursor.fetchall()}
        conn.close()

        # Build response with all hours that have data
        result = []
        for hour in sorted(set(summaries_by_hour.keys()) | set(hour_counts.keys())):
            summary_data = summaries_by_hour.get(hour)
            result.append({
                "hour": hour,
                "summary": summary_data["summary"] if summary_data else None,
                "screenshot_count": hour_counts.get(hour, 0),
            })

        # Get daily summary if exists
        daily_summary_data = storage.get_daily_summary(date_string)
        daily_summary = daily_summary_data["summary"] if daily_summary_data else None

        return jsonify({
            "date": date_string,
            "summaries": result,
            "daily_summary": daily_summary,
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get summaries: {str(e)}"}), 500


@app.route('/api/summaries/coverage')
def api_summaries_coverage():
    """JSON API for summary coverage statistics."""
    try:
        storage = ActivityStorage()
        coverage = storage.get_summary_coverage()

        # Calculate total days
        total_days = 0
        if coverage["date_range"]:
            start = datetime.strptime(coverage["date_range"]["start"], "%Y-%m-%d")
            end = datetime.strptime(coverage["date_range"]["end"], "%Y-%m-%d")
            total_days = (end - start).days + 1

        total_hours = coverage["total_hours_with_screenshots"]
        summarized_hours = coverage["total_hours_summarized"]
        coverage_pct = (summarized_hours / total_hours * 100) if total_hours > 0 else 0

        return jsonify({
            "total_days": total_days,
            "summarized_hours": summarized_hours,
            "total_hours": total_hours,
            "coverage_pct": round(coverage_pct, 1),
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get coverage: {str(e)}"}), 500


def _run_summarization(date_str: str, hours: list[int]):
    """Background thread function to run summarization."""
    global summarization_state

    try:
        storage = ActivityStorage()
        summarizer = HybridSummarizer()

        if not summarizer.is_available():
            summarization_state["error"] = "Summarizer not available (check Ollama and Tesseract)"
            summarization_state["running"] = False
            return

        for hour in hours:
            if not summarization_state["running"]:
                break  # Allow cancellation

            summarization_state["current_hour"] = hour

            # Get screenshots for this hour
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            start_ts = int(date_obj.timestamp()) + hour * 3600
            end_ts = start_ts + 3600

            conn = get_db_connection()
            cursor = conn.execute("""
                SELECT id, filepath FROM screenshots
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp ASC
            """, (start_ts, end_ts))
            screenshots = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if len(screenshots) < 2:
                summarization_state["completed"] += 1
                continue

            # Sample if too many
            if len(screenshots) > 6:
                step = len(screenshots) / 6
                indices = [int(i * step) for i in range(6)]
                screenshots = [screenshots[i] for i in indices]

            paths = [str(SCREENSHOTS_DIR / s["filepath"]) for s in screenshots]
            screenshot_ids = [s["id"] for s in screenshots]

            try:
                import time
                start_time = time.time()
                summary = summarizer.summarize_hour(paths)
                inference_ms = int((time.time() - start_time) * 1000)

                storage.save_summary(
                    date=date_str,
                    hour=hour,
                    summary=summary,
                    screenshot_ids=screenshot_ids,
                    model=summarizer.model,
                    inference_ms=inference_ms,
                )
            except Exception as e:
                summarization_state["error"] = f"Hour {hour}: {str(e)}"

            summarization_state["completed"] += 1

    except Exception as e:
        summarization_state["error"] = str(e)
    finally:
        summarization_state["running"] = False
        summarization_state["current_hour"] = None


@app.route('/api/summaries/generate', methods=['POST'])
def api_generate_summaries():
    """Start background summarization for a date."""
    global summarization_state

    if summarization_state["running"]:
        return jsonify({
            "status": "already_running",
            "date": summarization_state["date"],
            "hours_remaining": summarization_state["total"] - summarization_state["completed"],
        }), 409

    data = request.get_json() or {}
    date_str = data.get("date", datetime.now().strftime("%Y-%m-%d"))

    # Validate date
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    # Get hours to process
    hours = data.get("hours")
    if hours is None:
        storage = ActivityStorage()
        hours = storage.get_unsummarized_hours(date_str)

    if not hours:
        return jsonify({
            "status": "nothing_to_do",
            "message": "No unsummarized hours found for this date",
        })

    # Reset state and start background thread
    summarization_state.update({
        "running": True,
        "current_hour": None,
        "completed": 0,
        "total": len(hours),
        "date": date_str,
        "error": None,
    })

    thread = threading.Thread(target=_run_summarization, args=(date_str, hours))
    thread.daemon = True
    thread.start()

    return jsonify({
        "status": "started",
        "hours_queued": len(hours),
    })


@app.route('/api/summaries/generate/status')
def api_generate_status():
    """Get current summarization progress."""
    return jsonify({
        "running": summarization_state["running"],
        "current_hour": summarization_state["current_hour"],
        "completed": summarization_state["completed"],
        "total": summarization_state["total"],
        "date": summarization_state["date"],
        "error": summarization_state["error"],
    })


# =============================================================================
# Session-based API endpoints
# =============================================================================

@app.route('/api/sessions/<date_string>')
def api_sessions_for_date(date_string):
    """JSON API for sessions on a specific date.

    Returns:
        {
            "date": "2025-12-01",
            "sessions": [
                {
                    "id": 42,
                    "start_time": "2025-12-01T14:00:00",
                    "end_time": "2025-12-01T16:30:00",
                    "duration_minutes": 150,
                    "summary": "Implementing hybrid mode...",
                    "screenshot_count": 300,
                    "unique_windows": 5
                },
                ...
            ],
            "total_active_minutes": 420,
            "session_count": 4
        }
    """
    try:
        datetime.strptime(date_string, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

    try:
        storage = ActivityStorage()
        session_manager = SessionManager(storage)

        sessions = session_manager.get_sessions_for_date(date_string)

        # Format sessions for response
        formatted_sessions = []
        total_active_seconds = 0

        for session in sessions:
            duration_seconds = session.get("duration_seconds") or 0
            total_active_seconds += duration_seconds

            formatted_sessions.append({
                "id": session["id"],
                "start_time": session["start_time"],
                "end_time": session.get("end_time"),
                "duration_minutes": duration_seconds // 60,
                "summary": session.get("summary"),
                "screenshot_count": session.get("screenshot_count", 0),
                "unique_windows": session.get("unique_windows", 0),
                "model_used": session.get("model_used"),
                "inference_time_ms": session.get("inference_time_ms"),
                "prompt_text": session.get("prompt_text"),
                "screenshot_ids_used": session.get("screenshot_ids_used", []),
            })

        return jsonify({
            "date": date_string,
            "sessions": formatted_sessions,
            "total_active_minutes": total_active_seconds // 60,
            "session_count": len(formatted_sessions),
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get sessions: {str(e)}"}), 500


@app.route('/api/sessions/<int:session_id>/screenshots')
def api_session_screenshots(session_id):
    """JSON API for screenshots in a specific session.

    Supports pagination via 'page' and 'per_page' query parameters.

    Returns:
        {
            "session_id": 42,
            "screenshots": [...],
            "total": 300,
            "page": 1,
            "per_page": 50
        }
    """
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
    except ValueError:
        return jsonify({"error": "page and per_page must be integers"}), 400

    if page < 1 or per_page < 1 or per_page > 200:
        return jsonify({"error": "Invalid pagination parameters"}), 400

    try:
        storage = ActivityStorage()

        # Verify session exists
        session = storage.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        # Get all screenshots for session
        all_screenshots = storage.get_session_screenshots(session_id)
        total = len(all_screenshots)

        # Apply pagination
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated = all_screenshots[start_idx:end_idx]

        # Format for response
        screenshots = []
        for s in paginated:
            screenshots.append({
                "id": s["id"],
                "timestamp": s["timestamp"],
                "filepath": f"/screenshot/{s['id']}",
                "window_title": s.get("window_title"),
                "app_name": s.get("app_name"),
                "iso_time": datetime.fromtimestamp(s["timestamp"]).isoformat(),
            })

        return jsonify({
            "session_id": session_id,
            "screenshots": screenshots,
            "total": total,
            "page": page,
            "per_page": per_page,
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get screenshots: {str(e)}"}), 500


@app.route('/api/sessions/current')
def api_current_session():
    """JSON API for the currently active session.

    Returns:
        {
            "session": {...} or null,
            "is_afk": true/false
        }
    """
    try:
        storage = ActivityStorage()
        session_manager = SessionManager(storage)

        active_session = session_manager.get_current_session()

        if active_session:
            # Calculate current duration
            start_time = datetime.fromisoformat(active_session["start_time"])
            current_duration = int((datetime.now() - start_time).total_seconds())

            return jsonify({
                "session": {
                    "id": active_session["id"],
                    "start_time": active_session["start_time"],
                    "duration_minutes": current_duration // 60,
                    "screenshot_count": active_session.get("screenshot_count", 0),
                    "unique_windows": active_session.get("unique_windows", 0),
                },
                "is_afk": False,
            })
        else:
            return jsonify({
                "session": None,
                "is_afk": True,
            })
    except Exception as e:
        return jsonify({"error": f"Failed to get current session: {str(e)}"}), 500


@app.route('/api/sessions/<int:session_id>')
def api_get_session(session_id):
    """Get details for a single session including summary if available."""
    try:
        storage = ActivityStorage()
        session = storage.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/settings')
def settings_page():
    """Render settings page."""
    return render_template('settings.html')


@app.route('/api/config', methods=['GET'])
def get_config():
    """Return current configuration.

    Returns:
        JSON object with all configuration sections
    """
    return jsonify(config_manager.to_dict())


@app.route('/api/config', methods=['PATCH'])
def update_config():
    """Update configuration values.

    Request body:
        {
            "section": "capture",
            "key": "interval_seconds",
            "value": 60
        }

    Returns:
        {
            "success": true/false,
            "requires_restart": true/false,
            "config": {...}
        }
    """
    try:
        data = request.json or {}

        if not all(k in data for k in ['section', 'key', 'value']):
            return jsonify({"error": "Missing required fields: section, key, value"}), 400

        section = data['section']
        key = data['key']
        value = data['value']

        # Update configuration
        changed = config_manager.update(section, key, value)

        # Determine if daemon restart is required
        restart_keys = {
            'capture': ['interval_seconds'],
            'afk': ['timeout_seconds'],
            'web': ['host', 'port'],
            'storage': ['data_dir'],
        }
        requires_restart = section in restart_keys and key in restart_keys[section]

        return jsonify({
            "success": changed,
            "requires_restart": requires_restart,
            "config": config_manager.to_dict()
        })

    except Exception as e:
        return jsonify({"error": f"Failed to update config: {str(e)}"}), 500


@app.route('/api/config/reset', methods=['POST'])
def reset_config():
    """Reset configuration to defaults.

    Returns:
        {
            "success": true,
            "config": {...}
        }
    """
    try:
        config_manager.config = Config()
        config_manager.save()

        return jsonify({
            "success": True,
            "config": config_manager.to_dict()
        })

    except Exception as e:
        return jsonify({"error": f"Failed to reset config: {str(e)}"}), 500


@app.route('/api/restart', methods=['POST'])
def restart_service():
    """Restart the activity-tracker service.

    Triggers a systemd user service restart. The response is sent before
    the restart occurs, so the client should expect a brief disconnection.

    Returns:
        {
            "success": true,
            "message": "Service restart initiated"
        }
    """
    try:
        # Start restart in background thread so we can send response first
        def do_restart():
            import time
            time.sleep(0.5)  # Give time for response to be sent
            subprocess.run(
                ['systemctl', '--user', 'restart', 'activity-tracker'],
                check=True,
                capture_output=True
            )

        thread = threading.Thread(target=do_restart, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "message": "Service restart initiated. Page will reload automatically."
        })

    except Exception as e:
        return jsonify({"error": f"Failed to restart service: {str(e)}"}), 500


@app.route('/api/status', methods=['GET'])
def get_status():
    """Return daemon status, storage usage, and system information.

    Returns:
        {
            "storage_used_gb": 12.5,
            "screenshot_count": 45231,
            "ollama_available": true,
            "monitors": [...]
        }
    """
    try:
        storage = ActivityStorage()

        # Get storage usage
        storage_used = 0
        if SCREENSHOTS_DIR.exists():
            for file in SCREENSHOTS_DIR.rglob("*.webp"):
                try:
                    storage_used += file.stat().st_size
                except OSError:
                    pass
        storage_used_gb = storage_used / (1024 ** 3)

        # Get screenshot count
        with get_db_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM screenshots")
            screenshot_count = cursor.fetchone()['count']

        # Check Ollama availability
        try:
            summarizer = HybridSummarizer()
            ollama_available = summarizer.is_available()
        except Exception:
            ollama_available = False

        # Get monitors
        try:
            monitors = get_monitors()
            monitors_data = [asdict(m) for m in monitors]
        except Exception:
            monitors_data = []

        # Get current session if available
        try:
            session_mgr = SessionManager(storage)
            current_session_id = session_mgr.get_current_session_id()
            current_session = storage.get_session(current_session_id) if current_session_id else None
        except Exception:
            current_session = None

        return jsonify({
            "storage_used_gb": round(storage_used_gb, 2),
            "screenshot_count": screenshot_count,
            "ollama_available": ollama_available,
            "monitors": monitors_data,
            "current_session": current_session
        })

    except Exception as e:
        return jsonify({"error": f"Failed to get status: {str(e)}"}), 500


@app.route('/api/ollama/models', methods=['GET'])
def get_ollama_models():
    """Fetch available models from Ollama API.

    Returns:
        {
            "models": [
                {"name": "gemma3:14b-it-qat", "size": "8.0 GB", "modified": "..."},
                ...
            ],
            "available": true
        }
    """
    import requests

    try:
        ollama_host = config_manager.config.summarization.ollama_host
        response = requests.get(f"{ollama_host}/api/tags", timeout=5)

        if response.status_code == 200:
            data = response.json()
            models = []
            for model in data.get('models', []):
                # Format size in human-readable format
                size_bytes = model.get('size', 0)
                if size_bytes >= 1024**3:
                    size_str = f"{size_bytes / (1024**3):.1f} GB"
                elif size_bytes >= 1024**2:
                    size_str = f"{size_bytes / (1024**2):.1f} MB"
                else:
                    size_str = f"{size_bytes / 1024:.1f} KB"

                models.append({
                    'name': model.get('name', ''),
                    'size': size_str,
                    'modified': model.get('modified_at', ''),
                    'details': model.get('details', {})
                })

            # Sort by name
            models.sort(key=lambda x: x['name'])

            return jsonify({
                'models': models,
                'available': True,
                'current': config_manager.config.summarization.model
            })
        else:
            return jsonify({
                'models': [],
                'available': False,
                'error': f"Ollama returned status {response.status_code}"
            })

    except requests.exceptions.ConnectionError:
        return jsonify({
            'models': [],
            'available': False,
            'error': 'Cannot connect to Ollama. Is it running?'
        })
    except requests.exceptions.Timeout:
        return jsonify({
            'models': [],
            'available': False,
            'error': 'Ollama connection timed out'
        })
    except Exception as e:
        return jsonify({
            'models': [],
            'available': False,
            'error': str(e)
        })


@app.route('/api/summarization/prompt-template', methods=['GET'])
def get_prompt_template():
    """Get the prompt template used for summarization.

    Returns the current prompt template so users can see exactly
    what's being sent to the AI model.
    """
    # This is the prompt template from vision.py HybridSummarizer.summarize_session
    template = """You are summarizing a developer's work activity.

[Previous context: {previous_summary}]

## Time Breakdown (from focus tracking)
{focus_context}

## Window Content (OCR)
{ocr_section}

## Screenshots
{num_screenshots} screenshots attached showing actual screen content.

Based on the time breakdown, OCR text, and screenshots, write ONE sentence (max 25 words) describing the PRIMARY activity.

IMPORTANT: Output ONLY the summary sentence. No explanation, no reasoning, no preamble.

Guidelines:
- Be SPECIFIC: mention actual file names, function names, or topics visible
- Focus on WHAT was accomplished, not just what apps were used
- Use active voice: "Implemented X", "Debugged Y", "Reviewed Z"
- If multiple activities, focus on the dominant one (based on time breakdown)
"""

    return jsonify({
        'template': template,
        'note': 'Sections are included based on Content Mode settings. Variables like {focus_context} are filled with actual data.'
    })


# ==================== Threshold-Based Summary API ====================

# Global reference to summarizer worker (set by daemon when running)
summarizer_worker = None


def set_summarizer_worker(worker):
    """Set the summarizer worker reference for API access."""
    global summarizer_worker
    summarizer_worker = worker


@app.route('/api/threshold-summaries/<date>')
def api_get_threshold_summaries(date):
    """Get all threshold summaries for a date.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        {"summaries": [...], "date": "2025-12-09", "projects": [...]}
    """
    try:
        storage = ActivityStorage()
        summaries = storage.get_threshold_summaries_for_date(date)

        # Extract unique projects
        projects = list(set(s.get('project') or 'unknown' for s in summaries))

        return jsonify({
            "date": date,
            "summaries": summaries,
            "projects": sorted(projects)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/threshold-summaries/<date>/by-project')
def api_get_summaries_by_project(date):
    """Get threshold summaries for a date, grouped by project.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        {
            "date": "2025-12-09",
            "projects": {
                "activity-tracker": [...],
                "acusight": [...]
            },
            "project_count": 2
        }
    """
    try:
        start = datetime.strptime(date, '%Y-%m-%d')
        end = start + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        storage = ActivityStorage()
        by_project = storage.get_summaries_by_project(start, end)

        # Format for JSON response
        formatted = {}
        for project, summaries in by_project.items():
            formatted[project] = [
                {
                    'id': s.get('id'),
                    'start_time': s['start_time'].isoformat() if isinstance(s.get('start_time'), datetime) else s.get('start_time'),
                    'end_time': s['end_time'].isoformat() if isinstance(s.get('end_time'), datetime) else s.get('end_time'),
                    'summary': s.get('summary'),
                    'screenshot_count': s.get('screenshot_count')
                }
                for s in summaries
            ]

        return jsonify({
            'date': date,
            'projects': formatted,
            'project_count': len(by_project)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/threshold-summaries/<int:summary_id>/regenerate', methods=['POST'])
def api_regenerate_summary(summary_id):
    """Queue a summary for regeneration with current settings.

    Returns:
        {"status": "queued", "summary_id": 123}
    """
    global summarizer_worker

    if summarizer_worker is None:
        # Try to create a worker if daemon isn't running
        try:
            from tracker.summarizer_worker import SummarizerWorker
            storage = ActivityStorage()
            worker = SummarizerWorker(storage, config_manager)
            worker.start()
            worker.queue_regenerate(summary_id)
            return jsonify({
                "status": "queued",
                "summary_id": summary_id,
                "note": "Started standalone worker"
            })
        except Exception as e:
            return jsonify({"error": f"Summarizer not available: {e}"}), 503

    summarizer_worker.queue_regenerate(summary_id)
    return jsonify({
        "status": "queued",
        "summary_id": summary_id
    })


@app.route('/api/threshold-summaries/<date>/regenerate-all', methods=['POST'])
def api_regenerate_day_summaries(date):
    """Queue all summaries for a date for regeneration.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        {"status": "queued", "count": 5, "summary_ids": [1, 2, 3, 4, 5]}
    """
    global summarizer_worker

    # Validate date format
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        storage = ActivityStorage()
        summaries = storage.get_threshold_summaries_for_date(date)

        if not summaries:
            return jsonify({'error': 'No summaries found for this date'}), 404

        summary_ids = [s['id'] for s in summaries]

        # Ensure worker is available
        if summarizer_worker is None:
            try:
                from tracker.summarizer_worker import SummarizerWorker
                summarizer_worker = SummarizerWorker(storage, config_manager)
                summarizer_worker.start()
            except Exception as e:
                return jsonify({"error": f"Summarizer not available: {e}"}), 503

        # Queue each summary for regeneration
        for summary_id in summary_ids:
            summarizer_worker.queue_regenerate(summary_id)

        return jsonify({
            "status": "queued",
            "count": len(summary_ids),
            "summary_ids": summary_ids
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/threshold-summaries/<int:summary_id>', methods=['DELETE'])
def api_delete_summary(summary_id):
    """Delete a threshold summary.

    Returns:
        {"status": "deleted"}
    """
    try:
        storage = ActivityStorage()
        deleted = storage.delete_threshold_summary(summary_id)

        if not deleted:
            return jsonify({"error": "Summary not found"}), 404

        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/threshold-summaries/<int:summary_id>/detail')
def api_get_summary_detail(summary_id):
    """Get detailed information about a summary for the detail page.

    Returns full summary data plus screenshots, focus events, and window info.
    """
    try:
        storage = ActivityStorage()
        summary = storage.get_threshold_summary(summary_id)

        if not summary:
            return jsonify({"error": "Summary not found"}), 404

        # Get full screenshot data for each screenshot in this summary
        screenshots = []
        for sid in summary.get('screenshot_ids', []):
            s = storage.get_screenshot_by_id(sid)
            if s:
                # Add formatted time
                s['formatted_time'] = datetime.fromtimestamp(s['timestamp']).strftime('%H:%M:%S')
                screenshots.append(s)

        # Sort by timestamp
        screenshots.sort(key=lambda x: x['timestamp'])

        # Calculate duration
        if screenshots:
            start_ts = screenshots[0]['timestamp']
            end_ts = screenshots[-1]['timestamp']
            duration_seconds = end_ts - start_ts
            duration_minutes = int(duration_seconds // 60)
        else:
            duration_minutes = 0

        # Get focus events for this time range
        focus_events = []
        start_dt = None
        end_dt = None
        if summary.get('start_time') and summary.get('end_time'):
            start_dt = datetime.fromisoformat(summary['start_time']) if isinstance(summary['start_time'], str) else summary['start_time']
            end_dt = datetime.fromisoformat(summary['end_time']) if isinstance(summary['end_time'], str) else summary['end_time']
            focus_events = storage.get_focus_events_overlapping_range(start_dt, end_dt)

        # Calculate window durations from focus events
        # Aggregate by (app_name, enriched_title) to match how vision.py builds focus context
        window_durations = {}
        for event in focus_events:
            app_name = event.get('app_name', 'Unknown')
            title = event.get('window_title', 'Unknown')

            # Enrich with terminal context if available (same as vision.py)
            terminal_context = event.get('terminal_context')
            if terminal_context:
                enriched = _parse_terminal_context_for_ui(terminal_context)
                if enriched:
                    title = enriched

            # Truncate long titles
            if len(title) > 60:
                title = title[:57] + '...'

            # Clip duration to the summary time range if event spans boundaries
            event_start = datetime.fromisoformat(event['start_time']) if isinstance(event['start_time'], str) else event['start_time']
            event_end = datetime.fromisoformat(event['end_time']) if isinstance(event['end_time'], str) else event['end_time']

            # Clip to summary range
            clipped_start = max(event_start, start_dt)
            clipped_end = min(event_end, end_dt)
            if clipped_end > clipped_start:
                duration = (clipped_end - clipped_start).total_seconds()
            else:
                duration = 0

            if duration > 0:
                key = (app_name, title)
                if key not in window_durations:
                    window_durations[key] = {'app_name': app_name, 'title': title, 'total_seconds': 0}
                window_durations[key]['total_seconds'] += duration

        # Sort by duration descending
        window_durations_list = [
            {'app_name': v['app_name'], 'title': v['title'], 'duration_seconds': v['total_seconds']}
            for v in sorted(window_durations.values(), key=lambda x: -x['total_seconds'])
        ]

        # Calculate context switches (app changes within the time range)
        context_switches = 0
        if len(focus_events) > 1:
            for i in range(1, len(focus_events)):
                if focus_events[i].get('app_name') != focus_events[i-1].get('app_name'):
                    context_switches += 1

        # Build chronological activity log for UI
        activity_log = []
        sorted_events = sorted(focus_events, key=lambda e: e.get('start_time', '') or '')
        for event in sorted_events:
            app_name = event.get('app_name', 'Unknown')
            title = event.get('window_title', 'Unknown')

            # Enrich with terminal context
            terminal_context = event.get('terminal_context')
            if terminal_context:
                enriched = _parse_terminal_context_for_ui(terminal_context)
                if enriched:
                    title = enriched

            # Truncate long titles
            if len(title) > 60:
                title = title[:57] + '...'

            # Get clipped duration
            event_start = datetime.fromisoformat(event['start_time']) if isinstance(event['start_time'], str) else event['start_time']
            event_end = datetime.fromisoformat(event['end_time']) if isinstance(event['end_time'], str) else event['end_time']

            # Clip to summary range
            if start_dt and end_dt:
                clipped_start = max(event_start, start_dt)
                clipped_end = min(event_end, end_dt)
                if clipped_end > clipped_start:
                    duration = (clipped_end - clipped_start).total_seconds()
                else:
                    duration = 0
            else:
                duration = event.get('duration_seconds', 0) or 0

            if duration > 0:
                activity_log.append({
                    'time': clipped_start.strftime('%H:%M:%S'),
                    'app_name': app_name,
                    'title': title,
                    'duration_seconds': duration,
                })

        # Calculate total focus time for the activity log
        total_focus_seconds = sum(e['duration_seconds'] for e in activity_log)

        return jsonify({
            "summary": summary,
            "screenshots": screenshots,
            "duration_minutes": duration_minutes,
            "window_durations": window_durations_list,  # For Time Breakdown section
            "activity_log": activity_log,  # Chronological list for Activity Log section
            "total_focus_seconds": total_focus_seconds,
            "focus_event_count": len(focus_events),
            "context_switches": context_switches,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/threshold-summaries/pending')
def api_get_pending_count():
    """Get count of screenshots waiting for summarization.

    Returns:
        {
            "unsummarized_count": 7,
            "frequency_minutes": 15,
            "minutes_until_next": 8
        }
    """
    try:
        storage = ActivityStorage()
        unsummarized = storage.get_unsummarized_screenshots()
        frequency_minutes = config_manager.config.summarization.frequency_minutes

        # Calculate minutes until next summary
        minutes_until_next = frequency_minutes
        last_summary = storage.get_last_threshold_summary()
        if last_summary:
            last_end_str = last_summary.get('end_time', '')
            try:
                from datetime import datetime
                if 'T' in last_end_str:
                    last_end = datetime.fromisoformat(last_end_str.replace('Z', '+00:00'))
                    if last_end.tzinfo:
                        last_end = last_end.replace(tzinfo=None)
                else:
                    last_end = datetime.strptime(last_end_str, '%Y-%m-%d %H:%M:%S')

                elapsed = datetime.now() - last_end
                elapsed_minutes = elapsed.total_seconds() / 60
                minutes_until_next = max(0, frequency_minutes - elapsed_minutes)
            except (ValueError, TypeError):
                pass

        return jsonify({
            "unsummarized_count": len(unsummarized),
            "frequency_minutes": frequency_minutes,
            "minutes_until_next": round(minutes_until_next, 1)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/threshold-summaries/worker-status')
def api_get_worker_status():
    """Get summarizer worker status.

    Returns:
        {"running": true, "current_task": "summarize", "queue_size": 2}
    """
    global summarizer_worker

    if summarizer_worker is None:
        return jsonify({
            "running": False,
            "current_task": None,
            "queue_size": 0,
            "note": "Worker not attached (daemon may not be running)"
        })

    return jsonify(summarizer_worker.get_status())


@app.route('/api/threshold-summaries/generate', methods=['POST'])
def api_force_generate_summaries():
    """Force immediate summarization of pending screenshots.

    Request body (optional):
        {"date": "2025-12-12"}  - Limit to specific day

    Returns:
        {"status": "queued", "count": 15}
        or {"error": "message"}
    """
    global summarizer_worker

    if summarizer_worker is None:
        return jsonify({
            "error": "Worker not attached (daemon may not be running)"
        }), 503

    # Get optional date filter from request body
    date = None
    if request.is_json and request.json:
        date = request.json.get('date')

    try:
        count = summarizer_worker.force_summarize_pending(date=date)
        if count == 0:
            return jsonify({
                "status": "no_pending",
                "count": 0,
                "message": f"No unsummarized screenshots{' for ' + date if date else ''}"
            })
        return jsonify({
            "status": "queued",
            "count": count,
            "date": date
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== Daily Rollup Summary API ====================

@app.route('/api/daily-summary/<date>')
def api_get_daily_summary(date):
    """Get the daily rollup summary for a date.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        {"date": "2024-12-10", "summary": "...", "created_at": "..."}
        or {"error": "No daily summary for this date"} with 404
    """
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        storage = ActivityStorage()
        summary = storage.get_daily_summary(date)

        if not summary:
            return jsonify({'error': 'No daily summary for this date'}), 404

        return jsonify(summary)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/daily-summary/<date>/generate', methods=['POST'])
def api_generate_daily_summary(date):
    """Generate a daily rollup summary from threshold summaries.

    Combines all AI summaries for the day into a single high-level overview.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        {"status": "success", "summary": "...", "source_count": 5}
    """
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format, use YYYY-MM-DD'}), 400

    try:
        storage = ActivityStorage()
        summaries = storage.get_threshold_summaries_for_date(date)

        if not summaries:
            return jsonify({'error': 'No AI summaries for this date to synthesize'}), 404

        # Prepare summary texts for synthesis
        # Limit to avoid exceeding model context window
        MAX_SUMMARIES = 20  # Keep at most 20 summaries
        MAX_SUMMARY_LENGTH = 150  # Truncate each summary
        MAX_TOTAL_CHARS = 6000  # Max total input size

        # If too many summaries, sample evenly throughout the day
        if len(summaries) > MAX_SUMMARIES:
            step = len(summaries) / MAX_SUMMARIES
            summaries = [summaries[int(i * step)] for i in range(MAX_SUMMARIES)]

        summary_texts = []
        for s in summaries:
            start_time = s['start_time']
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time)
            time_str = start_time.strftime('%H:%M')
            project = s.get('project', 'unknown')
            # Truncate long summaries
            summary_text = s['summary']
            if len(summary_text) > MAX_SUMMARY_LENGTH:
                summary_text = summary_text[:MAX_SUMMARY_LENGTH] + "..."
            summary_texts.append(f"[{time_str}] ({project}) {summary_text}")

        combined_input = "\n".join(summary_texts)

        # Final safety truncation
        if len(combined_input) > MAX_TOTAL_CHARS:
            combined_input = combined_input[:MAX_TOTAL_CHARS] + "\n..."

        # Use the summarizer to generate a daily rollup
        cfg = config_manager.config.summarization
        summarizer = HybridSummarizer(
            model=cfg.model,
            ollama_host=cfg.ollama_host,
        )

        if not summarizer.is_available():
            return jsonify({'error': 'Summarizer not available (check Ollama)'}), 503

        # Create a prompt for daily synthesis
        prompt = f"""Below are activity summaries from throughout the day. Write a 2-3 sentence high-level summary of the entire day's work.

Activity summaries:
{combined_input}

Write a concise daily summary (2-3 sentences max) focusing on the main accomplishments and activities. Output ONLY the summary, no preamble."""

        # Call Ollama directly for this synthesis
        import requests
        response = requests.post(
            f"{cfg.ollama_host}/api/generate",
            json={
                "model": cfg.model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        daily_summary = response.json().get('response', '').strip()

        # Save to database
        storage.save_daily_summary(date, daily_summary)

        return jsonify({
            'status': 'success',
            'summary': daily_summary,
            'source_count': len(summaries),
        })
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Ollama request failed: {e}'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== Report Generation API ====================

# Global report generator and exporter (lazy initialized)
_report_generator = None
_report_exporter = None


def get_report_generator():
    """Get a report generator with current settings.

    Note: This creates a fresh instance each time to ensure it uses
    the latest configuration (model, host, etc.) from settings.
    Reports aren't generated frequently enough for caching to matter.
    """
    from tracker.reports import ReportGenerator
    storage = ActivityStorage()
    try:
        # Use configured model and host from settings (fresh each time)
        cfg = config_manager.config.summarization
        summarizer = HybridSummarizer(
            model=cfg.model,
            ollama_host=cfg.ollama_host,
        )
    except Exception:
        summarizer = None
    return ReportGenerator(storage, summarizer, config_manager)


def get_report_exporter():
    """Get or create the report exporter instance."""
    global _report_exporter
    if _report_exporter is None:
        from tracker.report_export import ReportExporter
        _report_exporter = ReportExporter()
    return _report_exporter


@app.route('/reports')
def reports_page():
    """Render the reports page."""
    today = date.today()
    return render_template('reports.html',
                         today=today.strftime('%Y-%m-%d'),
                         page='reports')


@app.route('/api/reports/generate', methods=['POST'])
def api_generate_report():
    """Generate a report for a time range.

    Request body:
        {
            "time_range": "last week",
            "report_type": "summary",  // summary, detailed, standup
            "include_screenshots": true,
            "max_screenshots": 10
        }

    Returns:
        Report data as JSON including executive summary, sections, analytics
    """
    data = request.json or {}

    time_range = data.get('time_range')
    if not time_range:
        return jsonify({"error": "time_range is required"}), 400

    report_type = data.get('report_type', 'summary')
    if report_type not in ('summary', 'detailed', 'standup'):
        return jsonify({"error": "report_type must be summary, detailed, or standup"}), 400

    include_screenshots = data.get('include_screenshots', True)
    max_screenshots = data.get('max_screenshots', 10)
    skip_ai_summary = data.get('skip_ai_summary', False)  # Fast dashboard load

    try:
        storage = ActivityStorage()

        # Parse the time range
        from tracker.timeparser import TimeParser
        time_parser = TimeParser()
        start, end = time_parser.parse(time_range)
        is_single_day = start.date() == end.date()

        # For fast dashboard loading, skip the slow LLM call
        if skip_ai_summary:
            report = None
        else:
            generator = get_report_generator()

            # For single-day reports: cache daily report for "Daily Summaries" list,
            # but use full generate() for better sections from threshold summaries
            if is_single_day:
                date_str = start.strftime('%Y-%m-%d')
                # Cache daily report so it shows in saved reports list
                generator.generate_daily_report(date_str)
                # Use full generation for proper sections (from threshold summaries)
                report = generator.generate(
                    time_range=time_range,
                    report_type=report_type,
                    include_screenshots=include_screenshots,
                    max_screenshots=max_screenshots
                )
            else:
                # For multi-day reports: try cached synthesis first (much faster)
                report = generator.generate_from_cached(
                    time_range=time_range,
                    report_type=report_type,
                    include_screenshots=include_screenshots,
                    max_screenshots=max_screenshots
                )

                # Fall back to full generation if cache unavailable
                if report is None:
                    report = generator.generate(
                        time_range=time_range,
                        report_type=report_type,
                        include_screenshots=include_screenshots,
                        max_screenshots=max_screenshots
                    )

        # Get new metrics
        from tracker.tag_detector import get_tag_breakdown, get_tag_colors
        focus_events = storage.get_focus_events_in_range(start, end, require_session=True)
        tag_breakdown = get_tag_breakdown(focus_events)
        deep_work_percentage = storage.get_deep_work_percentage(start, end)
        longest_streak = storage.get_longest_streak(start, end)
        total_tracked_seconds = storage.get_total_tracked_time(start, end)

        # Get health metrics
        work_break_balance = storage.get_work_break_balance(start, end)
        meetings_data = storage.get_meetings_time(start, end)

        # Get timeline data for visualization
        timeline_events = []
        for event in focus_events:
            from tracker.tag_detector import detect_tag, get_tag_color
            tag = detect_tag(event.get('app_name'), event.get('window_title'))
            timeline_events.append({
                'start_time': event.get('start_time'),
                'end_time': event.get('end_time'),
                'duration_seconds': event.get('duration_seconds'),
                'app_name': event.get('app_name'),
                'window_title': event.get('window_title'),
                'tag': tag,
                'color': get_tag_color(tag)
            })

        # Get screenshots - from report if available, otherwise from storage
        key_screenshots = []
        if report:
            screenshots_source = report.key_screenshots
        else:
            # Get screenshots directly from storage
            screenshots_source = storage.get_screenshots_in_range(
                start, end,
                limit=max_screenshots
            )

        for s in screenshots_source:
            ts = s.get('timestamp')
            if isinstance(ts, int):
                ts_str = datetime.fromtimestamp(ts).isoformat()
            elif isinstance(ts, datetime):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts) if ts else ''

            # Add tag info to screenshots
            from tracker.tag_detector import detect_tag, get_tag_color
            tag = detect_tag(s.get('app_name'), s.get('window_title'))

            key_screenshots.append({
                'id': s.get('id'),
                'url': f"/screenshot/{s.get('id')}",
                'timestamp': ts_str,
                'window_title': s.get('window_title', ''),
                'app_name': s.get('app_name', ''),
                'tag': tag,
                'color': get_tag_color(tag)
            })

        # Build response
        response_data = {
            'time_range': time_range,
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            # Dashboard metrics (always available, fast)
            'dashboard': {
                'total_tracked_seconds': total_tracked_seconds,
                'deep_work_percentage': round(deep_work_percentage, 1),
                'longest_streak': {
                    'duration_seconds': longest_streak.get('duration_seconds', 0),
                    'start_time': longest_streak.get('start_time'),
                    'end_time': longest_streak.get('end_time'),
                    'app_name': longest_streak.get('app_name'),
                    'window_title': longest_streak.get('window_title'),
                },
                # Health metrics
                'work_break_balance': work_break_balance,
                'meetings': meetings_data,
                'tag_breakdown': [
                    {
                        'tag': tb.tag,
                        'total_seconds': tb.total_seconds,
                        'percentage': round(tb.percentage, 1),
                        'color': tb.color,
                        'windows': tb.windows
                    }
                    for tb in tag_breakdown
                ],
                'tag_colors': get_tag_colors(),
                'timeline_events': timeline_events
            },
            'key_screenshots': key_screenshots
        }

        # Add report data if available (not when skip_ai_summary=True)
        if report:
            response_data.update({
                'title': report.title,
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
            })
        else:
            response_data.update({
                'title': f"Activity Report: {time_range}",
                'generated_at': datetime.now().isoformat(),
                'executive_summary': None,  # Not loaded yet
                'sections': [],
                'analytics': None,
            })

        return jsonify(response_data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Report generation failed: {str(e)}'}), 500


@app.route('/api/reports/export', methods=['POST'])
def api_export_report():
    """Export report to file.

    If 'report' data is provided, exports directly (instant - no regeneration).
    If only time_range is provided, falls back to regenerating (slow - legacy behavior).

    Request body (preferred - instant export):
        {
            "report": { ... report data from generate endpoint ... },
            "format": "pdf"  // markdown, html, pdf, json
        }

    Request body (legacy - regenerates report):
        {
            "time_range": "last week",
            "report_type": "summary",
            "format": "pdf"
        }

    Returns:
        {
            "path": "/path/to/file",
            "filename": "Activity_Report_20251209_143000.pdf",
            "download_url": "/reports/download/Activity_Report_20251209_143000.pdf"
        }
    """
    data = request.json or {}
    export_format = data.get('format', 'markdown')

    if export_format not in ('markdown', 'html', 'pdf', 'json'):
        return jsonify({"error": "format must be markdown, html, pdf, or json"}), 400

    try:
        exporter = get_report_exporter()

        # Preferred: use provided report data (instant export)
        report_data = data.get('report')
        if report_data:
            path = exporter.export_from_dict(report_data, format=export_format)
        else:
            # Legacy fallback: regenerate report (slow)
            time_range = data.get('time_range')
            if not time_range:
                return jsonify({"error": "Either 'report' data or 'time_range' is required"}), 400

            report_type = data.get('report_type', 'summary')
            generator = get_report_generator()
            report = generator.generate(
                time_range=time_range,
                report_type=report_type
            )
            path = exporter.export(report, format=export_format)

        return jsonify({
            'path': str(path),
            'filename': path.name,
            'download_url': f"/reports/download/{path.name}"
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Export failed: {str(e)}'}), 500


@app.route('/api/reports/capabilities', methods=['GET'])
def api_report_capabilities():
    """Get report export capabilities.

    Returns:
        {
            "formats": ["markdown", "html", "json", "pdf"],
            "pdf_available": true/false,
            "pdf_message": "Install weasyprint for PDF support" (if unavailable)
        }
    """
    from tracker.report_export import is_pdf_available

    pdf_available = is_pdf_available()
    formats = ['markdown', 'html', 'json']
    if pdf_available:
        formats.append('pdf')

    return jsonify({
        'formats': formats,
        'pdf_available': pdf_available,
        'pdf_message': None if pdf_available else 'PDF export requires weasyprint. Install with: pip install weasyprint'
    })


@app.route('/api/reports/history', methods=['GET'])
def api_report_history():
    """Get exported reports history.

    Query params:
        limit: Max number of records (default 50)
        offset: Number of records to skip (default 0)

    Returns:
        {
            "reports": [
                {
                    "id": 1,
                    "title": "Activity Report: Today",
                    "time_range": "today",
                    "report_type": "summary",
                    "format": "json",
                    "filename": "Activity_Report_...",
                    "download_url": "/reports/download/...",
                    "file_size": 12345,
                    "created_at": "2025-12-30T12:00:00"
                },
                ...
            ]
        }
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)

    storage = ActivityStorage()
    reports = storage.get_exported_reports(limit=limit, offset=offset)

    # Add download URLs and format file sizes
    for r in reports:
        r['download_url'] = f"/reports/download/{r['filename']}"
        if r.get('file_size'):
            # Format as KB/MB
            size = r['file_size']
            if size > 1024 * 1024:
                r['file_size_display'] = f"{size / (1024 * 1024):.1f} MB"
            elif size > 1024:
                r['file_size_display'] = f"{size / 1024:.1f} KB"
            else:
                r['file_size_display'] = f"{size} B"

    return jsonify({'reports': reports})


@app.route('/api/reports/history/<int:report_id>', methods=['DELETE'])
def api_delete_report_history(report_id):
    """Delete an exported report from history.

    Note: This only removes the database record. The file remains on disk.

    Returns:
        {"success": true} or {"error": "..."}
    """
    storage = ActivityStorage()
    if storage.delete_exported_report(report_id):
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Report not found'}), 404


@app.route('/api/reports/saved', methods=['GET'])
def api_saved_reports():
    """Get list of saved/cached reports.

    Returns cached daily reports that can be re-exported in any format.

    Query params:
        days_back: How many days to look back (default 30)

    Returns:
        {
            "reports": [
                {
                    "id": 1,
                    "period_type": "daily",
                    "period_date": "2025-12-30",
                    "executive_summary": "...",
                    "total_minutes": 240,
                    "created_at": "2025-12-31T00:05:00"
                },
                ...
            ]
        }
    """
    days_back = request.args.get('days_back', 30, type=int)

    storage = ActivityStorage()

    # Get cached reports for the period
    end = datetime.now()
    start = end - timedelta(days=days_back)
    cached = storage.get_cached_reports_in_range('daily', start, end)

    reports = []
    for r in cached:
        # Parse analytics to get total minutes
        analytics = r.get('analytics_json', '{}')
        if isinstance(analytics, str):
            analytics = json.loads(analytics)

        reports.append({
            'id': r.get('id'),
            'period_type': r.get('period_type', 'daily'),
            'period_date': r.get('period_date'),
            'executive_summary': (r.get('executive_summary', '') or '')[:200] + '...',
            'total_minutes': analytics.get('total_active_minutes', 0),
            'created_at': r.get('created_at'),
        })

    # Sort by date descending
    reports.sort(key=lambda x: x['period_date'], reverse=True)

    return jsonify({'reports': reports})


@app.route('/api/reports/saved/<period_date>', methods=['GET'])
def api_get_saved_report(period_date):
    """Get a specific saved daily report.

    Returns the full report data that can be used for export.

    Returns:
        Full report data in the same format as /api/reports/generate
    """
    storage = ActivityStorage()
    cached = storage.get_cached_report('daily', period_date)

    if not cached:
        return jsonify({'error': 'Report not found'}), 404

    # Parse JSON fields
    analytics = cached.get('analytics_json', '{}')
    if isinstance(analytics, str):
        analytics = json.loads(analytics)

    sections = cached.get('sections_json', '[]')
    if isinstance(sections, str):
        sections = json.loads(sections)

    # Build response in same format as generate endpoint
    return jsonify({
        'title': f"Activity Report: {period_date}",
        'time_range': period_date,
        'generated_at': cached.get('created_at', ''),
        'executive_summary': cached.get('executive_summary', ''),
        'sections': sections,
        'analytics': analytics,
        'key_screenshots': [],  # Can be added later if needed
    })


@app.route('/reports/download/<filename>')
def download_report(filename):
    """Download exported report file."""
    from flask import send_from_directory

    exporter = get_report_exporter()

    # Security check: ensure filename doesn't contain path traversal
    if '..' in filename or filename.startswith('/'):
        abort(400, "Invalid filename")

    return send_from_directory(
        exporter.output_dir,
        filename,
        as_attachment=True
    )


@app.route('/api/reports/presets', methods=['GET'])
def api_report_presets():
    """Get common report presets.

    Returns:
        {
            "presets": [
                {"name": "Today", "time_range": "today", "type": "summary"},
                ...
            ]
        }
    """
    return jsonify({
        'presets': [
            {'name': 'Today', 'time_range': 'today', 'type': 'summary'},
            {'name': 'Yesterday', 'time_range': 'yesterday', 'type': 'summary'},
            {'name': 'This Week', 'time_range': 'this week', 'type': 'summary'},
            {'name': 'Last Week', 'time_range': 'last week', 'type': 'detailed'},
            {'name': 'This Month', 'time_range': 'this month', 'type': 'detailed'},
            {'name': 'Standup (Today)', 'time_range': 'since this morning', 'type': 'standup'},
            {'name': 'Standup (Yesterday)', 'time_range': 'yesterday', 'type': 'standup'},
        ]
    })


# ==================== Tag Management API ====================

@app.route('/api/tags', methods=['GET'])
def api_get_all_tags():
    """Get all unique tags with their occurrence counts.

    Returns:
        {
            "tags": [
                {"tag": "debugging", "count": 12},
                {"tag": "development", "count": 9},
                ...
            ],
            "total_unique": 182
        }
    """
    try:
        storage = ActivityStorage()
        tag_counts = storage.get_all_tags()

        # Sort by count descending, then alphabetically
        sorted_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
        tags_list = [{"tag": tag, "count": count} for tag, count in sorted_tags]

        return jsonify({
            "tags": tags_list,
            "total_unique": len(tags_list)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _normalize_tag(tag: str) -> str:
    """Normalize a tag to canonical form for comparison.

    Converts to lowercase, replaces spaces/underscores with hyphens,
    and strips whitespace.
    """
    return tag.lower().strip().replace(' ', '-').replace('_', '-')


@app.route('/api/tags/suggest-consolidation', methods=['POST'])
def api_suggest_tag_consolidation():
    """Suggest tag consolidation groups using algorithmic matching.

    Uses normalization to find duplicates instantly (no LLM needed).
    Groups tags that normalize to the same form (case, spaces, hyphens, underscores).

    Request body (optional):
        {"min_count": 1}  - Minimum occurrence to include tag

    Returns:
        {
            "consolidations": [
                {
                    "canonical": "debugging",
                    "variants": ["debugging", "Debugging"],
                    "total_count": 17
                },
                ...
            ]
        }
    """
    data = request.json or {}
    min_count = data.get('min_count', 1)

    try:
        storage = ActivityStorage()
        tag_counts = storage.get_all_tags()

        # Filter by min_count
        filtered_tags = {k: v for k, v in tag_counts.items() if v >= min_count}

        if len(filtered_tags) < 2:
            return jsonify({
                "consolidations": [],
                "message": "Not enough tags to analyze"
            })

        # Group tags by their normalized form
        from collections import defaultdict
        groups = defaultdict(list)
        for tag in filtered_tags.keys():
            normalized = _normalize_tag(tag)
            groups[normalized].append(tag)

        # Build consolidation suggestions (only groups with 2+ variants)
        consolidations = []
        for normalized, variants in sorted(groups.items()):
            if len(variants) >= 2:
                # Use the normalized form as canonical
                canonical = normalized
                total_count = sum(tag_counts.get(v, 0) for v in variants)
                consolidations.append({
                    "canonical": canonical,
                    "variants": sorted(variants),
                    "total_count": total_count
                })

        # Sort by total count descending
        consolidations.sort(key=lambda x: -x['total_count'])

        return jsonify({
            "consolidations": consolidations,
            "method": "algorithmic"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tags/consolidate', methods=['POST'])
def api_consolidate_tags():
    """Apply tag consolidation by updating tags in database.

    Request body:
        {
            "consolidations": [
                {
                    "canonical": "debugging",
                    "variants": ["Debugging", "debug", "Debug"]
                },
                ...
            ]
        }

    Returns:
        {"status": "success", "updated_summaries": 15, "tags_consolidated": 3}
    """
    data = request.json or {}
    consolidations = data.get('consolidations', [])

    if not consolidations:
        return jsonify({"error": "No consolidations provided"}), 400

    try:
        storage = ActivityStorage()
        total_updated = 0
        tags_consolidated = 0

        for group in consolidations:
            canonical = group.get('canonical')
            variants = group.get('variants', [])

            if not canonical or not variants:
                continue

            # Remove canonical from variants if present (don't replace itself)
            variants_to_replace = [v for v in variants if v != canonical]

            if not variants_to_replace:
                continue

            updated = storage.consolidate_tags(canonical, variants_to_replace)
            total_updated += updated
            if updated > 0:
                tags_consolidated += 1

        return jsonify({
            "status": "success",
            "updated_summaries": total_updated,
            "tags_consolidated": tags_consolidated
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== Hierarchical Summaries (Daily/Weekly/Monthly) ====================

@app.route('/summary/daily/<date>')
def hierarchical_summary_daily(date):
    """Show detail page for a daily summary."""
    return render_template('hierarchical_summary_detail.html',
                         period_type='daily',
                         period_date=date,
                         page='reports')


@app.route('/summary/weekly/<week>')
def hierarchical_summary_weekly(week):
    """Show detail page for a weekly summary."""
    return render_template('hierarchical_summary_detail.html',
                         period_type='weekly',
                         period_date=week,
                         page='reports')


@app.route('/summary/monthly/<month>')
def hierarchical_summary_monthly(month):
    """Show detail page for a monthly summary."""
    return render_template('hierarchical_summary_detail.html',
                         period_type='monthly',
                         period_date=month,
                         page='reports')


@app.route('/api/hierarchical-summaries/<period_type>/<period_date>')
def api_get_hierarchical_summary(period_type, period_date):
    """Get a hierarchical summary (daily/weekly/monthly).

    Returns:
        Full summary data including child summaries and analytics.
    """
    if period_type not in ('daily', 'weekly', 'monthly'):
        return jsonify({"error": "period_type must be 'daily', 'weekly', or 'monthly'"}), 400

    try:
        storage = ActivityStorage()
        report = storage.get_cached_report(period_type, period_date)

        if not report:
            return jsonify({"error": f"No {period_type} summary found for {period_date}"}), 404

        # Get child summaries for detail view
        child_summaries = []
        if report.get('child_summary_ids'):
            for child_id in report['child_summary_ids'][:20]:  # Limit for performance
                if period_type == 'daily':
                    # Children are threshold summaries
                    child = storage.get_threshold_summary(child_id)
                else:
                    # Children are cached reports (daily for weekly, weekly for monthly)
                    child_type = 'daily' if period_type == 'weekly' else 'weekly'
                    # Query by ID
                    with storage.get_connection() as conn:
                        cursor = conn.execute(
                            "SELECT * FROM cached_reports WHERE id = ?", (child_id,)
                        )
                        row = cursor.fetchone()
                        child = dict(row) if row else None

                if child:
                    child_summaries.append({
                        'id': child.get('id'),
                        'start_time': child.get('start_time'),
                        'end_time': child.get('end_time'),
                        'summary': child.get('summary') or child.get('executive_summary'),
                        'period_type': child.get('period_type'),
                        'period_date': child.get('period_date'),
                    })

        response = {
            'id': report.get('id'),
            'period_type': period_type,
            'period_date': period_date,
            'start_time': report.get('start_time'),
            'end_time': report.get('end_time'),
            'executive_summary': report.get('executive_summary'),
            'explanation': report.get('explanation'),
            'tags': report.get('tags'),
            'confidence': report.get('confidence'),
            'analytics': report.get('analytics'),
            'model_used': report.get('model_used'),
            'inference_time_ms': report.get('inference_time_ms'),
            'prompt_text': report.get('prompt_text'),
            'created_at': report.get('created_at'),
            'regenerated_at': report.get('regenerated_at'),
            'child_summaries': child_summaries,
        }

        return jsonify(response)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/hierarchical-summaries/<period_type>/<period_date>/regenerate', methods=['POST'])
def api_regenerate_hierarchical_summary(period_type, period_date):
    """Queue regeneration of a hierarchical summary.

    Returns:
        {"status": "queued", "period_type": "daily", "period_date": "2024-12-30"}
    """
    if period_type not in ('daily', 'weekly', 'monthly'):
        return jsonify({"error": "period_type must be 'daily', 'weekly', or 'monthly'"}), 400

    try:
        # Queue regeneration through the worker
        worker = get_summarizer_worker()
        if worker:
            worker.queue_regenerate_report(period_type, period_date)
            return jsonify({
                "status": "queued",
                "period_type": period_type,
                "period_date": period_date
            })
        else:
            return jsonify({"error": "Summarizer worker not running"}), 503

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/hierarchical-summaries/<period_type>/<period_date>', methods=['DELETE'])
def api_delete_hierarchical_summary(period_type, period_date):
    """Delete a hierarchical summary.

    Returns:
        {"status": "deleted", "period_type": "daily", "period_date": "2024-12-30"}
    """
    if period_type not in ('daily', 'weekly', 'monthly'):
        return jsonify({"error": "period_type must be 'daily', 'weekly', or 'monthly'"}), 400

    try:
        storage = ActivityStorage()
        deleted = storage.delete_cached_report(period_type, period_date)

        if deleted:
            return jsonify({
                "status": "deleted",
                "period_type": period_type,
                "period_date": period_date
            })
        else:
            return jsonify({"error": f"No {period_type} summary found for {period_date}"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/hierarchical-summaries/<period_type>/<period_date>/generate', methods=['POST'])
def api_generate_hierarchical_summary(period_type, period_date):
    """Generate a hierarchical summary on-demand.

    Returns:
        {"status": "generated", "period_type": "daily", "period_date": "2024-12-30"}
    """
    if period_type not in ('daily', 'weekly', 'monthly'):
        return jsonify({"error": "period_type must be 'daily', 'weekly', or 'monthly'"}), 400

    try:
        from tracker.reports import ReportGenerator
        from tracker.config import ConfigManager

        storage = ActivityStorage()
        config = ConfigManager()

        # Get summarizer from worker if available
        worker = get_summarizer_worker()
        summarizer = worker.summarizer if worker else None

        generator = ReportGenerator(storage, summarizer, config)

        if period_type == 'daily':
            result = generator.generate_daily_report(period_date)
        elif period_type == 'weekly':
            result = generator.generate_weekly_report(period_date)
        else:
            result = generator.generate_monthly_report(period_date)

        if result:
            return jsonify({
                "status": "generated",
                "period_type": period_type,
                "period_date": period_date
            })
        else:
            return jsonify({"error": f"No data available to generate {period_type} summary for {period_date}"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/hierarchical-summaries/list/<period_type>')
def api_list_hierarchical_summaries(period_type):
    """List available hierarchical summaries of a given type.

    Query params:
        limit: Maximum number of results (default 30)
        offset: Offset for pagination (default 0)

    Returns:
        {"summaries": [...], "total": 45}
    """
    if period_type not in ('daily', 'weekly', 'monthly'):
        return jsonify({"error": "period_type must be 'daily', 'weekly', or 'monthly'"}), 400

    limit = request.args.get('limit', 30, type=int)
    offset = request.args.get('offset', 0, type=int)

    try:
        storage = ActivityStorage()

        with storage.get_connection() as conn:
            # Get total count
            count_cursor = conn.execute(
                "SELECT COUNT(*) FROM cached_reports WHERE period_type = ?",
                (period_type,)
            )
            total = count_cursor.fetchone()[0]

            # Get summaries with pagination
            cursor = conn.execute(
                """
                SELECT id, period_type, period_date, start_time, end_time,
                       executive_summary, tags, confidence, model_used,
                       inference_time_ms, created_at, regenerated_at
                FROM cached_reports
                WHERE period_type = ?
                ORDER BY period_date DESC
                LIMIT ? OFFSET ?
                """,
                (period_type, limit, offset)
            )

            summaries = []
            for row in cursor.fetchall():
                summary = dict(row)
                if summary.get('tags'):
                    import json
                    summary['tags'] = json.loads(summary['tags'])
                summaries.append(summary)

        return jsonify({
            "summaries": summaries,
            "total": total,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
