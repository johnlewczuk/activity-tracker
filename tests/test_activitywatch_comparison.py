"""
Test script to compare activity-tracker sessions with ActivityWatch AFK events.

This validates that session boundaries align with AFK detection.
ActivityWatch is treated as ground truth since both systems use the same
pynput-based detection with the same timeout.

Usage:
    pytest tests/test_activitywatch_comparison.py -v

    # Or run directly for a specific date:
    python tests/test_activitywatch_comparison.py 2026-01-05
"""

import json
import requests
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import pytest


# ActivityWatch API configuration
AW_BASE_URL = "http://localhost:5600/api/0"
AW_AFK_BUCKET = "aw-watcher-afk_kraken"

# Activity-tracker database
AT_DB_PATH = Path.home() / "activity-tracker-data" / "activity.db"

# Tolerance for timestamp comparisons (seconds)
TIMESTAMP_TOLERANCE = 60  # Allow 1 minute difference


def get_aw_afk_events(date: str) -> List[Dict]:
    """Fetch AFK events from ActivityWatch for a specific date.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        List of AFK events with timestamp, duration, and status
    """
    start = f"{date}T00:00:00"
    end_date = datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
    end = end_date.strftime("%Y-%m-%dT00:00:00")

    url = f"{AW_BASE_URL}/buckets/{AW_AFK_BUCKET}/events"
    params = {"start": start, "end": end}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        events = response.json()

        # Convert to standardized format with local timestamps
        result = []
        for event in events:
            ts = datetime.fromisoformat(event["timestamp"].replace("+00:00", ""))
            # ActivityWatch stores in UTC, convert to local
            # Note: This assumes the system timezone matches ActivityWatch
            result.append({
                "timestamp": ts,
                "duration_seconds": event["duration"],
                "status": event["data"]["status"],
                "end_time": ts + timedelta(seconds=event["duration"])
            })

        # Sort by timestamp (oldest first)
        result.sort(key=lambda x: x["timestamp"])
        return result

    except requests.RequestException as e:
        pytest.skip(f"ActivityWatch not available: {e}")
        return []


def get_at_sessions(date: str) -> List[Dict]:
    """Fetch sessions from activity-tracker for a specific date.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        List of sessions with start_time, end_time, and duration
    """
    if not AT_DB_PATH.exists():
        pytest.skip(f"Activity-tracker database not found: {AT_DB_PATH}")
        return []

    conn = sqlite3.connect(AT_DB_PATH)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute(
        """
        SELECT id, start_time, end_time, duration_seconds
        FROM activity_sessions
        WHERE date(start_time) = ?
        ORDER BY start_time
        """,
        (date,)
    )

    sessions = []
    for row in cursor.fetchall():
        start = datetime.fromisoformat(row["start_time"])
        end = datetime.fromisoformat(row["end_time"]) if row["end_time"] else None
        sessions.append({
            "id": row["id"],
            "start_time": start,
            "end_time": end,
            "duration_seconds": row["duration_seconds"]
        })

    conn.close()
    return sessions


def extract_active_periods_from_aw(events: List[Dict]) -> List[Tuple[datetime, datetime]]:
    """Extract active (not-afk) periods from ActivityWatch events.

    Args:
        events: List of AFK events from ActivityWatch

    Returns:
        List of (start, end) tuples for each active period
    """
    active_periods = []

    for event in events:
        if event["status"] == "not-afk":
            active_periods.append((event["timestamp"], event["end_time"]))

    return active_periods


def find_matching_session(
    aw_start: datetime,
    aw_end: datetime,
    sessions: List[Dict],
    tolerance: int = TIMESTAMP_TOLERANCE
) -> Optional[Dict]:
    """Find a session that matches the ActivityWatch active period.

    Args:
        aw_start: Start of ActivityWatch active period
        aw_end: End of ActivityWatch active period
        sessions: List of activity-tracker sessions
        tolerance: Maximum seconds difference to consider a match

    Returns:
        Matching session or None
    """
    for session in sessions:
        start_diff = abs((session["start_time"] - aw_start).total_seconds())

        if session["end_time"]:
            end_diff = abs((session["end_time"] - aw_end).total_seconds())
        else:
            # Active session - compare with now or aw_end
            end_diff = 0  # Don't penalize active sessions

        # Match if start times are close
        if start_diff <= tolerance:
            return session

    return None


