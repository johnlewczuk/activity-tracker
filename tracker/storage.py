"""SQLite Database Storage Module for Activity Tracker.

This module provides a comprehensive database interface for storing and retrieving
screenshot metadata. It manages SQLite connections, handles database schema 
initialization, and provides efficient querying methods for the web interface.

The database schema stores:
- Screenshot metadata (timestamp, filepath, perceptual hash)
- Window context information (title, application name)
- Indexed lookups for time-based and hash-based queries

Key Features:
- Automatic database initialization with proper indexes
- Context manager for connection handling
- Time-range queries for web interface
- Efficient storage of screenshot metadata
- Thread-safe database operations

Database Schema:
    screenshots table:
        - id: Primary key (autoincrement)
        - timestamp: Unix timestamp (indexed)
        - filepath: Relative path to screenshot file
        - dhash: Perceptual hash for duplicate detection (indexed)
        - window_title: Active window title (optional)
        - app_name: Application class name (optional)

Example:
    >>> storage = ActivityStorage()
    >>> screenshot_id = storage.save_screenshot(
    ...     "/path/to/screenshot.webp", 
    ...     "a1b2c3d4e5f67890",
    ...     "Firefox - Activity Tracker",
    ...     "firefox"
    ... )
    >>> screenshots = storage.get_screenshots(start_time, end_time)
"""

import json
import sqlite3
import os
from contextlib import contextmanager
from typing import List, Dict, Optional
from pathlib import Path


