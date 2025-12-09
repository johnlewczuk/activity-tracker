"""Natural language time range parser for Activity Tracker reports.

This module provides parsing of natural language time references into
datetime ranges. It supports various formats like "today", "last week",
"past 3 hours", and specific date ranges.

Example:
    >>> parser = TimeParser()
    >>> start, end = parser.parse("last week")
    >>> print(parser.describe_range(start, end))
    'Dec 02 - Dec 08, 2025'
"""

from datetime import datetime, timedelta
from typing import Tuple
from dateutil import parser as dateutil_parser
import re


class TimeParser:
    """Parse natural language time references into datetime ranges.

    Supports various time expressions including:
    - Relative: "today", "yesterday", "this week", "last month"
    - Periods: "this morning", "yesterday afternoon", "this evening"
    - Duration: "last 3 days", "past 2 hours"
    - Weekdays: "monday", "last friday"
    - Exact dates: "2025-12-09", "2025-12-01 to 2025-12-07"

    Attributes:
        now: Reference datetime for relative calculations (defaults to now)
        today_start: Start of current day at midnight
    """

    def __init__(self, reference_time: datetime = None):
        """Initialize TimeParser with optional reference time.

        Args:
            reference_time: Base datetime for relative calculations.
                If None, uses current datetime.
        """
        self.now = reference_time or datetime.now()
        self.today_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)

    def parse(self, text: str) -> Tuple[datetime, datetime]:
        """Parse natural language to (start, end) datetime tuple.

        Args:
            text: Natural language time expression.

        Returns:
            Tuple of (start_datetime, end_datetime) representing the range.

        Raises:
            ValueError: If the text cannot be parsed.
        """
        text = text.lower().strip()

        # Define patterns and their handlers
        patterns = {
            # Today variants
            r'^today$': lambda: (self.today_start, self.now),
            r'^this morning$': lambda: (
                self.today_start.replace(hour=6),
                min(self.today_start.replace(hour=12), self.now)
            ),
            r'^this afternoon$': lambda: (
                self.today_start.replace(hour=12),
                min(self.today_start.replace(hour=18), self.now)
            ),
            r'^this evening$': lambda: (
                self.today_start.replace(hour=18),
                self.now
            ),
            r'^since this morning$': lambda: (
                self.today_start.replace(hour=6),
                self.now
            ),
            r'^since lunch$': lambda: (
                self.today_start.replace(hour=12),
                self.now
            ),

            # Yesterday
            r'^yesterday$': lambda: (
                self.today_start - timedelta(days=1),
                self.today_start - timedelta(seconds=1)
            ),
            r'^yesterday morning$': lambda: (
                (self.today_start - timedelta(days=1)).replace(hour=6),
                (self.today_start - timedelta(days=1)).replace(hour=12)
            ),
            r'^yesterday afternoon$': lambda: (
                (self.today_start - timedelta(days=1)).replace(hour=12),
                (self.today_start - timedelta(days=1)).replace(hour=18)
            ),

            # This week/month
            r'^this week$': lambda: (
                self.today_start - timedelta(days=self.now.weekday()),  # Monday
                self.now
            ),
            r'^this month$': lambda: (
                self.today_start.replace(day=1),
                self.now
            ),

            # Last week/month
            r'^last week$': lambda: self._last_week(),
            r'^last month$': lambda: self._last_month(),

            # Relative days
            r'^last (\d+) days?$': lambda m: (
                self.today_start - timedelta(days=int(m.group(1))),
                self.now
            ),
            r'^past (\d+) days?$': lambda m: (
                self.today_start - timedelta(days=int(m.group(1))),
                self.now
            ),
            r'^last (\d+) hours?$': lambda m: (
                self.now - timedelta(hours=int(m.group(1))),
                self.now
            ),
            r'^past (\d+) hours?$': lambda m: (
                self.now - timedelta(hours=int(m.group(1))),
                self.now
            ),

            # Specific days
            r'^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$':
                lambda m: self._specific_weekday(m.group(1)),
            r'^last (monday|tuesday|wednesday|thursday|friday|saturday|sunday)$':
                lambda m: self._specific_weekday(m.group(1), last=True),

            # Date ranges
            r'^(\d{4}-\d{2}-\d{2})$': lambda m: self._single_date(m.group(1)),
            r'^(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})$':
                lambda m: self._date_range(m.group(1), m.group(2)),
        }

        for pattern, handler in patterns.items():
            match = re.match(pattern, text)
            if match:
                if match.groups():
                    return handler(match)
                else:
                    return handler()

        # Try dateutil as fallback
        try:
            parsed = dateutil_parser.parse(text, fuzzy=True)
            return (
                parsed.replace(hour=0, minute=0, second=0),
                parsed.replace(hour=23, minute=59, second=59)
            )
        except Exception:
            raise ValueError(f"Could not parse time range: {text}")

    def _last_week(self) -> Tuple[datetime, datetime]:
        """Get Monday to Sunday of previous week."""
        last_monday = self.today_start - timedelta(days=self.now.weekday() + 7)
        last_sunday = last_monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return (last_monday, last_sunday)

    def _last_month(self) -> Tuple[datetime, datetime]:
        """Get first to last day of previous month."""
        first_of_this_month = self.today_start.replace(day=1)
        last_of_prev_month = first_of_this_month - timedelta(days=1)
        first_of_prev_month = last_of_prev_month.replace(day=1)
        return (
            first_of_prev_month,
            last_of_prev_month.replace(hour=23, minute=59, second=59)
        )

    def _specific_weekday(self, day_name: str, last: bool = False) -> Tuple[datetime, datetime]:
        """Get date range for a specific weekday.

        Args:
            day_name: Name of the weekday (e.g., 'monday')
            last: If True, always returns last week's instance
        """
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        target_weekday = days.index(day_name)
        current_weekday = self.now.weekday()

        days_ago = (current_weekday - target_weekday) % 7
        if days_ago == 0 and not last:
            days_ago = 0  # Today
        elif days_ago == 0 or last:
            days_ago += 7  # Last week's instance

        target_date = self.today_start - timedelta(days=days_ago)
        return (target_date, target_date.replace(hour=23, minute=59, second=59))

    def _single_date(self, date_str: str) -> Tuple[datetime, datetime]:
        """Parse a single date string."""
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return (dt, dt.replace(hour=23, minute=59, second=59))

    def _date_range(self, start_str: str, end_str: str) -> Tuple[datetime, datetime]:
        """Parse a date range from two date strings."""
        start = datetime.strptime(start_str, '%Y-%m-%d')
        end = datetime.strptime(end_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        return (start, end)

    def describe_range(self, start: datetime, end: datetime) -> str:
        """Generate human-readable description of a time range.

        Args:
            start: Start datetime of the range.
            end: End datetime of the range.

        Returns:
            Formatted string describing the range.
        """
        if start.date() == end.date():
            return start.strftime('%A, %B %d, %Y')
        elif (end - start).days <= 7:
            return f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"
        else:
            return f"{start.strftime('%B %d')} - {end.strftime('%B %d, %Y')}"