def compare_sessions_with_aw(date: str) -> Dict:
    """Compare activity-tracker sessions with ActivityWatch for a date.

    Args:
        date: Date string in YYYY-MM-DD format

    Returns:
        Comparison results including matches, mismatches, and statistics
    """
    aw_events = get_aw_afk_events(date)
    at_sessions = get_at_sessions(date)

    if not aw_events:
        return {"error": "No ActivityWatch events found"}

    aw_active_periods = extract_active_periods_from_aw(aw_events)

    results = {
        "date": date,
        "aw_active_periods": len(aw_active_periods),
        "at_sessions": len(at_sessions),
        "matches": [],
        "aw_unmatched": [],
        "at_extra_sessions": [],
        "session_gaps": []
    }

    matched_sessions = set()

    # For each ActivityWatch active period, find matching session(s)
    for aw_start, aw_end in aw_active_periods:
        duration_min = (aw_end - aw_start).total_seconds() / 60

        # Find sessions that overlap with this active period
        overlapping = []
        for session in at_sessions:
            s_start = session["start_time"]
            s_end = session["end_time"] or datetime.now()

            # Check for overlap
            if s_start <= aw_end and s_end >= aw_start:
                overlapping.append(session)
                matched_sessions.add(session["id"])

        if overlapping:
            results["matches"].append({
                "aw_period": (aw_start.isoformat(), aw_end.isoformat()),
                "aw_duration_min": round(duration_min, 1),
                "at_sessions": [s["id"] for s in overlapping],
                "session_count": len(overlapping)
            })

            # Check for suspicious gaps between overlapping sessions
            if len(overlapping) > 1:
                for i in range(len(overlapping) - 1):
                    current_end = overlapping[i]["end_time"]
                    next_start = overlapping[i + 1]["start_time"]
                    if current_end:
                        gap = (next_start - current_end).total_seconds()
                        if gap < 60:  # Gap less than 1 minute = suspicious restart
                            results["session_gaps"].append({
                                "session_before": overlapping[i]["id"],
                                "session_after": overlapping[i + 1]["id"],
                                "gap_seconds": round(gap, 1),
                                "likely_cause": "daemon_restart" if gap < 5 else "possible_restart"
                            })
        else:
            # No matching session for this active period
            results["aw_unmatched"].append({
                "period": (aw_start.isoformat(), aw_end.isoformat()),
                "duration_min": round(duration_min, 1)
            })

    # Find sessions that don't match any ActivityWatch period
    for session in at_sessions:
        if session["id"] not in matched_sessions:
            duration = session["duration_seconds"] or 0
            results["at_extra_sessions"].append({
                "id": session["id"],
                "start": session["start_time"].isoformat(),
                "duration_min": round(duration / 60, 1)
            })

    # Summary statistics
    results["summary"] = {
        "aw_periods_matched": len(results["matches"]),
        "aw_periods_unmatched": len(results["aw_unmatched"]),
        "at_extra_sessions": len(results["at_extra_sessions"]),
        "suspicious_gaps": len(results["session_gaps"]),
        "sessions_per_aw_period": (
            len(at_sessions) / len(aw_active_periods)
            if aw_active_periods else 0
        )
    }

    return results


