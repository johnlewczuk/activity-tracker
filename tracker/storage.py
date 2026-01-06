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
import logging
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


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
                    window_height INTEGER,
                    monitor_name TEXT,
                    monitor_width INTEGER,
                    monitor_height INTEGER
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

            # Add monitor metadata columns to screenshots table if they don't exist
            try:
                conn.execute("ALTER TABLE screenshots ADD COLUMN monitor_name TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            for col in ['monitor_width', 'monitor_height']:
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

            # Threshold-based summaries - trigger every N screenshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS threshold_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    summary TEXT NOT NULL,
                    screenshot_ids TEXT NOT NULL,
                    screenshot_count INTEGER NOT NULL,
                    model_used TEXT NOT NULL,
                    config_snapshot TEXT,
                    inference_time_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    regenerated_from INTEGER REFERENCES threshold_summaries(id)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_threshold_summary_time
                ON threshold_summaries(start_time, end_time)
            """)

            # Add project column to threshold_summaries if not exists (migration)
            cursor = conn.execute("PRAGMA table_info(threshold_summaries)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'project' not in columns:
                conn.execute("ALTER TABLE threshold_summaries ADD COLUMN project TEXT")
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_threshold_summary_project
                    ON threshold_summaries(project)
                """)
                logger.info("Added 'project' column to threshold_summaries table")

            # Add prompt_text column to threshold_summaries if not exists (migration)
            if 'prompt_text' not in columns:
                conn.execute("ALTER TABLE threshold_summaries ADD COLUMN prompt_text TEXT")
                logger.info("Added 'prompt_text' column to threshold_summaries table")

            # Add explanation and confidence columns for structured summaries (migration)
            if 'explanation' not in columns:
                conn.execute("ALTER TABLE threshold_summaries ADD COLUMN explanation TEXT")
                logger.info("Added 'explanation' column to threshold_summaries table")
            if 'confidence' not in columns:
                conn.execute("ALTER TABLE threshold_summaries ADD COLUMN confidence REAL")
                logger.info("Added 'confidence' column to threshold_summaries table")
            if 'tags' not in columns:
                conn.execute("ALTER TABLE threshold_summaries ADD COLUMN tags TEXT")
                logger.info("Added 'tags' column to threshold_summaries table")

            # Junction table for threshold summaries <-> screenshots (proper M:N relationship)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS threshold_summary_screenshots (
                    summary_id INTEGER NOT NULL REFERENCES threshold_summaries(id) ON DELETE CASCADE,
                    screenshot_id INTEGER NOT NULL REFERENCES screenshots(id) ON DELETE CASCADE,
                    PRIMARY KEY (summary_id, screenshot_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tss_screenshot
                ON threshold_summary_screenshots(screenshot_id)
            """)

            # Window focus tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS window_focus_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    window_title TEXT NOT NULL,
                    app_name TEXT NOT NULL,
                    window_class TEXT,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    duration_seconds REAL NOT NULL,
                    session_id INTEGER REFERENCES activity_sessions(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_focus_start
                ON window_focus_events(start_time)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_focus_app
                ON window_focus_events(app_name)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_focus_session
                ON window_focus_events(session_id)
            """)

            # Add capture_reason and focus_duration columns to screenshots if not exists
            try:
                conn.execute("ALTER TABLE screenshots ADD COLUMN capture_reason TEXT")
            except Exception:
                pass  # Column already exists

            try:
                conn.execute("ALTER TABLE screenshots ADD COLUMN focus_duration_at_capture REAL")
            except Exception:
                pass  # Column already exists

            # Add terminal_context column for terminal introspection
            try:
                conn.execute("ALTER TABLE window_focus_events ADD COLUMN terminal_context TEXT")
            except Exception:
                pass  # Column already exists

            # Exported reports history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exported_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    time_range TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    format TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    file_size INTEGER,
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exported_reports_created
                ON exported_reports(created_at DESC)
            """)

            # Cached daily/weekly/monthly reports for fast synthesis
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cached_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_type TEXT NOT NULL,  -- 'daily', 'weekly', 'monthly'
                    period_date TEXT NOT NULL,  -- '2025-12-30' for daily, '2025-W52' for weekly, '2025-12' for monthly
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    executive_summary TEXT,
                    sections_json TEXT,  -- JSON array of sections
                    analytics_json TEXT,  -- JSON of analytics data
                    summary_ids_json TEXT,  -- JSON array of source summary IDs
                    model_used TEXT,
                    inference_time_ms INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    prompt_text TEXT,  -- Full prompt sent to LLM
                    explanation TEXT,  -- LLM-provided explanation
                    tags TEXT,  -- JSON array of activity tags
                    confidence REAL,  -- Confidence score (0.0-1.0)
                    child_summary_ids TEXT,  -- JSON array of child summary IDs
                    regenerated_at TIMESTAMP,  -- Last regeneration timestamp
                    UNIQUE(period_type, period_date)
                )
            """)

            # Add new columns to existing cached_reports table (safe migrations)
            for col_def in [
                ('prompt_text', 'TEXT'),
                ('explanation', 'TEXT'),
                ('tags', 'TEXT'),
                ('confidence', 'REAL'),
                ('child_summary_ids', 'TEXT'),
                ('regenerated_at', 'TIMESTAMP'),
            ]:
                try:
                    conn.execute(f"ALTER TABLE cached_reports ADD COLUMN {col_def[0]} {col_def[1]}")
                except sqlite3.OperationalError:
                    pass  # Column already exists

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cached_reports_period
                ON cached_reports(period_type, period_date)
            """)

            conn.commit()
    
    def save_screenshot(self, filepath: str, dhash: str, window_title: str = None,
                       app_name: str = None, window_geometry: dict = None,
                       monitor_name: str = None, monitor_width: int = None,
                       monitor_height: int = None) -> int:
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
            monitor_name (str, optional): Monitor identifier (e.g., "DP-1", "HDMI-0")
            monitor_width (int, optional): Monitor width in pixels
            monitor_height (int, optional): Monitor height in pixels

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
            ...     {"x": 100, "y": 50, "width": 1920, "height": 1080},
            ...     "DP-1", 3840, 2160
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
                                        window_x, window_y, window_width, window_height,
                                        monitor_name, monitor_width, monitor_height)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, filepath, dhash, window_title, app_name,
                  window_x, window_y, window_width, window_height,
                  monitor_name, monitor_width, monitor_height))

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
                       window_x, window_y, window_width, window_height,
                       monitor_name, monitor_width, monitor_height
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
                       window_x, window_y, window_width, window_height,
                       monitor_name, monitor_width, monitor_height
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
            # Clear session_id from focus events (keep the events, just remove the link)
            conn.execute(
                "UPDATE window_focus_events SET session_id = NULL WHERE session_id = ?",
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
                       s.window_x, s.window_y, s.window_width, s.window_height,
                       s.monitor_name, s.monitor_width, s.monitor_height
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

    # ==================== Threshold-Based Summary Methods ====================

    def get_unsummarized_screenshots(
        self, require_session: bool = True, date: str = None
    ) -> List[Dict]:
        """Get screenshots not covered by any threshold summary.

        Args:
            require_session: If True (default), only returns screenshots linked
                to an active session. Set to False to include ALL unsummarized
                screenshots (useful for "Generate Missing" backfill).
            date: Optional date string (YYYY-MM-DD) to filter screenshots to
                a specific day. If None, returns all unsummarized screenshots.

        Returns:
            List of screenshot dicts ordered by timestamp DESC (recent first),
            each containing id, timestamp, filepath, window_title, app_name.

        Note:
            Screenshots are returned in reverse chronological order so that
            the most recent activity is summarized first, providing immediate
            value to users viewing today's timeline.
        """
        # Build date filter if provided
        date_filter = ""
        params = []
        if date:
            from datetime import datetime
            try:
                date_obj = datetime.strptime(date, '%Y-%m-%d')
                start_ts = int(date_obj.timestamp())
                end_ts = start_ts + 86400  # +1 day
                date_filter = "AND s.timestamp >= ? AND s.timestamp < ?"
                params = [start_ts, end_ts]
            except ValueError:
                pass  # Invalid date, ignore filter

        with self.get_connection() as conn:
            if require_session:
                # Only screenshots linked to sessions (excludes AFK periods)
                cursor = conn.execute(f"""
                    SELECT s.id, s.timestamp, s.filepath, s.window_title, s.app_name,
                           s.window_x, s.window_y, s.window_width, s.window_height
                    FROM screenshots s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM threshold_summary_screenshots tss
                        WHERE tss.screenshot_id = s.id
                    )
                    AND EXISTS (
                        SELECT 1 FROM session_screenshots ss
                        WHERE ss.screenshot_id = s.id
                    )
                    {date_filter}
                    ORDER BY s.timestamp DESC
                """, params)
            else:
                # All unsummarized screenshots (for backfill)
                cursor = conn.execute(f"""
                    SELECT s.id, s.timestamp, s.filepath, s.window_title, s.app_name,
                           s.window_x, s.window_y, s.window_width, s.window_height
                    FROM screenshots s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM threshold_summary_screenshots tss
                        WHERE tss.screenshot_id = s.id
                    )
                    {date_filter}
                    ORDER BY s.timestamp DESC
                """, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_last_threshold_summary(self) -> Optional[Dict]:
        """Get the most recent threshold summary for context continuity.

        Returns:
            Summary dict or None if no summaries exist.
        """
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, regenerated_from, project
                FROM threshold_summaries
                ORDER BY end_time DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                return result
            return None

    def save_threshold_summary(
        self,
        start_time: str,
        end_time: str,
        summary: str,
        screenshot_ids: List[int],
        model: str,
        config_snapshot: dict,
        inference_ms: int,
        regenerated_from: int = None,
        project: str = None,
        prompt_text: str = None,
        explanation: str = None,
        tags: List[str] = None,
        confidence: float = None,
        is_preview: bool = False
    ) -> int:
        """Save a new threshold-based summary.

        Args:
            start_time: ISO format timestamp of first screenshot
            end_time: ISO format timestamp of last screenshot
            summary: The generated summary text
            screenshot_ids: List of screenshot IDs included
            model: Model used for generation
            config_snapshot: Dict of config settings used
            inference_ms: Time taken for inference
            regenerated_from: ID of original summary if this is a regeneration
            project: Detected project context name
            prompt_text: The full prompt text sent to the LLM (for debugging)
            explanation: Model's explanation of what it observed
            tags: List of tags from the LLM
            confidence: Model's confidence score (0.0-1.0)
            is_preview: True if this is a preview summary for active session

        Returns:
            ID of the new summary record.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO threshold_summaries
                    (start_time, end_time, summary, screenshot_ids, screenshot_count,
                     model_used, config_snapshot, inference_time_ms, regenerated_from, project, prompt_text,
                     explanation, tags, confidence, is_preview)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    start_time,
                    end_time,
                    summary,
                    json.dumps(screenshot_ids),
                    len(screenshot_ids),
                    model,
                    json.dumps(config_snapshot) if config_snapshot else None,
                    inference_ms,
                    regenerated_from,
                    project,
                    prompt_text,
                    explanation,
                    json.dumps(tags) if tags else None,
                    confidence,
                    1 if is_preview else 0,
                ),
            )
            summary_id = cursor.lastrowid

            # Insert into junction table to track which screenshots are summarized
            conn.executemany(
                "INSERT OR IGNORE INTO threshold_summary_screenshots (summary_id, screenshot_id) VALUES (?, ?)",
                [(summary_id, sid) for sid in screenshot_ids]
            )
            conn.commit()
            return summary_id

    def update_threshold_summary(
        self,
        summary_id: int,
        summary: str,
        model: str,
        config_snapshot: dict,
        inference_ms: int,
        prompt_text: str = None,
        explanation: str = None,
        tags: List[str] = None,
        confidence: float = None
    ) -> bool:
        """Update an existing summary in-place (for regeneration).

        Args:
            summary_id: ID of the summary to update
            summary: The new generated summary text
            model: Model used for generation
            config_snapshot: Dict of config settings used
            inference_ms: Time taken for inference
            prompt_text: The full prompt text sent to the LLM
            explanation: Model's explanation of what it observed
            tags: List of tags from the LLM
            confidence: Model's confidence score (0.0-1.0)

        Returns:
            True if update succeeded, False if summary not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE threshold_summaries
                SET summary = ?,
                    model_used = ?,
                    config_snapshot = ?,
                    inference_time_ms = ?,
                    prompt_text = ?,
                    explanation = ?,
                    tags = ?,
                    confidence = ?,
                    regenerated_from = NULL
                WHERE id = ?
                """,
                (
                    summary,
                    model,
                    json.dumps(config_snapshot) if config_snapshot else None,
                    inference_ms,
                    prompt_text,
                    explanation,
                    json.dumps(tags) if tags else None,
                    confidence,
                    summary_id,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def has_summary_for_time_range(self, start_time: str, end_time: str) -> bool:
        """Check if a summary already exists for the given time range.

        Used to prevent duplicate summaries for the same time slot.

        Args:
            start_time: ISO format start timestamp
            end_time: ISO format end timestamp

        Returns:
            True if a summary already exists, False otherwise.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM threshold_summaries
                WHERE start_time = ? AND end_time = ?
                """,
                (start_time, end_time),
            )
            count = cursor.fetchone()[0]
            return count > 0

    def cleanup_duplicate_summaries(self) -> int:
        """Remove duplicate summaries, keeping only the latest per time range.

        For each (start_time, end_time) pair with multiple summaries,
        keeps the one with the highest ID and deletes the rest.
        Also clears regenerated_from on all remaining summaries.

        Returns:
            Number of duplicate summaries deleted.
        """
        with self.get_connection() as conn:
            # Find and delete duplicates (keep highest ID per time range)
            cursor = conn.execute(
                """
                DELETE FROM threshold_summaries
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM threshold_summaries
                    GROUP BY start_time, end_time
                )
                """
            )
            deleted_count = cursor.rowcount

            # Clear regenerated_from on all remaining summaries
            conn.execute(
                "UPDATE threshold_summaries SET regenerated_from = NULL"
            )
            conn.commit()
            return deleted_count

    def get_threshold_summary(self, summary_id: int) -> Optional[Dict]:
        """Get a threshold summary by ID.

        Args:
            summary_id: The summary ID to retrieve.

        Returns:
            Summary dict or None if not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, regenerated_from, project, prompt_text,
                       explanation, tags, confidence
                FROM threshold_summaries
                WHERE id = ?
                """,
                (summary_id,),
            )
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                if result.get('tags'):
                    result['tags'] = json.loads(result['tags'])
                else:
                    result['tags'] = []
                # Normalize created_at to ISO format with T separator (UTC to local)
                if result.get('created_at'):
                    try:
                        from datetime import datetime
                        # Parse UTC timestamp from SQLite
                        utc_dt = datetime.strptime(result['created_at'], '%Y-%m-%d %H:%M:%S')
                        # Convert to local time and ISO format
                        result['created_at'] = utc_dt.strftime('%Y-%m-%dT%H:%M:%S')
                    except (ValueError, TypeError):
                        pass  # Keep original if parsing fails
                return result
            return None

    def get_threshold_summaries_for_date(self, date: str) -> List[Dict]:
        """Get all threshold summaries for a specific date.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            List of summary dicts ordered by start_time.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, regenerated_from, project,
                       explanation, tags, confidence, is_preview
                FROM threshold_summaries
                WHERE date(start_time) = ?
                ORDER BY start_time ASC
                """,
                (date,),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                if result.get('tags'):
                    result['tags'] = json.loads(result['tags'])
                else:
                    result['tags'] = []
                result['is_preview'] = bool(result.get('is_preview', 0))
                results.append(result)
            return results

    def get_summary_versions(self, original_id: int) -> List[Dict]:
        """Get all versions of a summary (original + regenerations).

        Args:
            original_id: The root summary ID.

        Returns:
            List of all versions including original, ordered by created_at.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, regenerated_from, project,
                       explanation, tags, confidence
                FROM threshold_summaries
                WHERE id = ? OR regenerated_from = ?
                ORDER BY created_at ASC
                """,
                (original_id, original_id),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                if result.get('tags'):
                    result['tags'] = json.loads(result['tags'])
                else:
                    result['tags'] = []
                results.append(result)
            return results

    def delete_threshold_summary(self, summary_id: int) -> bool:
        """Delete a threshold summary and its screenshot links.

        Args:
            summary_id: The summary ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        with self.get_connection() as conn:
            # Delete screenshot links first (makes screenshots "unsummarized" again)
            conn.execute(
                "DELETE FROM threshold_summary_screenshots WHERE summary_id = ?",
                (summary_id,),
            )
            # Then delete the summary itself
            cursor = conn.execute(
                "DELETE FROM threshold_summaries WHERE id = ?",
                (summary_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_current_preview_summary(self) -> Optional[Dict]:
        """Get the current preview summary if one exists.

        Returns:
            Preview summary dict or None if no preview exists.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, project,
                       explanation, tags, confidence, is_preview
                FROM threshold_summaries
                WHERE is_preview = 1
                ORDER BY created_at DESC
                LIMIT 1
                """,
            )
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                if result.get('tags'):
                    result['tags'] = json.loads(result['tags'])
                else:
                    result['tags'] = []
                result['is_preview'] = True
                return result
            return None

    def delete_preview_summaries(self) -> int:
        """Delete all preview summaries.

        Called when a session ends to clean up the preview before
        generating the final summary.

        Returns:
            Number of preview summaries deleted.
        """
        with self.get_connection() as conn:
            # Get preview IDs first for junction table cleanup
            cursor = conn.execute(
                "SELECT id FROM threshold_summaries WHERE is_preview = 1"
            )
            preview_ids = [row['id'] for row in cursor.fetchall()]

            if preview_ids:
                # Delete screenshot links
                placeholders = ','.join('?' * len(preview_ids))
                conn.execute(
                    f"DELETE FROM threshold_summary_screenshots WHERE summary_id IN ({placeholders})",
                    preview_ids,
                )
                # Delete previews
                cursor = conn.execute(
                    f"DELETE FROM threshold_summaries WHERE id IN ({placeholders})",
                    preview_ids,
                )
                conn.commit()
                return cursor.rowcount
            return 0

    def update_preview_summary(
        self,
        summary_id: int,
        end_time: str,
        summary: str,
        screenshot_ids: List[int],
        model: str,
        config_snapshot: dict,
        inference_ms: int,
        explanation: str = None,
        tags: List[str] = None,
        confidence: float = None
    ) -> bool:
        """Update an existing preview summary with new data.

        Args:
            summary_id: ID of the preview to update
            end_time: New end time (start time stays the same)
            summary: The new generated summary text
            screenshot_ids: Updated list of screenshot IDs
            model: Model used for generation
            config_snapshot: Dict of config settings used
            inference_ms: Time taken for inference
            explanation: Model's explanation of what it observed
            tags: List of tags from the LLM
            confidence: Model's confidence score (0.0-1.0)

        Returns:
            True if update succeeded, False if preview not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE threshold_summaries
                SET end_time = ?,
                    summary = ?,
                    screenshot_ids = ?,
                    screenshot_count = ?,
                    model_used = ?,
                    config_snapshot = ?,
                    inference_time_ms = ?,
                    explanation = ?,
                    tags = ?,
                    confidence = ?
                WHERE id = ? AND is_preview = 1
                """,
                (
                    end_time,
                    summary,
                    json.dumps(screenshot_ids),
                    len(screenshot_ids),
                    model,
                    json.dumps(config_snapshot) if config_snapshot else None,
                    inference_ms,
                    explanation,
                    json.dumps(tags) if tags else None,
                    confidence,
                    summary_id,
                ),
            )
            if cursor.rowcount > 0:
                # Update screenshot links
                conn.execute(
                    "DELETE FROM threshold_summary_screenshots WHERE summary_id = ?",
                    (summary_id,),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO threshold_summary_screenshots (summary_id, screenshot_id) VALUES (?, ?)",
                    [(summary_id, sid) for sid in screenshot_ids]
                )
                conn.commit()
                return True
            return False

    def get_screenshot_by_id(self, screenshot_id: int) -> Optional[Dict]:
        """Get a screenshot by its ID.

        Args:
            screenshot_id: The screenshot ID.

        Returns:
            Screenshot dict or None if not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, timestamp, filepath, window_title, app_name,
                       window_x, window_y, window_width, window_height,
                       monitor_name, monitor_width, monitor_height
                FROM screenshots
                WHERE id = ?
                """,
                (screenshot_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # =========================================================================
    # Tag Management Methods
    # =========================================================================

    def get_all_tags(self) -> Dict[str, int]:
        """Get all unique tags with their occurrence counts.

        Returns:
            Dict mapping tag name to occurrence count.
        """
        tag_counts = {}

        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT tags FROM threshold_summaries WHERE tags IS NOT NULL"
            )
            for row in cursor.fetchall():
                if row['tags']:
                    try:
                        tags = json.loads(row['tags'])
                        for tag in tags:
                            if tag:
                                tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass

        return tag_counts

    def consolidate_tags(self, canonical: str, variants: List[str]) -> int:
        """Replace variant tags with canonical tag in all summaries.

        Args:
            canonical: The canonical tag name to use.
            variants: List of variant tag names to replace.

        Returns:
            Number of summaries updated.
        """
        if not variants:
            return 0

        updated_count = 0

        with self.get_connection() as conn:
            # Get all summaries with tags
            cursor = conn.execute(
                "SELECT id, tags FROM threshold_summaries WHERE tags IS NOT NULL"
            )
            rows = cursor.fetchall()

            for row in rows:
                if not row['tags']:
                    continue

                try:
                    tags = json.loads(row['tags'])
                    original_tags = tags.copy()

                    # Replace variants with canonical
                    new_tags = []
                    has_canonical = False
                    for tag in tags:
                        if tag in variants:
                            if not has_canonical:
                                new_tags.append(canonical)
                                has_canonical = True
                            # Skip the variant (it's being consolidated)
                        elif tag == canonical:
                            if not has_canonical:
                                new_tags.append(canonical)
                                has_canonical = True
                        else:
                            new_tags.append(tag)

                    # Check if tags changed
                    if new_tags != original_tags:
                        conn.execute(
                            "UPDATE threshold_summaries SET tags = ? WHERE id = ?",
                            (json.dumps(new_tags), row['id'])
                        )
                        updated_count += 1

                except (json.JSONDecodeError, TypeError):
                    continue

            conn.commit()

        return updated_count

    # =========================================================================
    # Report Generation Methods
    # =========================================================================

    def get_summaries_in_range(self, start: 'datetime', end: 'datetime') -> List[Dict]:
        """Get all summaries within a datetime range.

        Queries both threshold_summaries and activity_sessions tables
        to find all available summaries.

        Args:
            start: Start datetime (inclusive).
            end: End datetime (inclusive).

        Returns:
            List of summary dicts ordered by start_time.
        """
        results = []

        with self.get_connection() as conn:
            # Get threshold summaries
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_ids,
                       screenshot_count, model_used, config_snapshot,
                       inference_time_ms, created_at, regenerated_from, project,
                       explanation, confidence,
                       'threshold' as source
                FROM threshold_summaries
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            )
            for row in cursor.fetchall():
                result = dict(row)
                result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                if result['config_snapshot']:
                    result['config_snapshot'] = json.loads(result['config_snapshot'])
                results.append(result)

            # Get session summaries
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, screenshot_count,
                       model_used, inference_time_ms,
                       'session' as source
                FROM activity_sessions
                WHERE summary IS NOT NULL
                  AND datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            )
            for row in cursor.fetchall():
                result = dict(row)
                result['screenshot_ids'] = []  # Sessions don't store this directly
                result['config_snapshot'] = None
                result['explanation'] = None  # Sessions don't have this
                result['confidence'] = None  # Sessions don't have this
                results.append(result)

        # Sort all results by start_time
        results.sort(key=lambda x: x['start_time'])
        return results

    def get_screenshots_in_range(
        self,
        start: 'datetime',
        end: 'datetime',
        limit: Optional[int] = None
    ) -> List[Dict]:
        """Get screenshots within a datetime range.

        Args:
            start: Start datetime (inclusive).
            end: End datetime (inclusive).
            limit: Maximum number of screenshots to return (None = no limit).

        Returns:
            List of screenshot dicts ordered by timestamp.
        """
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())

        query = """
            SELECT id, timestamp, filepath, dhash, window_title, app_name,
                   window_x, window_y, window_width, window_height,
                   monitor_name, monitor_width, monitor_height
            FROM screenshots
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """
        if limit is not None:
            query += f" LIMIT {int(limit)}"

        with self.get_connection() as conn:
            cursor = conn.execute(query, (start_ts, end_ts))
            return [dict(row) for row in cursor.fetchall()]

    def get_sessions_in_range(self, start: 'datetime', end: 'datetime') -> List[Dict]:
        """Get activity sessions within a datetime range.

        Args:
            start: Start datetime (inclusive).
            end: End datetime (inclusive).

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
                WHERE datetime(start_time) >= datetime(?)
                  AND (end_time IS NULL OR datetime(end_time) <= datetime(?))
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("screenshot_ids_used"):
                    result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])
                results.append(result)
            return results

    def has_active_session_in_range(self, start: 'datetime', end: 'datetime') -> bool:
        """Check if any session was active during the given time range.

        A session overlaps if it started before the range ends AND
        (ended after the range starts OR is still active).

        This is used to detect AFK periods - if no session overlaps with
        the time range, the user was AFK for the entire period.

        Args:
            start: Start datetime of the range.
            end: End datetime of the range.

        Returns:
            True if at least one session overlaps with the range.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM activity_sessions
                WHERE datetime(start_time) < datetime(?)
                  AND (end_time IS NULL OR datetime(end_time) > datetime(?))
                LIMIT 1
                """,
                (end.isoformat(), start.isoformat()),
            )
            return cursor.fetchone() is not None

    def get_recent_sessions(self, limit: int = 20) -> List[Dict]:
        """Get most recent completed sessions, ordered by end_time DESC.

        Used for session merging in activity-based summarization -
        need to walk backward through recent sessions to find logical
        session boundaries after daemon restarts.

        Args:
            limit: Maximum number of sessions to return (default: 20).

        Returns:
            List of session dicts ordered by end_time DESC (most recent first).
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, duration_seconds, summary,
                       screenshot_count, unique_windows, model_used, inference_time_ms,
                       prompt_text, screenshot_ids_used
                FROM activity_sessions
                WHERE end_time IS NOT NULL
                ORDER BY end_time DESC
                LIMIT ?
                """,
                (limit,),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("screenshot_ids_used"):
                    result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])
                results.append(result)
            return results

    def get_sessions_without_summaries(self, min_duration_seconds: int = 300) -> List[Dict]:
        """Find completed sessions that have no corresponding summary.

        Used for startup recovery in activity-based summarization -
        detects sessions that ended without triggering a summary
        (e.g., daemon was killed before summary was generated).

        A session "has a summary" if any threshold_summary's time range
        overlaps with the session's time range.

        Args:
            min_duration_seconds: Minimum session duration to consider (default: 300s = 5min).

        Returns:
            List of session dicts that need summarization, ordered by start_time ASC.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT s.id, s.start_time, s.end_time, s.duration_seconds, s.summary,
                       s.screenshot_count, s.unique_windows, s.model_used, s.inference_time_ms,
                       s.prompt_text, s.screenshot_ids_used
                FROM activity_sessions s
                WHERE s.end_time IS NOT NULL
                  AND s.duration_seconds >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM threshold_summaries ts
                      WHERE datetime(ts.start_time) < datetime(s.end_time)
                        AND datetime(ts.end_time) > datetime(s.start_time)
                  )
                ORDER BY s.start_time ASC
                """,
                (min_duration_seconds,),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("screenshot_ids_used"):
                    result["screenshot_ids_used"] = json.loads(result["screenshot_ids_used"])
                results.append(result)
            return results

    # =========================================================================
    # Project-Aware Summary Methods
    # =========================================================================

    def get_last_summary_for_project(self, project: str) -> Optional[str]:
        """Get most recent summary for a specific project.

        Args:
            project: Project name to query.

        Returns:
            Summary text or None if no summaries exist for this project.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT summary FROM threshold_summaries
                WHERE project = ?
                ORDER BY end_time DESC
                LIMIT 1
                """,
                (project,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def get_summaries_by_project(
        self, start: 'datetime', end: 'datetime'
    ) -> Dict[str, List[Dict]]:
        """Get summaries grouped by project for a time range.

        Args:
            start: Start datetime (inclusive).
            end: End datetime (inclusive).

        Returns:
            Dict mapping project_name -> list of summary dicts.
        """
        from collections import defaultdict

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, start_time, end_time, summary, project, screenshot_ids,
                       screenshot_count, model_used, inference_time_ms, created_at
                FROM threshold_summaries
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            )

            grouped = defaultdict(list)
            for row in cursor.fetchall():
                result = dict(row)
                if result.get('screenshot_ids'):
                    result['screenshot_ids'] = json.loads(result['screenshot_ids'])
                project = result.get('project') or 'unknown'
                grouped[project].append(result)

            return dict(grouped)

    # =========================================================================
    # Window Focus Tracking Methods
    # =========================================================================

    def save_focus_event(
        self,
        window_title: str,
        app_name: str,
        window_class: str,
        start_time: 'datetime',
        end_time: 'datetime',
        session_id: int = None,
        terminal_context: str = None
    ) -> int:
        """Save a completed focus event.

        Args:
            window_title: Title of the focused window.
            app_name: Application name/class.
            window_class: X11 window class.
            start_time: When focus started.
            end_time: When focus ended.
            session_id: Optional session ID to link to.
            terminal_context: JSON string with terminal introspection data.

        Returns:
            ID of the saved focus event.
        """
        duration = (end_time - start_time).total_seconds()

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO window_focus_events
                (window_title, app_name, window_class, start_time, end_time, duration_seconds, session_id, terminal_context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (window_title, app_name, window_class,
                 start_time.isoformat(), end_time.isoformat(),
                 duration, session_id, terminal_context)
            )
            conn.commit()
            return cursor.lastrowid

    def get_focus_events_in_range(
        self, start: 'datetime', end: 'datetime', require_session: bool = False
    ) -> List[Dict]:
        """Get all focus events that started within time range.

        Args:
            start: Start datetime (inclusive).
            end: End datetime (inclusive).
            require_session: If True, exclude events with NULL session_id (AFK periods).

        Returns:
            List of focus event dicts ordered by start_time.
        """
        session_filter = "AND session_id IS NOT NULL" if require_session else ""
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, window_title, app_name, window_class,
                       start_time, end_time, duration_seconds, session_id, terminal_context
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                  {session_filter}
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat())
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_focus_events_overlapping_range(
        self, start: 'datetime', end: 'datetime', require_session: bool = False
    ) -> List[Dict]:
        """Get all focus events that overlap with time range.

        This includes events that:
        - Started within the range
        - Started before the range but ended during or after range start

        Args:
            start: Start datetime.
            end: End datetime.
            require_session: If True, exclude events with NULL session_id (AFK periods).

        Returns:
            List of focus event dicts ordered by start_time.
        """
        session_filter = "AND session_id IS NOT NULL" if require_session else ""
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT id, window_title, app_name, window_class,
                       start_time, end_time, duration_seconds, session_id, terminal_context
                FROM window_focus_events
                WHERE datetime(start_time) < datetime(?)
                  AND (datetime(end_time) > datetime(?) OR end_time IS NULL)
                  {session_filter}
                ORDER BY start_time ASC
                """,
                (end.isoformat(), start.isoformat())
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_app_durations_in_range(self, start: 'datetime', end: 'datetime') -> List[Dict]:
        """Aggregate duration by app, sorted by total time descending.

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            List of dicts with app_name, total_seconds, event_count.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    app_name,
                    SUM(duration_seconds) as total_seconds,
                    COUNT(*) as event_count
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                GROUP BY app_name
                ORDER BY total_seconds DESC
                """,
                (start.isoformat(), end.isoformat())
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_window_durations_in_range(
        self,
        start: 'datetime',
        end: 'datetime',
        limit: int = 20
    ) -> List[Dict]:
        """Aggregate duration by app + window title.

        Args:
            start: Start datetime.
            end: End datetime.
            limit: Maximum results to return.

        Returns:
            List of dicts with app_name, window_title, total_seconds, event_count.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    app_name,
                    window_title,
                    SUM(duration_seconds) as total_seconds,
                    COUNT(*) as event_count
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                GROUP BY app_name, window_title
                ORDER BY total_seconds DESC
                LIMIT ?
                """,
                (start.isoformat(), end.isoformat(), limit)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_hourly_app_breakdown(self, date: str) -> List[Dict]:
        """Get app usage breakdown by hour for a specific day.

        Args:
            date: Date string in YYYY-MM-DD format.

        Returns:
            List of dicts with hour, app_name, seconds.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    CAST(strftime('%H', start_time) AS INTEGER) as hour,
                    app_name,
                    SUM(duration_seconds) as seconds
                FROM window_focus_events
                WHERE date(start_time) = ?
                GROUP BY hour, app_name
                ORDER BY hour, seconds DESC
                """,
                (date,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_context_switch_count(self, start: 'datetime', end: 'datetime') -> int:
        """Count number of app switches in time range.

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            Number of times app changed.
        """
        events = self.get_focus_events_in_range(start, end)
        if len(events) < 2:
            return 0
        return sum(
            1 for i in range(1, len(events))
            if events[i]['app_name'] != events[i - 1]['app_name']
        )

    def get_longest_focus_sessions(
        self,
        start: 'datetime',
        end: 'datetime',
        min_duration_minutes: int = 10,
        limit: int = 10
    ) -> List[Dict]:
        """Find longest uninterrupted focus periods (deep work sessions).

        Args:
            start: Start datetime.
            end: End datetime.
            min_duration_minutes: Minimum duration to include.
            limit: Maximum results.

        Returns:
            List of dicts with app_name, window_title, start_time, end_time, duration_seconds.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    app_name,
                    window_title,
                    start_time,
                    end_time,
                    duration_seconds
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND duration_seconds >= ?
                ORDER BY duration_seconds DESC
                LIMIT ?
                """,
                (start.isoformat(), end.isoformat(), min_duration_minutes * 60, limit)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_deep_work_percentage(
        self,
        start: 'datetime',
        end: 'datetime',
        min_focus_minutes: int = 10
    ) -> float:
        """Calculate percentage of tracked time spent in deep work (sustained focus).

        Deep work is defined as focus sessions lasting at least min_focus_minutes
        without switching apps/windows.

        Args:
            start: Start datetime.
            end: End datetime.
            min_focus_minutes: Minimum duration to count as deep work (default 10 min).

        Returns:
            Percentage (0-100) of total tracked time in deep work sessions.
        """
        with self.get_connection() as conn:
            # Get total tracked time
            cursor = conn.execute(
                """
                SELECT COALESCE(SUM(duration_seconds), 0) as total
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                """,
                (start.isoformat(), end.isoformat())
            )
            total_seconds = cursor.fetchone()['total']

            if total_seconds == 0:
                return 0.0

            # Get time in deep work sessions (>= min_focus_minutes)
            cursor = conn.execute(
                """
                SELECT COALESCE(SUM(duration_seconds), 0) as deep_work
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                  AND duration_seconds >= ?
                """,
                (start.isoformat(), end.isoformat(), min_focus_minutes * 60)
            )
            deep_work_seconds = cursor.fetchone()['deep_work']

            return (deep_work_seconds / total_seconds) * 100

    def get_longest_streak(
        self,
        start: 'datetime',
        end: 'datetime'
    ) -> Dict:
        """Get the longest uninterrupted focus streak in the time range.

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            Dict with:
                - duration_seconds: Length of the streak
                - start_time: When the streak started
                - end_time: When the streak ended
                - app_name: Primary app during the streak
                - window_title: Primary window during the streak
            Returns empty dict if no focus events.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    app_name,
                    window_title,
                    start_time,
                    end_time,
                    duration_seconds
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                ORDER BY duration_seconds DESC
                LIMIT 1
                """,
                (start.isoformat(), end.isoformat())
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {}

    def get_total_tracked_time(
        self,
        start: 'datetime',
        end: 'datetime'
    ) -> int:
        """Get total tracked time in seconds for a time range.

        Only includes time during active sessions (excludes AFK periods).

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            Total tracked time in seconds.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COALESCE(SUM(duration_seconds), 0) as total
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                """,
                (start.isoformat(), end.isoformat())
            )
            return cursor.fetchone()['total']

    def get_work_break_balance(
        self,
        start: 'datetime',
        end: 'datetime'
    ) -> Dict:
        """Get work vs break time balance for a time range.

        Calculates active work time (during sessions) vs AFK/break time.

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            Dict with:
                active_seconds: Total active work time in seconds
                break_seconds: Total break time in seconds
                break_count: Number of breaks taken
                workday_span_seconds: Time from first to last session
                active_percentage: Active time as % of workday span
                longest_work_without_break_seconds: Longest focus period
                longest_work_without_break_start: Start of longest focus
                longest_work_without_break_end: End of longest focus
                first_session_start: When first session started
                last_session_end: When last session ended
                last_break_end: When the most recent break ended (for live timer)
                is_active: Whether there's an active session right now
        """
        total_span = (end - start).total_seconds()

        with self.get_connection() as conn:
            # Get active time from focus events with session
            cursor = conn.execute(
                """
                SELECT COALESCE(SUM(duration_seconds), 0) as active_time
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                """,
                (start.isoformat(), end.isoformat())
            )
            active_seconds = cursor.fetchone()['active_time']

            # Find longest continuous work period without a break
            # A "break" is defined as an AFK period (gap between sessions)
            cursor = conn.execute(
                """
                SELECT
                    start_time,
                    end_time,
                    CAST((julianday(end_time) - julianday(start_time)) * 86400 AS INTEGER) as duration
                FROM activity_sessions
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                  AND end_time IS NOT NULL
                ORDER BY start_time
                """,
                (start.isoformat(), end.isoformat())
            )
            sessions = cursor.fetchall()

            longest_continuous = 0
            longest_continuous_start = None
            longest_continuous_end = None
            current_continuous = 0
            current_continuous_start = None
            last_end = None
            break_count = 0
            total_break_seconds = 0
            last_break_end = None  # When the most recent break ended (start of current work period)

            for session in sessions:
                session_duration = session['duration'] or 0

                if last_end:
                    # Check gap between sessions (break time)
                    gap_seconds = (
                        datetime.fromisoformat(session['start_time']) -
                        datetime.fromisoformat(last_end)
                    ).total_seconds()

                    # Only count as a break if gap is 1 min to 2 hours
                    # (< 1 min is just context switch, > 2 hours is probably AFK/lunch/end of day)
                    if 60 <= gap_seconds <= 7200:
                        break_count += 1
                        total_break_seconds += gap_seconds

                    # If gap > 5 minutes, reset continuous work counter
                    if gap_seconds > 300:
                        if current_continuous > longest_continuous:
                            longest_continuous = current_continuous
                            longest_continuous_start = current_continuous_start
                            longest_continuous_end = last_end
                        current_continuous = session_duration
                        current_continuous_start = session['start_time']
                        # This session started after a break
                        last_break_end = session['start_time']
                    else:
                        current_continuous += session_duration
                else:
                    current_continuous = session_duration
                    current_continuous_start = session['start_time']

                last_end = session['end_time']

            # Check if there's an active session (end_time IS NULL)
            cursor = conn.execute(
                """
                SELECT id, start_time FROM activity_sessions
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(start_time) <= datetime(?)
                  AND end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
                """,
                (start.isoformat(), end.isoformat())
            )
            active_session = cursor.fetchone()
            is_active = active_session is not None

            # Include active session in continuous work calculation
            if is_active and active_session:
                now = datetime.now()
                active_start = datetime.fromisoformat(active_session['start_time'])
                active_duration = int((now - active_start).total_seconds())

                if last_end:
                    # Check gap between last completed session and active session
                    gap_to_active = (active_start - datetime.fromisoformat(last_end)).total_seconds()

                    if gap_to_active > 300:  # > 5 min gap = break
                        # Check if previous continuous period was longest
                        if current_continuous > longest_continuous:
                            longest_continuous = current_continuous
                            longest_continuous_start = current_continuous_start
                            longest_continuous_end = last_end
                        # Start new continuous period with active session
                        current_continuous = active_duration
                        current_continuous_start = active_session['start_time']
                        last_break_end = active_session['start_time']
                    else:
                        # Merge with previous continuous period
                        current_continuous += active_duration
                else:
                    # Active session is the only session
                    current_continuous = active_duration
                    current_continuous_start = active_session['start_time']

                # Update last_end to now for the active session
                last_end = now.isoformat()

            # Check final period (includes active session if present)
            if current_continuous > longest_continuous:
                longest_continuous = current_continuous
                longest_continuous_start = current_continuous_start
                longest_continuous_end = last_end

        # Active percentage based on first session to last session span (not midnight to midnight)
        if sessions:
            first_session_start = sessions[0]['start_time']
            last_session_end = sessions[-1]['end_time']
            first_dt = datetime.fromisoformat(first_session_start)
            last_dt = datetime.fromisoformat(last_session_end)
            workday_span = (last_dt - first_dt).total_seconds()
            active_percentage = (active_seconds / workday_span * 100) if workday_span > 0 else 0
        elif is_active and active_session:
            # Only an active session exists (no completed sessions yet)
            first_session_start = active_session['start_time']
            last_session_end = None
            workday_span = 0
            active_percentage = 0
        else:
            first_session_start = None
            last_session_end = None
            workday_span = 0
            active_percentage = 0

        return {
            'active_seconds': int(active_seconds),
            'break_seconds': int(total_break_seconds),
            'break_count': break_count,
            'workday_span_seconds': int(workday_span),
            'active_percentage': round(active_percentage, 1),
            'longest_work_without_break_seconds': int(longest_continuous),
            'longest_work_without_break_start': longest_continuous_start,
            'longest_work_without_break_end': longest_continuous_end,
            'first_session_start': first_session_start,
            'last_session_end': last_session_end,
            'last_break_end': last_break_end,
            'is_active': is_active
        }

    def get_meetings_time(
        self,
        start: 'datetime',
        end: 'datetime'
    ) -> Dict:
        """Get time spent in meetings for a time range.

        Detects meetings via app names (Zoom, Teams, Meet) and window titles.

        Args:
            start: Start datetime.
            end: End datetime.

        Returns:
            Dict with meetings_seconds, meetings_count, meetings list.
        """
        # Meeting app patterns
        meeting_apps = ['zoom', 'teams', 'webex', 'gotomeeting', 'bluejeans']
        meeting_window_patterns = [
            '%meet.google%', '%zoom meeting%', '%zoom webinar%',
            '%teams%call%', '%teams%meeting%', '%webex%',
            '%huddle%', '%standup%'
        ]

        with self.get_connection() as conn:
            # Build query for meeting detection
            app_conditions = ' OR '.join(
                f"LOWER(app_name) LIKE '%{app}%'" for app in meeting_apps
            )
            window_conditions = ' OR '.join(
                f"LOWER(window_title) LIKE '{pattern}'" for pattern in meeting_window_patterns
            )

            cursor = conn.execute(
                f"""
                SELECT
                    app_name,
                    window_title,
                    start_time,
                    end_time,
                    duration_seconds
                FROM window_focus_events
                WHERE datetime(start_time) >= datetime(?)
                  AND datetime(end_time) <= datetime(?)
                  AND session_id IS NOT NULL
                  AND ({app_conditions} OR {window_conditions})
                ORDER BY start_time
                """,
                (start.isoformat(), end.isoformat())
            )
            meetings = cursor.fetchall()

            total_seconds = sum(m['duration_seconds'] or 0 for m in meetings)

            # Group into distinct meetings (merge if gap < 2 minutes)
            distinct_meetings = []
            current_meeting = None

            for m in meetings:
                if current_meeting is None:
                    current_meeting = {
                        'start': m['start_time'],
                        'end': m['end_time'],
                        'duration': m['duration_seconds'] or 0,
                        'app': m['app_name'],
                        'title': m['window_title']
                    }
                else:
                    gap = (
                        datetime.fromisoformat(m['start_time']) -
                        datetime.fromisoformat(current_meeting['end'])
                    ).total_seconds()

                    if gap < 120:  # Within 2 minutes, same meeting
                        current_meeting['end'] = m['end_time']
                        current_meeting['duration'] += m['duration_seconds'] or 0
                    else:
                        distinct_meetings.append(current_meeting)
                        current_meeting = {
                            'start': m['start_time'],
                            'end': m['end_time'],
                            'duration': m['duration_seconds'] or 0,
                            'app': m['app_name'],
                            'title': m['window_title']
                        }

            if current_meeting:
                distinct_meetings.append(current_meeting)

        return {
            'meetings_seconds': int(total_seconds),
            'meetings_count': len(distinct_meetings),
            'meetings': distinct_meetings
        }

    # =========================================================================
    # Exported Reports History
    # =========================================================================

    def save_exported_report(
        self,
        title: str,
        time_range: str,
        report_type: str,
        format: str,
        filename: str,
        filepath: str,
        file_size: int = None,
        start_time: 'datetime' = None,
        end_time: 'datetime' = None
    ) -> int:
        """Save exported report metadata to history.

        Args:
            title: Report title.
            time_range: Original time range string (e.g., "last week").
            report_type: Report type (summary, detailed, standup).
            format: Export format (markdown, html, pdf, json).
            filename: Just the filename.
            filepath: Full path to the file.
            file_size: Size of the exported file in bytes.
            start_time: Parsed start of time range.
            end_time: Parsed end of time range.

        Returns:
            ID of the inserted record.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO exported_reports
                    (title, time_range, report_type, format, filename, filepath,
                     file_size, start_time, end_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    time_range,
                    report_type,
                    format,
                    filename,
                    filepath,
                    file_size,
                    start_time.isoformat() if start_time else None,
                    end_time.isoformat() if end_time else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_exported_reports(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get exported reports history, most recent first.

        Args:
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            List of exported report dicts.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, title, time_range, report_type, format, filename, filepath,
                       file_size, start_time, end_time, created_at
                FROM exported_reports
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_exported_report(self, report_id: int) -> bool:
        """Delete an exported report record (does not delete file).

        Args:
            report_id: ID of the report to delete.

        Returns:
            True if a record was deleted.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM exported_reports WHERE id = ?",
                (report_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    # =========================================================================
    # Cached Reports (Daily/Weekly/Monthly)
    # =========================================================================

    def save_cached_report(
        self,
        period_type: str,
        period_date: str,
        start_time: 'datetime',
        end_time: 'datetime',
        executive_summary: str,
        sections: List[Dict] = None,
        analytics: Dict = None,
        summary_ids: List[int] = None,
        model_used: str = None,
        inference_time_ms: int = None,
        prompt_text: str = None,
        explanation: str = None,
        tags: List[str] = None,
        confidence: float = None,
        child_summary_ids: List[int] = None,
        is_regeneration: bool = False
    ) -> int:
        """Save or update a cached report.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'.
            period_date: Period identifier (e.g., '2025-12-30', '2025-W52', '2025-12').
            start_time: Start of the period.
            end_time: End of the period.
            executive_summary: Generated executive summary.
            sections: List of section dicts with title/content.
            analytics: Analytics data dict.
            summary_ids: IDs of source threshold summaries used.
            model_used: LLM model used for generation.
            inference_time_ms: Time taken to generate.
            prompt_text: Full prompt sent to LLM.
            explanation: LLM-provided explanation of the summary.
            tags: List of activity tags extracted from content.
            confidence: Confidence score (0.0-1.0).
            child_summary_ids: IDs of child summaries used for synthesis.
            is_regeneration: If True, set regenerated_at instead of created_at.

        Returns:
            ID of the inserted/updated record.
        """
        with self.get_connection() as conn:
            # Check if this is a regeneration (record exists)
            if is_regeneration:
                cursor = conn.execute(
                    """
                    UPDATE cached_reports SET
                        start_time = ?,
                        end_time = ?,
                        executive_summary = ?,
                        sections_json = ?,
                        analytics_json = ?,
                        summary_ids_json = ?,
                        model_used = ?,
                        inference_time_ms = ?,
                        prompt_text = ?,
                        explanation = ?,
                        tags = ?,
                        confidence = ?,
                        child_summary_ids = ?,
                        regenerated_at = CURRENT_TIMESTAMP
                    WHERE period_type = ? AND period_date = ?
                    """,
                    (
                        start_time.isoformat(),
                        end_time.isoformat(),
                        executive_summary,
                        json.dumps(sections) if sections is not None else None,
                        json.dumps(analytics) if analytics is not None else None,
                        json.dumps(summary_ids) if summary_ids is not None else None,
                        model_used,
                        inference_time_ms,
                        prompt_text,
                        explanation,
                        json.dumps(tags) if tags is not None else None,
                        confidence,
                        json.dumps(child_summary_ids) if child_summary_ids is not None else None,
                        period_type,
                        period_date,
                    ),
                )
                conn.commit()
                if cursor.rowcount > 0:
                    # Get the ID of the updated record
                    id_cursor = conn.execute(
                        "SELECT id FROM cached_reports WHERE period_type = ? AND period_date = ?",
                        (period_type, period_date),
                    )
                    return id_cursor.fetchone()[0]

            # Insert or update (for new records)
            cursor = conn.execute(
                """
                INSERT INTO cached_reports
                    (period_type, period_date, start_time, end_time, executive_summary,
                     sections_json, analytics_json, summary_ids_json, model_used, inference_time_ms,
                     prompt_text, explanation, tags, confidence, child_summary_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_type, period_date) DO UPDATE SET
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    executive_summary = excluded.executive_summary,
                    sections_json = excluded.sections_json,
                    analytics_json = excluded.analytics_json,
                    summary_ids_json = excluded.summary_ids_json,
                    model_used = excluded.model_used,
                    inference_time_ms = excluded.inference_time_ms,
                    prompt_text = excluded.prompt_text,
                    explanation = excluded.explanation,
                    tags = excluded.tags,
                    confidence = excluded.confidence,
                    child_summary_ids = excluded.child_summary_ids,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    period_type,
                    period_date,
                    start_time.isoformat(),
                    end_time.isoformat(),
                    executive_summary,
                    json.dumps(sections) if sections is not None else None,
                    json.dumps(analytics) if analytics is not None else None,
                    json.dumps(summary_ids) if summary_ids is not None else None,
                    model_used,
                    inference_time_ms,
                    prompt_text,
                    explanation,
                    json.dumps(tags) if tags is not None else None,
                    confidence,
                    json.dumps(child_summary_ids) if child_summary_ids is not None else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_cached_report(self, period_type: str, period_date: str) -> Optional[Dict]:
        """Get a cached report by period.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'.
            period_date: Period identifier.

        Returns:
            Cached report dict or None if not found.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, period_type, period_date, start_time, end_time,
                       executive_summary, sections_json, analytics_json,
                       summary_ids_json, model_used, inference_time_ms, created_at,
                       prompt_text, explanation, tags, confidence,
                       child_summary_ids, regenerated_at
                FROM cached_reports
                WHERE period_type = ? AND period_date = ?
                """,
                (period_type, period_date),
            )
            row = cursor.fetchone()
            if not row:
                return None

            result = dict(row)
            if result.get('sections_json'):
                result['sections'] = json.loads(result['sections_json'])
            if result.get('analytics_json'):
                result['analytics'] = json.loads(result['analytics_json'])
            if result.get('summary_ids_json'):
                result['summary_ids'] = json.loads(result['summary_ids_json'])
            if result.get('tags'):
                result['tags'] = json.loads(result['tags'])
            if result.get('child_summary_ids'):
                result['child_summary_ids'] = json.loads(result['child_summary_ids'])
            return result

    def get_cached_reports_in_range(
        self,
        period_type: str,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Get cached reports within a date range.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'.
            start_date: Start period date (inclusive).
            end_date: End period date (inclusive).

        Returns:
            List of cached report dicts ordered by period_date.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, period_type, period_date, start_time, end_time,
                       executive_summary, sections_json, analytics_json,
                       summary_ids_json, model_used, inference_time_ms, created_at,
                       prompt_text, explanation, tags, confidence,
                       child_summary_ids, regenerated_at
                FROM cached_reports
                WHERE period_type = ?
                  AND period_date >= ?
                  AND period_date <= ?
                ORDER BY period_date ASC
                """,
                (period_type, start_date, end_date),
            )
            results = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get('sections_json'):
                    result['sections'] = json.loads(result['sections_json'])
                if result.get('analytics_json'):
                    result['analytics'] = json.loads(result['analytics_json'])
                if result.get('summary_ids_json'):
                    result['summary_ids'] = json.loads(result['summary_ids_json'])
                if result.get('tags'):
                    result['tags'] = json.loads(result['tags'])
                if result.get('child_summary_ids'):
                    result['child_summary_ids'] = json.loads(result['child_summary_ids'])
                results.append(result)
            return results

    def get_missing_daily_reports(self, days_back: int = 7) -> List[str]:
        """Get dates that don't have cached daily reports.

        Args:
            days_back: How many days to look back.

        Returns:
            List of date strings (YYYY-MM-DD) missing cached reports.
        """
        from datetime import timedelta

        today = datetime.now().date()
        expected_dates = set()
        for i in range(1, days_back + 1):  # Start from 1 (yesterday)
            d = today - timedelta(days=i)
            expected_dates.add(d.strftime('%Y-%m-%d'))

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT period_date FROM cached_reports
                WHERE period_type = 'daily'
                  AND period_date >= ?
                """,
                ((today - timedelta(days=days_back)).strftime('%Y-%m-%d'),),
            )
            existing_dates = set(row[0] for row in cursor.fetchall())

        return sorted(expected_dates - existing_dates, reverse=True)

    def get_missing_weekly_reports(self, weeks_back: int = 4) -> List[str]:
        """Get weeks that don't have cached weekly reports.

        Args:
            weeks_back: How many weeks to look back.

        Returns:
            List of ISO week strings (YYYY-Www) missing cached reports.
        """
        from datetime import timedelta

        today = datetime.now().date()
        expected_weeks = set()
        # Calculate ISO weeks for previous weeks
        for i in range(1, weeks_back + 1):
            # Go back to the previous week's Monday
            days_since_monday = today.weekday()
            last_monday = today - timedelta(days=days_since_monday + (i * 7))
            iso_year, iso_week, _ = last_monday.isocalendar()
            expected_weeks.add(f"{iso_year}-W{iso_week:02d}")

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT period_date FROM cached_reports
                WHERE period_type = 'weekly'
                """,
            )
            existing_weeks = set(row[0] for row in cursor.fetchall())

        return sorted(expected_weeks - existing_weeks, reverse=True)

    def get_missing_monthly_reports(self, months_back: int = 3) -> List[str]:
        """Get months that don't have cached monthly reports.

        Args:
            months_back: How many months to look back.

        Returns:
            List of month strings (YYYY-MM) missing cached reports.
        """
        today = datetime.now().date()
        expected_months = set()
        # Calculate previous months
        for i in range(1, months_back + 1):
            # Calculate month by subtracting
            year = today.year
            month = today.month - i
            while month <= 0:
                month += 12
                year -= 1
            expected_months.add(f"{year}-{month:02d}")

        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT period_date FROM cached_reports
                WHERE period_type = 'monthly'
                """,
            )
            existing_months = set(row[0] for row in cursor.fetchall())

        return sorted(expected_months - existing_months, reverse=True)

    def delete_cached_report(self, period_type: str, period_date: str) -> bool:
        """Delete a cached report.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'.
            period_date: Period identifier.

        Returns:
            True if a record was deleted.
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM cached_reports WHERE period_type = ? AND period_date = ?",
                (period_type, period_date),
            )
            conn.commit()
            return cursor.rowcount > 0