class ActivityStorage:
    """SQLite database interface for Activity Tracker metadata storage.
    
    Manages screenshot metadata including timestamps, file paths, perceptual hashes,
    and window context information. Provides efficient querying capabilities for
    the web interface and handles database schema management.
    
    The class automatically initializes the database schema on first use and
    ensures proper indexing for time-based and hash-based queries.
    
    Attributes:
        db_path (str): Absolute path to the SQLite database file
        
    Example:
        >>> storage = ActivityStorage()
        >>> # Save a screenshot
        >>> id = storage.save_screenshot("/path/to/img.webp", "abc123", "Firefox")
        >>> 
        >>> # Query by time range
        >>> screenshots = storage.get_screenshots(start_ts, end_ts)
    """
    
    def __init__(self, db_path: str = None):
        """Initialize ActivityStorage with database connection.
        
        Sets up the database path and ensures the database schema exists.
        If no path is provided, uses the default location in the user's home
        directory at ~/activity-tracker-data/activity.db.
        
        Args:
            db_path (str, optional): Path to SQLite database file. If None,
                uses ~/activity-tracker-data/activity.db (default)
                
        Raises:
            RuntimeError: If directory creation fails due to permission issues
            sqlite3.Error: If database initialization fails
        """
        if db_path is None:
            data_dir = Path.home() / "activity-tracker-data"
            # TODO: Permission errors - handle case where data directory creation fails
            # Should check write permissions to home directory
            try:
                data_dir.mkdir(exist_ok=True)
            except PermissionError as e:
                raise RuntimeError(f"Permission denied creating data directory {data_dir}: {e}") from e
            db_path = data_dir / "activity.db"
        
        self.db_path = str(db_path)
        self.init_db()
    
    @contextmanager
    def get_connection(self):
        """Context manager for SQLite database connections.
        
        Provides a database connection with proper row factory and automatic
        cleanup. Uses Row factory for dictionary-like access to query results.
        
        Yields:
            sqlite3.Connection: Database connection with Row factory enabled
            
        Raises:
            RuntimeError: If database connection fails due to permission or
                file access issues
                
        Example:
            >>> storage = ActivityStorage()
            >>> with storage.get_connection() as conn:
            ...     cursor = conn.execute("SELECT * FROM screenshots LIMIT 1")
            ...     row = cursor.fetchone()
        """
        # TODO: Permission errors - handle case where database file access fails
        # Should check read/write permissions to database file location
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
        except (sqlite3.OperationalError, PermissionError) as e:
            raise RuntimeError(f"Database access error for {self.db_path}: {e}") from e
    
    def init_db(self):
        """Initialize the database schema with required tables and indexes.
        
        Creates the screenshots table if it doesn't exist and adds performance
        indexes for timestamp and dhash columns. This method is automatically
        called during ActivityStorage initialization.
        
        The schema includes:
        - screenshots table with metadata columns
        - Index on timestamp for time-range queries
        - Index on dhash for duplicate detection
        
        Raises:
            sqlite3.Error: If schema creation fails
            RuntimeError: If database access fails
        """
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    filepath TEXT NOT NULL,
                    dhash TEXT NOT NULL,
                    window_title TEXT,
                    app_name TEXT,
                    window_x INTEGER,
                    window_y INTEGER,
                    window_width INTEGER,
                    window_height INTEGER
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON screenshots(timestamp)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dhash ON screenshots(dhash)
            """)

            # Activity summaries table for hourly LLM-generated summaries
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    screenshot_ids TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    inference_time_ms INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, hour)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_summary_date ON activity_summaries(date)
            """)

            # Daily summaries table for consolidated daily rollups
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summaries (
                    date TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Activity sessions table - continuous periods of user activity
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP,
                    duration_seconds INTEGER,
                    summary TEXT,
                    screenshot_count INTEGER DEFAULT 0,
                    unique_windows INTEGER DEFAULT 0,
                    model_used TEXT,
                    inference_time_ms INTEGER,
                    prompt_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Add prompt_text column if it doesn't exist (for existing DBs)
            try:
                conn.execute("ALTER TABLE activity_sessions ADD COLUMN prompt_text TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Add screenshot_ids_used column if it doesn't exist
            try:
                conn.execute("ALTER TABLE activity_sessions ADD COLUMN screenshot_ids_used TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Add window geometry columns to screenshots table if they don't exist
            for col in ['window_x', 'window_y', 'window_width', 'window_height']:
                try:
                    conn.execute(f"ALTER TABLE screenshots ADD COLUMN {col} INTEGER")
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_start ON activity_sessions(start_time)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_end ON activity_sessions(end_time)
            """)

            # Session screenshots junction table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_screenshots (
                    session_id INTEGER REFERENCES activity_sessions(id),
                    screenshot_id INTEGER REFERENCES screenshots(id),
                    PRIMARY KEY (session_id, screenshot_id)
                )
            """)

            # Session OCR cache - store OCR per unique window_title
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_ocr_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES activity_sessions(id),
                    window_title TEXT NOT NULL,
                    ocr_text TEXT,
                    screenshot_id INTEGER,
                    UNIQUE(session_id, window_title)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ocr_session ON session_ocr_cache(session_id)
            """)

            conn.commit()
    
    def save_screenshot(self, filepath: str, dhash: str, window_title: str = None,
                       app_name: str = None, window_geometry: dict = None) -> int:
        """Save screenshot metadata to the database.

        Stores screenshot information including file path, perceptual hash, and
        optional window context. Uses file modification time as timestamp, falling
        back to current time if file access fails.

        Args:
            filepath (str): Absolute path to the screenshot file
            dhash (str): Perceptual hash (dhash) as hexadecimal string
            window_title (str, optional): Active window title when screenshot taken
            app_name (str, optional): Application class name when screenshot taken
            window_geometry (dict, optional): Window geometry with keys x, y, width, height

        Returns:
            int: Database ID of the inserted screenshot record

        Raises:
            sqlite3.Error: If database insertion fails
            RuntimeError: If database connection fails

        Example:
            >>> storage = ActivityStorage()
            >>> screenshot_id = storage.save_screenshot(
            ...     "/path/to/screenshot.webp",
            ...     "a1b2c3d4e5f67890",
            ...     "Firefox - Activity Tracker",
            ...     "firefox",
            ...     {"x": 100, "y": 50, "width": 1920, "height": 1080}
            ... )
        """
        # TODO: Edge case - handle case where file doesn't exist or permission denied when getting mtime
        try:
            timestamp = int(os.path.getmtime(filepath))
        except (OSError, PermissionError) as e:
            # Fallback to current timestamp if file access fails
            import time
            timestamp = int(time.time())

        # Extract window geometry if provided
        window_x = window_geometry.get('x') if window_geometry else None
        window_y = window_geometry.get('y') if window_geometry else None
        window_width = window_geometry.get('width') if window_geometry else None
        window_height = window_geometry.get('height') if window_geometry else None

        with self.get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO screenshots (timestamp, filepath, dhash, window_title, app_name,
                                        window_x, window_y, window_width, window_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, filepath, dhash, window_title, app_name,
                  window_x, window_y, window_width, window_height))

            conn.commit()
            return cursor.lastrowid
    
    def get_screenshots(self, start_time: int, end_time: int) -> List[Dict]:
        """Retrieve screenshots within a time range.
        
        Queries the database for all screenshots taken between start_time and
        end_time (inclusive), ordered by timestamp in descending order (newest first).
        
        Args:
            start_time (int): Unix timestamp for range start (inclusive)
            end_time (int): Unix timestamp for range end (inclusive)
            
        Returns:
            List[Dict]: List of screenshot dictionaries containing:
                - id (int): Database record ID
                - timestamp (int): Unix timestamp
                - filepath (str): Path to screenshot file
                - dhash (str): Perceptual hash
                - window_title (str|None): Window title
                - app_name (str|None): Application name
                
        Raises:
            sqlite3.Error: If database query fails
            RuntimeError: If database connection fails
            
        Example:
            >>> storage = ActivityStorage()
            >>> import time
            >>> start = int(time.time()) - 3600  # Last hour
            >>> end = int(time.time())
            >>> screenshots = storage.get_screenshots(start, end)
            >>> print(f"Found {len(screenshots)} screenshots")
        """
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, timestamp, filepath, dhash, window_title, app_name,
                       window_x, window_y, window_width, window_height
                FROM screenshots
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
            """, (start_time, end_time))

            return [dict(row) for row in cursor.fetchall()]
    
    def get_screenshot(self, screenshot_id: int) -> Optional[Dict]:
        """Retrieve a single screenshot by database ID.
        
        Fetches metadata for a specific screenshot record by its primary key.
        
        Args:
            screenshot_id (int): Database ID of the screenshot record
            
        Returns:
            Optional[Dict]: Screenshot dictionary with all fields, or None if not found.
                Dictionary contains same fields as get_screenshots() method.
                
        Raises:
            sqlite3.Error: If database query fails
            RuntimeError: If database connection fails
            
        Example:
            >>> storage = ActivityStorage() 
            >>> screenshot = storage.get_screenshot(123)
            >>> if screenshot:
            ...     print(f"Found: {screenshot['filepath']}")
            ... else:
            ...     print("Screenshot not found")
        """
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, timestamp, filepath, dhash, window_title, app_name,
                       window_x, window_y, window_width, window_height
                FROM screenshots
                WHERE id = ?
            """, (screenshot_id,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def save_summary(
        self,
        date: str,
        hour: int,
        summary: str,
        screenshot_ids: list[int],
        model: str,
        inference_ms: int,
    ) -> int:
        """Save an hourly activity summary to the database.

        Uses upsert (INSERT OR REPLACE) to handle conflicts on the unique
        (date, hour) constraint, allowing re-summarization of existing hours.

        Args:
            date: Date string in YYYY-MM-DD format.
            hour: Hour of day (0-23).
            summary: LLM-generated activity summary text.
            screenshot_ids: List of screenshot database IDs used for summarization.
            model: Name of the LLM model used (e.g., "gemma3:27b-it-qat").
            inference_ms: Time taken for LLM inference in milliseconds.

        Returns:
            Database ID of the inserted/updated summary record.

        Raises:
            sqlite3.Error: If database insertion fails.
            RuntimeError: If database connection fails.
        """
        screenshot_ids_json = json.dumps(screenshot_ids)

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO activity_summaries
                    (date, hour, summary, screenshot_ids, model_used, inference_time_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (date, hour, summary, screenshot_ids_json, model, inference_ms),
            )
            conn.commit()
            return cursor.lastrowid

    def get_summaries_for_date(self, date: str) -> List[Dict]:
        """Retrieve all hourly summaries for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            List of summary dictionaries ordered by hour, each containing:
                - id (int): Database record ID
                - date (str): Date string
                - hour (int): Hour of day (0-23)
                - summary (str): Activity summary text
                - screenshot_ids (list[int]): IDs of screenshots used
                - model_used (str): LLM model name
                - inference_time_ms (int): Inference duration
                - created_at (str): Timestamp when summary was created

        Raises:
            sqlite3.Error: If database query fails.
            RuntimeError: If database connection fails.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, date, hour, summary, screenshot_ids, model_used,
                       inference_time_ms, created_at
                FROM activity_summaries
                WHERE date = ?
                ORDER BY hour
                """,
                (date,),
            )

            results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                row_dict["screenshot_ids"] = json.loads(row_dict["screenshot_ids"])
                results.append(row_dict)
            return results

    def get_summary(self, date: str, hour: int) -> Optional[Dict]:
        """Retrieve a specific hour's activity summary.

        Args:
            date: Date string in YYYY-MM-DD format.
            hour: Hour of day (0-23).

        Returns:
            Summary dictionary with all fields, or None if not found.
            Dictionary contains same fields as get_summaries_for_date().

        Raises:
            sqlite3.Error: If database query fails.
            RuntimeError: If database connection fails.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, date, hour, summary, screenshot_ids, model_used,
                       inference_time_ms, created_at
                FROM activity_summaries
                WHERE date = ? AND hour = ?
                """,
                (date, hour),
            )

            row = cursor.fetchone()
            if row:
                row_dict = dict(row)
                row_dict["screenshot_ids"] = json.loads(row_dict["screenshot_ids"])
                return row_dict
            return None

    def get_unsummarized_hours(self, date: str) -> List[int]:
        """Get hours that have screenshots but no summary for a date.

        Identifies hours with captured screenshots that haven't yet been
        processed for activity summarization.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            List of hours (0-23) that have screenshots but no summary,
            sorted in ascending order.

        Raises:
            sqlite3.Error: If database query fails.
            RuntimeError: If database connection fails.
        """
        # Convert date to timestamp range
        from datetime import datetime

        date_obj = datetime.strptime(date, "%Y-%m-%d")
        start_ts = int(date_obj.timestamp())
        end_ts = start_ts + 86400  # 24 hours

        with self.get_connection() as conn:
            # Get all hours that have screenshots
            cursor = conn.execute(
                """
                SELECT DISTINCT CAST((timestamp - ?) / 3600 AS INTEGER) as hour
                FROM screenshots
                WHERE timestamp >= ? AND timestamp < ?
                """,
                (start_ts, start_ts, end_ts),
            )
            hours_with_screenshots = {row["hour"] for row in cursor.fetchall()}

            # Get hours that already have summaries
            cursor = conn.execute(
                """
                SELECT hour FROM activity_summaries WHERE date = ?
                """,
                (date,),
            )
            hours_with_summaries = {row["hour"] for row in cursor.fetchall()}

            # Return hours that have screenshots but no summary
            unsummarized = hours_with_screenshots - hours_with_summaries
            return sorted(list(unsummarized))

    def get_summary_coverage(self) -> Dict:
        """Get statistics about summary coverage across all data.

        Calculates how many hours have been summarized versus how many
        have screenshots, along with the date range of available data.

        Returns:
            Dictionary containing:
                - total_hours_with_screenshots (int): Hours with captured data
                - total_hours_summarized (int): Hours with summaries
                - date_range (dict): Contains 'start' and 'end' date strings,
                  or None if no data exists

        Raises:
            sqlite3.Error: If database query fails.
            RuntimeError: If database connection fails.
        """
        from datetime import datetime

        with self.get_connection() as conn:
            # Get total hours with screenshots
            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT date(timestamp, 'unixepoch') || '-' ||
                       CAST(strftime('%H', timestamp, 'unixepoch') AS INTEGER)) as count
                FROM screenshots
                """
            )
            total_hours_with_screenshots = cursor.fetchone()["count"]

            # Get total hours summarized
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count FROM activity_summaries
                """
            )
            total_hours_summarized = cursor.fetchone()["count"]

            # Get date range
            cursor = conn.execute(
                """
                SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts
                FROM screenshots
                """
            )
            row = cursor.fetchone()

            date_range = None
            if row["min_ts"] is not None:
                date_range = {
                    "start": datetime.fromtimestamp(row["min_ts"]).strftime("%Y-%m-%d"),
                    "end": datetime.fromtimestamp(row["max_ts"]).strftime("%Y-%m-%d"),
                }

            return {
                "total_hours_with_screenshots": total_hours_with_screenshots,
                "total_hours_summarized": total_hours_summarized,
                "date_range": date_range,
            }

    def save_daily_summary(self, date: str, summary: str) -> None:
        """Save a daily rollup summary to the database.

        Uses upsert (INSERT OR REPLACE) to handle updates to existing summaries.

        Args:
            date: Date string in YYYY-MM-DD format.
            summary: LLM-generated daily summary text.

        Raises:
            sqlite3.Error: If database insertion fails.
            RuntimeError: If database connection fails.
        """
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_summaries (date, summary)
                VALUES (?, ?)
                """,
                (date, summary),
            )
            conn.commit()

    def get_daily_summary(self, date: str) -> Optional[Dict]:
        """Retrieve the daily summary for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            Dictionary with 'date', 'summary', and 'created_at' fields,
            or None if no daily summary exists for that date.

        Raises:
            sqlite3.Error: If database query fails.
            RuntimeError: If database connection fails.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT date, summary, created_at
                FROM daily_summaries
                WHERE date = ?
                """,
                (date,),
            )

            row = cursor.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # Session Management Methods
    # =========================================================================

    def create_session(self, start_time) -> int:
        """Create a new activity session.

        Args:
            start_time: datetime object for session start.

        Returns:
            Database ID of the newly created session.

        Raises:
            sqlite3.Error: If database insertion fails.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO activity_sessions (start_time)
                VALUES (?)
                """,
                (start_time.isoformat(),),
            )
            conn.commit()
            return cursor.lastrowid

    def end_session(self, session_id: int, end_time, duration_seconds: int) -> None:
        """End a session by setting end_time and duration.

        Args:
            session_id: ID of the session to end.
            end_time: datetime object for session end.
            duration_seconds: Total session duration in seconds.

        Raises:
            sqlite3.Error: If database update fails.
        """
        with self.get_connection() as conn:
            # Count screenshots and unique windows for this session
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count FROM session_screenshots
                WHERE session_id = ?
                """,
                (session_id,),
            )
            screenshot_count = cursor.fetchone()["count"]

            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT s.window_title) as count
                FROM session_screenshots ss
                JOIN screenshots s ON ss.screenshot_id = s.id
                WHERE ss.session_id = ? AND s.window_title IS NOT NULL
                """,
                (session_id,),
            )
            unique_windows = cursor.fetchone()["count"]

            conn.execute(
                """
                UPDATE activity_sessions
                SET end_time = ?, duration_seconds = ?,
                    screenshot_count = ?, unique_windows = ?
                WHERE id = ?
                """,
                (end_time.isoformat(), duration_seconds, screenshot_count, unique_windows, session_id),
            )
            conn.commit()

    def get_active_session(self) -> Optional[Dict]:
        """Get the currently active session (end_time is NULL).

        Returns:
            Session dictionary or None if no active session.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, duration_seconds, summary,
                       screenshot_count, unique_windows, model_used, inference_time_ms,
                       prompt_text, screenshot_ids_used
                FROM activity_sessions
                WHERE end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)
            session_id = result["id"]

            # Parse screenshot_ids_used JSON if present
            if result.get("screenshot_ids_used"):
                result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])

            # Calculate live counts for active session
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count FROM session_screenshots
                WHERE session_id = ?
                """,
                (session_id,),
            )
            result["screenshot_count"] = cursor.fetchone()["count"]

            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT s.window_title) as count
                FROM session_screenshots ss
                JOIN screenshots s ON ss.screenshot_id = s.id
                WHERE ss.session_id = ? AND s.window_title IS NOT NULL
                """,
                (session_id,),
            )
            result["unique_windows"] = cursor.fetchone()["count"]

            return result

    def get_session(self, session_id: int) -> Optional[Dict]:
        """Get a session by ID.

        Args:
            session_id: The session ID to retrieve.

        Returns:
            Session dictionary with all metadata, or None if not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, duration_seconds, summary,
                       screenshot_count, unique_windows, model_used, inference_time_ms,
                       prompt_text, screenshot_ids_used
                FROM activity_sessions
                WHERE id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)

            # Parse screenshot_ids_used JSON if present
            if result.get("screenshot_ids_used"):
                result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])

            # For active sessions (no end_time), calculate live counts
            if result["end_time"] is None:
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) as count FROM session_screenshots
                    WHERE session_id = ?
                    """,
                    (session_id,),
                )
                result["screenshot_count"] = cursor.fetchone()["count"]

                cursor = conn.execute(
                    """
                    SELECT COUNT(DISTINCT s.window_title) as count
                    FROM session_screenshots ss
                    JOIN screenshots s ON ss.screenshot_id = s.id
                    WHERE ss.session_id = ? AND s.window_title IS NOT NULL
                    """,
                    (session_id,),
                )
                result["unique_windows"] = cursor.fetchone()["count"]

            return result

    def get_sessions_for_date(self, date: str) -> List[Dict]:
        """Get all sessions for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            List of session dicts ordered by start_time.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, duration_seconds, summary,
                       screenshot_count, unique_windows, model_used, inference_time_ms,
                       prompt_text, screenshot_ids_used
                FROM activity_sessions
                WHERE date(start_time) = ?
                ORDER BY start_time
                """,
                (date,),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)

                # Parse screenshot_ids_used JSON if present
                if result.get("screenshot_ids_used"):
                    result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])

                # For active sessions (no end_time), calculate live counts
                if result["end_time"] is None:
                    session_id = result["id"]
                    cursor2 = conn.execute(
                        """
                        SELECT COUNT(*) as count FROM session_screenshots
                        WHERE session_id = ?
                        """,
                        (session_id,),
                    )
                    result["screenshot_count"] = cursor2.fetchone()["count"]

                    cursor2 = conn.execute(
                        """
                        SELECT COUNT(DISTINCT s.window_title) as count
                        FROM session_screenshots ss
                        JOIN screenshots s ON ss.screenshot_id = s.id
                        WHERE ss.session_id = ? AND s.window_title IS NOT NULL
                        """,
                        (session_id,),
                    )
                    result["unique_windows"] = cursor2.fetchone()["count"]

                results.append(result)
            return results

    def get_unsummarized_sessions(self) -> List[Dict]:
        """Get sessions that have ended but have no summary.

        Returns:
            List of session dicts that need summarization.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, duration_seconds, summary,
                       screenshot_count, unique_windows, model_used, inference_time_ms
                FROM activity_sessions
                WHERE end_time IS NOT NULL AND summary IS NULL
                ORDER BY start_time
                """
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_session(self, session_id: int) -> None:
        """Delete a session and its associated data.

        Used for sessions shorter than minimum duration.

        Args:
            session_id: ID of the session to delete.
        """
        with self.get_connection() as conn:
            # Delete OCR cache entries
            conn.execute(
                "DELETE FROM session_ocr_cache WHERE session_id = ?",
                (session_id,),
            )
            # Delete screenshot links
            conn.execute(
                "DELETE FROM session_screenshots WHERE session_id = ?",
                (session_id,),
            )
            # Delete the session itself
            conn.execute(
                "DELETE FROM activity_sessions WHERE id = ?",
                (session_id,),
            )
            conn.commit()

    # =========================================================================
    # Screenshot Linking Methods
    # =========================================================================

    def link_screenshot_to_session(self, session_id: int, screenshot_id: int) -> None:
        """Link a screenshot to a session.

        Args:
            session_id: The session to link to.
            screenshot_id: The screenshot to link.
        """
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO session_screenshots (session_id, screenshot_id)
                VALUES (?, ?)
                """,
                (session_id, screenshot_id),
            )
            conn.commit()

    def get_session_screenshots(self, session_id: int) -> List[Dict]:
        """Get all screenshots for a session.

        Args:
            session_id: The session ID.

        Returns:
            List of screenshot dicts ordered by timestamp.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT s.id, s.timestamp, s.filepath, s.dhash, s.window_title, s.app_name,
                       s.window_x, s.window_y, s.window_width, s.window_height
                FROM screenshots s
                JOIN session_screenshots ss ON s.id = ss.screenshot_id
                WHERE ss.session_id = ?
                ORDER BY s.timestamp
                """,
                (session_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_unique_window_titles_for_session(self, session_id: int) -> List[str]:
        """Get unique window titles for a session.

        Args:
            session_id: The session ID.

        Returns:
            List of unique window title strings.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT s.window_title
                FROM screenshots s
                JOIN session_screenshots ss ON s.id = ss.screenshot_id
                WHERE ss.session_id = ? AND s.window_title IS NOT NULL
                ORDER BY s.window_title
                """,
                (session_id,),
            )
            return [row["window_title"] for row in cursor.fetchall()]

    # =========================================================================
    # OCR Caching Methods
    # =========================================================================

    def get_cached_ocr(self, session_id: int, window_title: str) -> Optional[str]:
        """Get cached OCR text for a window title in a session.

        Args:
            session_id: The session ID.
            window_title: The window title to look up.

        Returns:
            OCR text if cached, None otherwise.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ocr_text FROM session_ocr_cache
                WHERE session_id = ? AND window_title = ?
                """,
                (session_id, window_title),
            )
            row = cursor.fetchone()
            return row["ocr_text"] if row else None

    def cache_ocr(
        self, session_id: int, window_title: str, ocr_text: str, screenshot_id: int
    ) -> None:
        """Cache OCR text for a window title in a session.

        Args:
            session_id: The session ID.
            window_title: The window title.
            ocr_text: The extracted OCR text.
            screenshot_id: ID of the screenshot that was OCR'd.
        """
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_ocr_cache
                    (session_id, window_title, ocr_text, screenshot_id)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, window_title, ocr_text, screenshot_id),
            )
            conn.commit()

    def get_all_session_ocr(self, session_id: int) -> List[Dict]:
        """Get all cached OCR text for a session.

        Args:
            session_id: The session ID.

        Returns:
            List of dicts with window_title and ocr_text.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT window_title, ocr_text FROM session_ocr_cache
                WHERE session_id = ?
                ORDER BY window_title
                """,
                (session_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # =========================================================================
    # Session Summary Methods
    # =========================================================================

    def save_session_summary(
        self, session_id: int, summary: str, model: str, inference_ms: int,
        prompt_text: str = None, screenshot_ids_used: list = None
    ) -> None:
        """Save a summary for a session.

        Args:
            session_id: The session ID.
            summary: The LLM-generated summary text.
            model: Name of the model used.
            inference_ms: Inference time in milliseconds.
            prompt_text: The full prompt text sent to the LLM (for debugging).
            screenshot_ids_used: List of screenshot IDs actually used in summarization.
        """
        screenshot_ids_json = json.dumps(screenshot_ids_used) if screenshot_ids_used else None

        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE activity_sessions
                SET summary = ?, model_used = ?, inference_time_ms = ?, prompt_text = ?,
                    screenshot_ids_used = ?
                WHERE id = ?
                """,
                (summary, model, inference_ms, prompt_text, screenshot_ids_json, session_id),
            )
            conn.commit()

    def get_recent_summaries(self, n: int = 3) -> List[str]:
        """Get the last N session summaries for context continuity.

        Args:
            n: Number of recent summaries to retrieve.

        Returns:
            List of summary strings, most recent first.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT summary FROM activity_sessions
                WHERE summary IS NOT NULL
                ORDER BY end_time DESC
                LIMIT ?
                """,
                (n,),
            )
            return [row["summary"] for row in cursor.fetchall()]

    def get_last_screenshot_timestamp_for_session(self, session_id: int) -> Optional[int]:
        """Get the timestamp of the last screenshot in a session.

        Args:
            session_id: The session ID.

        Returns:
            Unix timestamp of the last screenshot, or None if no screenshots.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT MAX(s.timestamp) as last_ts
                FROM screenshots s
                JOIN session_screenshots ss ON s.id = ss.screenshot_id
                WHERE ss.session_id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return row["last_ts"] if row and row["last_ts"] else None