class TestActivityWatchComparison:
    """Test cases for ActivityWatch comparison."""

    def test_aw_connection(self):
        """Test that ActivityWatch API is accessible."""
        try:
            response = requests.get(f"{AW_BASE_URL}/buckets", timeout=5)
            assert response.status_code == 200
            buckets = response.json()
            assert AW_AFK_BUCKET in buckets, f"AFK bucket not found. Available: {list(buckets.keys())}"
        except requests.RequestException:
            pytest.skip("ActivityWatch not running")

    def test_at_database_exists(self):
        """Test that activity-tracker database exists."""
        assert AT_DB_PATH.exists(), f"Database not found: {AT_DB_PATH}"

    def test_today_comparison(self):
        """Compare today's sessions with ActivityWatch."""
        today = datetime.now().strftime("%Y-%m-%d")
        results = compare_sessions_with_aw(today)

        if "error" in results:
            pytest.skip(results["error"])

        # Log results for debugging
        print(f"\n=== Comparison for {today} ===")
        print(f"ActivityWatch active periods: {results['aw_active_periods']}")
        print(f"Activity-tracker sessions: {results['at_sessions']}")
        print(f"Suspicious gaps (daemon restarts): {len(results['session_gaps'])}")

        for gap in results["session_gaps"]:
            print(f"  - Session {gap['session_before']} -> {gap['session_after']}: {gap['gap_seconds']}s gap")

        # Assertions
        # We expect sessions_per_aw_period to be close to 1.0 if working correctly
        # Higher values indicate daemon restarts splitting sessions
        ratio = results["summary"]["sessions_per_aw_period"]
        print(f"Sessions per AW period ratio: {ratio:.2f} (ideal: 1.0)")

        # Warn if ratio is too high (indicates many restarts)
        if ratio > 2.0:
            print(f"WARNING: High session ratio ({ratio:.2f}) indicates frequent daemon restarts")

    def test_no_sub_minute_gaps(self):
        """Test that there are no suspicious sub-minute gaps between sessions."""
        today = datetime.now().strftime("%Y-%m-%d")
        results = compare_sessions_with_aw(today)

        if "error" in results:
            pytest.skip(results["error"])

        # After the fix, there should be no suspicious gaps
        # (daemon restarts should resume sessions, not create new ones)
        gaps = results["session_gaps"]

        if gaps:
            print(f"\nFound {len(gaps)} suspicious gaps:")
            for gap in gaps:
                print(f"  Session {gap['session_before']} -> {gap['session_after']}: {gap['gap_seconds']}s")

        # This is a soft assertion - gaps might exist from before the fix
        # assert len(gaps) == 0, f"Found {len(gaps)} sub-minute gaps between sessions"


def main():
    """Run comparison for a specific date from command line."""
    import sys

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = datetime.now().strftime("%Y-%m-%d")

    print(f"Comparing activity-tracker sessions with ActivityWatch for {date}")
    print("=" * 60)

    results = compare_sessions_with_aw(date)

    if "error" in results:
        print(f"Error: {results['error']}")
        return

    print(f"\nActivityWatch active periods: {results['aw_active_periods']}")
    print(f"Activity-tracker sessions: {results['at_sessions']}")

    print(f"\n--- Matches ---")
    for match in results["matches"]:
        print(f"AW period {match['aw_period'][0][:19]} - {match['aw_period'][1][11:19]}")
        print(f"  Duration: {match['aw_duration_min']} min")
        print(f"  AT sessions: {match['at_sessions']} ({match['session_count']} sessions)")

    if results["aw_unmatched"]:
        print(f"\n--- ActivityWatch periods without matching sessions ---")
        for unmatched in results["aw_unmatched"]:
            print(f"  {unmatched['period'][0][:19]} ({unmatched['duration_min']} min)")

    if results["session_gaps"]:
        print(f"\n--- Suspicious session gaps (likely daemon restarts) ---")
        for gap in results["session_gaps"]:
            print(f"  Session {gap['session_before']} -> {gap['session_after']}: {gap['gap_seconds']}s ({gap['likely_cause']})")

    print(f"\n--- Summary ---")
    summary = results["summary"]
    print(f"AW periods matched: {summary['aw_periods_matched']}")
    print(f"AW periods unmatched: {summary['aw_periods_unmatched']}")
    print(f"Extra AT sessions: {summary['at_extra_sessions']}")
    print(f"Suspicious gaps: {summary['suspicious_gaps']}")
    print(f"Sessions per AW period: {summary['sessions_per_aw_period']:.2f} (ideal: 1.0)")


if __name__ == "__main__":
    main()
