#!/usr/bin/env python3

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


@app.route('/day/<date_string>')
def day_view(date_string):
    """Show screenshots for a specific day (YYYY-MM-DD format)."""
    try:
        target_date = datetime.strptime(date_string, '%Y-%m-%d').date()
    except ValueError:
        abort(400, "Invalid date format. Use YYYY-MM-DD.")

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


# Session summarization state
session_summarization_state = {
    "running": False,
    "session_id": None,
    "error": None,
}


def _run_session_summarization(session_id: int):
    """Background thread function to run session summarization."""
    global session_summarization_state

    try:
        storage = ActivityStorage()
        summarizer = HybridSummarizer()

        if not summarizer.is_available():
            session_summarization_state["error"] = "Summarizer not available"
            session_summarization_state["running"] = False
            return

        # Get session
        session = storage.get_session(session_id)
        if not session:
            session_summarization_state["error"] = "Session not found"
            session_summarization_state["running"] = False
            return

        # Get screenshots
        screenshots = storage.get_session_screenshots(session_id)
        if len(screenshots) < 2:
            session_summarization_state["error"] = "Not enough screenshots"
            session_summarization_state["running"] = False
            return

        # Process OCR
        unique_titles = storage.get_unique_window_titles_for_session(session_id)
        ocr_texts = []

        for title in unique_titles:
            cached = storage.get_cached_ocr(session_id, title)
            if cached is not None:
                ocr_texts.append({"window_title": title, "ocr_text": cached})
                continue

            for s in screenshots:
                if s.get("window_title") == title:
                    try:
                        # Use cropped version for better OCR accuracy
                        cropped_path = summarizer.get_cropped_path(s)
                        ocr_text = summarizer.extract_ocr(cropped_path)
                        storage.cache_ocr(session_id, title, ocr_text, s["id"])
                        ocr_texts.append({"window_title": title, "ocr_text": ocr_text})
                    except Exception:
                        pass
                    break

        # Get previous summary for context
        recent_summaries = storage.get_recent_summaries(1)
        previous_summary = recent_summaries[0] if recent_summaries else None

        # Generate summary
        summary, inference_ms, prompt_text, screenshot_ids_used = summarizer.summarize_session(
            screenshots=screenshots,
            ocr_texts=ocr_texts,
            previous_summary=previous_summary,
        )

        # Save
        storage.save_session_summary(
            session_id=session_id,
            summary=summary,
            model=summarizer.model,
            inference_ms=inference_ms,
            prompt_text=prompt_text,
            screenshot_ids_used=screenshot_ids_used,
        )

    except Exception as e:
        session_summarization_state["error"] = str(e)
    finally:
        session_summarization_state["running"] = False
        session_summarization_state["session_id"] = None


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


@app.route('/api/sessions/<int:session_id>/summarize', methods=['POST'])
def api_summarize_session(session_id):
    """Manually trigger summarization for a specific session.

    Returns:
        {"status": "started"} or {"status": "already_summarized"} or error
    """
    global session_summarization_state

    if session_summarization_state["running"]:
        return jsonify({
            "status": "already_running",
            "session_id": session_summarization_state["session_id"],
        }), 409

    try:
        storage = ActivityStorage()

        # Verify session exists
        session = storage.get_session(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        # Check if already summarized
        if session.get("summary") and not (request.json or {}).get("force"):
            return jsonify({
                "status": "already_summarized",
                "summary": session["summary"],
            })

        # Start background summarization
        session_summarization_state.update({
            "running": True,
            "session_id": session_id,
            "error": None,
        })

        thread = threading.Thread(
            target=_run_session_summarization,
            args=(session_id,)
        )
        thread.daemon = True
        thread.start()

        return jsonify({"status": "started"})

    except Exception as e:
        return jsonify({"error": f"Failed to start summarization: {str(e)}"}), 500


@app.route('/api/sessions/<int:session_id>/summarize/status')
def api_session_summarize_status(session_id):
    """Get current session summarization progress."""
    return jsonify({
        "running": session_summarization_state["running"] and session_summarization_state["session_id"] == session_id,
        "session_id": session_summarization_state["session_id"],
        "error": session_summarization_state["error"],
    })


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
        {"summaries": [...], "date": "2025-12-09"}
    """
    try:
        storage = ActivityStorage()
        summaries = storage.get_threshold_summaries_for_date(date)
        return jsonify({
            "date": date,
            "summaries": summaries
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route('/api/threshold-summaries/<int:summary_id>/history')
def api_get_summary_history(summary_id):
    """Get all versions of a summary (original + regenerations).

    Returns:
        {
            "original_id": 1,
            "versions": [...],
            "current_config": {...}
        }
    """
    try:
        storage = ActivityStorage()
        original = storage.get_threshold_summary(summary_id)

        if not original:
            return jsonify({"error": "Summary not found"}), 404

        # Find the root if this is a regeneration
        root_id = summary_id
        while original and original.get('regenerated_from'):
            root_id = original['regenerated_from']
            original = storage.get_threshold_summary(root_id)

        # Get all versions
        versions = storage.get_summary_versions(root_id)

        return jsonify({
            "original_id": root_id,
            "versions": versions,
            "current_config": config_manager.to_dict()['summarization']
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route('/api/threshold-summaries/pending')
def api_get_pending_count():
    """Get count of screenshots waiting for summarization.

    Returns:
        {
            "unsummarized_count": 7,
            "threshold": 10,
            "ready_batches": 0
        }
    """
    try:
        storage = ActivityStorage()
        unsummarized = storage.get_unsummarized_screenshots()
        threshold = config_manager.config.summarization.trigger_threshold

        return jsonify({
            "unsummarized_count": len(unsummarized),
            "threshold": threshold,
            "ready_batches": len(unsummarized) // threshold
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=55555)
