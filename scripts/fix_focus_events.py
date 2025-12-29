#!/usr/bin/env python3
"""
Migration script to fix historical focus event data.

Fixes two issues:
1. Focus events with wrong session_id (assigned to wrong session)
2. Focus events with inflated durations (include AFK time)

Run with --dry-run first to see what would be changed.

Usage:
    python scripts/fix_focus_events.py --dry-run
    python scripts/fix_focus_events.py --apply
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def get_db_connection():
    """Get connection to activity database."""
    db_path = Path.home() / "activity-tracker-data" / "activity.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def find_correct_session(conn, focus_start: str) -> int | None:
    """Find the session that was active when the focus event started."""
    cursor = conn.execute(
        """
        SELECT id FROM activity_sessions
        WHERE datetime(start_time) <= datetime(?)
          AND (end_time IS NULL OR datetime(end_time) >= datetime(?))
        ORDER BY start_time DESC
        LIMIT 1
        """,
        (focus_start, focus_start),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def get_session_end_time(conn, session_id: int) -> str | None:
    """Get the end_time of a session."""
    cursor = conn.execute(
        "SELECT end_time FROM activity_sessions WHERE id = ?", (session_id,)
    )
    row = cursor.fetchone()
    return row["end_time"] if row else None


def calculate_clipped_duration(focus_start: str, focus_end: str, session_end: str) -> float:
    """Calculate duration clipped to session boundary."""
    start = datetime.fromisoformat(focus_start)
    end = datetime.fromisoformat(focus_end) if focus_end else datetime.now()

    if session_end:
        session_end_dt = datetime.fromisoformat(session_end)
        if end > session_end_dt:
            end = session_end_dt

    return max(0, (end - start).total_seconds())


def analyze_issues(conn):
    """Analyze and report issues in focus events."""
    # Count mismatched session_ids
    cursor = conn.execute(
        """
        SELECT COUNT(*) as count
        FROM window_focus_events fe
        WHERE fe.session_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM activity_sessions s
            WHERE s.id = fe.session_id
              AND datetime(fe.start_time) >= datetime(s.start_time)
              AND (s.end_time IS NULL OR datetime(fe.start_time) <= datetime(s.end_time))
          )
        """
    )
    mismatched_count = cursor.fetchone()[0]

    # Count events extending past session
    cursor = conn.execute(
        """
        SELECT COUNT(*) as count
        FROM window_focus_events fe
        JOIN activity_sessions s ON fe.session_id = s.id
        WHERE s.end_time IS NOT NULL
          AND datetime(fe.end_time) > datetime(s.end_time)
        """
    )
    extended_count = cursor.fetchone()[0]

    # Total
    cursor = conn.execute("SELECT COUNT(*) FROM window_focus_events")
    total = cursor.fetchone()[0]

    print(f"=== Focus Event Analysis ===")
    print(f"Total focus events: {total}")
    print(f"Events with wrong session_id: {mismatched_count} ({100*mismatched_count/total:.1f}%)")
    print(f"Events extending past session: {extended_count} ({100*extended_count/total:.1f}%)")
    print()

    return mismatched_count, extended_count


def get_events_to_fix(conn):
    """Get all focus events that need fixing."""
    # Events with wrong session_id OR extending past session end
    cursor = conn.execute(
        """
        SELECT
            fe.id,
            fe.session_id as current_session_id,
            fe.start_time,
            fe.end_time,
            fe.duration_seconds,
            fe.app_name
        FROM window_focus_events fe
        WHERE
            -- Wrong session_id
            (fe.session_id IS NOT NULL
             AND NOT EXISTS (
                SELECT 1 FROM activity_sessions s
                WHERE s.id = fe.session_id
                  AND datetime(fe.start_time) >= datetime(s.start_time)
                  AND (s.end_time IS NULL OR datetime(fe.start_time) <= datetime(s.end_time))
             ))
            OR
            -- Extends past session end
            (fe.session_id IS NOT NULL
             AND EXISTS (
                SELECT 1 FROM activity_sessions s
                WHERE s.id = fe.session_id
                  AND s.end_time IS NOT NULL
                  AND datetime(fe.end_time) > datetime(s.end_time)
             ))
        ORDER BY fe.id
        """
    )
    return cursor.fetchall()


def fix_events(conn, dry_run: bool = True):
    """Fix focus events with wrong session_id or inflated durations."""
    events = get_events_to_fix(conn)

    if not events:
        print("No events need fixing!")
        return

    print(f"Found {len(events)} events to fix")
    print()

    fixed_session_id = 0
    fixed_duration = 0
    set_null_session = 0

    for event in events:
        event_id = event["id"]
        current_session = event["current_session_id"]
        start_time = event["start_time"]
        end_time = event["end_time"]
        current_duration = event["duration_seconds"]
        app_name = event["app_name"]

        # Find correct session
        correct_session = find_correct_session(conn, start_time)

        # Get session end time for duration clipping
        session_end = None
        if correct_session:
            session_end = get_session_end_time(conn, correct_session)

        # Calculate clipped duration
        new_duration = current_duration
        if end_time and session_end:
            new_duration = calculate_clipped_duration(start_time, end_time, session_end)

        # Determine what changed
        session_changed = correct_session != current_session
        duration_changed = abs(new_duration - current_duration) > 1  # Allow 1s tolerance

        if session_changed or duration_changed:
            if dry_run:
                changes = []
                if session_changed:
                    changes.append(f"session: {current_session} -> {correct_session}")
                if duration_changed:
                    changes.append(f"duration: {current_duration:.0f}s -> {new_duration:.0f}s")
                print(f"  Event {event_id} ({app_name[:20]}): {', '.join(changes)}")
            else:
                conn.execute(
                    """
                    UPDATE window_focus_events
                    SET session_id = ?, duration_seconds = ?
                    WHERE id = ?
                    """,
                    (correct_session, new_duration, event_id),
                )

            if session_changed:
                if correct_session is None:
                    set_null_session += 1
                else:
                    fixed_session_id += 1
            if duration_changed:
                fixed_duration += 1

    print()
    print(f"=== Summary ===")
    print(f"Session ID corrections: {fixed_session_id}")
    print(f"Session ID set to NULL: {set_null_session}")
    print(f"Duration corrections: {fixed_duration}")

    if not dry_run:
        conn.commit()
        print()
        print("Changes committed to database.")
    else:
        print()
        print("Dry run complete. Use --apply to make changes.")


def main():
    parser = argparse.ArgumentParser(description="Fix historical focus event data")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be changed")
    group.add_argument("--apply", action="store_true", help="Apply the fixes")

    args = parser.parse_args()

    conn = get_db_connection()

    try:
        analyze_issues(conn)
        fix_events(conn, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
