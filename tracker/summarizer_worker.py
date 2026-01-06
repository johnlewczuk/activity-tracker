"""Background summarization worker with activity-based session triggers.

This module implements a background worker that triggers summarization when
a user session ends (goes AFK). Instead of fixed periodic intervals, summaries
are generated based on natural activity boundaries.

The worker supports:
- Activity-based summarization (triggered on session end with debouncing)
- Session merging for daemon-restart fragmentation (gaps < 60s merged)
- Startup recovery for unsummarized sessions
- Time-range based summarization for manual backfilling
- Regeneration of existing summaries with new settings
- Context continuity via previous summary inclusion
- OCR extraction for unique window titles
- Focus-based app/window usage passed to LLM
"""

import json
import logging
import queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import ActivityStorage
    from .config import ConfigManager

logger = logging.getLogger(__name__)


class SummarizerWorker:
    """Background worker with activity-based session summarization.

    Triggers summarization when user sessions end (AFK detected), with
    debouncing to handle brief returns. Merges daemon-restart-fragmented
    sessions automatically.

    Attributes:
        storage: ActivityStorage instance for database access
        config: ConfigManager instance for configuration
        summarizer: HybridSummarizer for generating summaries
    """

    def __init__(self, storage: "ActivityStorage", config: "ConfigManager"):
        """Initialize the summarizer worker.

        Args:
            storage: ActivityStorage instance
            config: ConfigManager instance
        """
        self.storage = storage
        self.config = config
        self._summarizer = None  # Lazy load to avoid import issues
        self._summarizer_model = None  # Track which model the summarizer was created with
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._pending_queue: queue.Queue = queue.Queue()  # For regenerate/force/session_end tasks
        self._current_task: Optional[str] = None
        self._last_summarized_end: Optional[datetime] = None  # Track last summarized period
        self._last_daily_report_date: Optional[str] = None  # Track date of last daily report
        self._last_weekly_report_week: Optional[str] = None  # Track week of last weekly report
        self._last_monthly_report_month: Optional[str] = None  # Track month of last monthly report
        self._startup_backfill_done: bool = False  # Track if startup backfill is done
        self._pending_session_end: Optional[Tuple[int, datetime]] = None  # (session_id, scheduled_at)
        self._last_session_active_time: Optional[datetime] = None  # For debounce check
        # Preview summary tracking
        self._current_session_start: Optional[datetime] = None  # When active session started
        self._current_session_id: Optional[int] = None  # ID of current active session
        self._last_preview_time: Optional[datetime] = None  # When last preview was generated

    @property
    def summarizer(self):
        """Lazy-load the HybridSummarizer, recreating if model changed."""
        current_model = self.config.config.summarization.model
        current_host = self.config.config.summarization.ollama_host
        cfg = self.config.config.summarization

        # Recreate summarizer if model or host changed
        if self._summarizer is None or self._summarizer_model != current_model:
            from .vision import HybridSummarizer
            logger.info(f"Creating summarizer with model: {current_model}")
            self._summarizer = HybridSummarizer(
                model=current_model,
                ollama_host=current_host,
                max_samples=cfg.max_samples,
                sample_interval_minutes=cfg.sample_interval_minutes,
                focus_weighted_sampling=cfg.focus_weighted_sampling,
                include_focus_context=getattr(cfg, 'include_focus_context', True),
                include_screenshots=getattr(cfg, 'include_screenshots', True),
                include_ocr=getattr(cfg, 'include_ocr', True),
            )
            self._summarizer_model = current_model
        return self._summarizer

    def _get_schedule_slot(self, dt: datetime, frequency_minutes: int) -> datetime:
        """Get the schedule slot for a given datetime.

        Rounds down to the nearest frequency_minutes boundary.
        For frequency_minutes=15: returns hh:00, hh:15, hh:30, or hh:45.

        Args:
            dt: Datetime to get slot for
            frequency_minutes: Interval in minutes (e.g., 15, 30, 60)

        Returns:
            Datetime rounded down to the nearest slot boundary
        """
        # Calculate minutes since midnight
        minutes_since_midnight = dt.hour * 60 + dt.minute
        # Round down to nearest frequency_minutes boundary
        slot_minutes = (minutes_since_midnight // frequency_minutes) * frequency_minutes
        slot_hour = slot_minutes // 60
        slot_minute = slot_minutes % 60
        return dt.replace(hour=slot_hour, minute=slot_minute, second=0, microsecond=0)

    def _get_next_scheduled_time(self) -> datetime:
        """Calculate the next scheduled run time based on frequency_minutes.

        Returns a datetime aligned to the clock (e.g., hh:00, hh:15, hh:30, hh:45
        for frequency_minutes=15).

        Returns:
            Next scheduled datetime
        """
        frequency_minutes = self.config.config.summarization.frequency_minutes
        now = datetime.now()

        # Get current slot
        current_slot = self._get_schedule_slot(now, frequency_minutes)

        # Next slot is current_slot + frequency_minutes
        next_slot = current_slot + timedelta(minutes=frequency_minutes)

        return next_slot

    def _get_time_range_for_slot(self, slot_end: datetime) -> Tuple[datetime, datetime]:
        """Get the time range to summarize for a given slot.

        The range is from the previous slot to the current slot end time.

        Args:
            slot_end: End time of the slot (the scheduled run time)

        Returns:
            Tuple of (start_time, end_time)
        """
        frequency_minutes = self.config.config.summarization.frequency_minutes
        slot_start = slot_end - timedelta(minutes=frequency_minutes)
        return (slot_start, slot_end)

    def _find_last_summarized_time(self) -> Optional[datetime]:
        """Find the end time of the last summary to know where to resume from.

        Returns:
            End time of last summary, or None if no summaries exist
        """
        last_summary = self.storage.get_last_threshold_summary()
        if not last_summary:
            return None

        end_time_str = last_summary.get('end_time', '')
        try:
            if 'T' in end_time_str:
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                if end_time.tzinfo:
                    end_time = end_time.replace(tzinfo=None)
            else:
                end_time = datetime.strptime(end_time_str, '%Y-%m-%d %H:%M:%S')
            return end_time
        except (ValueError, TypeError):
            return None

    def start(self):
        """Start the background worker thread."""
        if self._running:
            logger.warning("SummarizerWorker already running")
            return

        self._running = True
        self._last_summarized_end = self._find_last_summarized_time()

        logger.info("SummarizerWorker starting with activity-based summarization")

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("SummarizerWorker started")

    def stop(self):
        """Stop the background worker thread."""
        if not self._running:
            return

        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("SummarizerWorker stopped")

    def check_and_queue(self):
        """DEPRECATED: Summarization now uses internal cron-like scheduling.

        This method is a no-op and will be removed in a future version.
        The worker now automatically schedules summarization at fixed clock
        times based on frequency_minutes (e.g., hh:00, hh:15, hh:30, hh:45).
        """
        # No-op: scheduling is now handled internally by _run_loop
        pass

    def queue_regenerate(self, summary_id: int):
        """Queue a summary for regeneration.

        Args:
            summary_id: ID of the summary to regenerate
        """
        self._pending_queue.put(('regenerate', summary_id))
        logger.info(f"Queued summary {summary_id} for regeneration")

    def queue_session_end(self, session_id: int):
        """Queue a session for summarization after it ends.

        Called by the daemon when a user goes AFK. The worker will
        apply debouncing before actually summarizing to handle
        brief returns.

        Args:
            session_id: ID of the session that just ended
        """
        scheduled_at = datetime.now()
        self._pending_queue.put(('session_end', (session_id, scheduled_at)))
        logger.info(f"Queued session {session_id} for summarization")

    def notify_session_start(self, session_id: int = None):
        """Notify the worker that a new session has started.

        Called by the daemon when user becomes active. Used for
        debounce logic - if a new session starts before debounce
        period expires, we skip summarizing the previous session.

        Also tracks session info for preview summary generation.

        Args:
            session_id: ID of the new session (for preview tracking)
        """
        self._last_session_active_time = datetime.now()
        self._current_session_start = datetime.now()
        self._current_session_id = session_id
        self._last_preview_time = None  # Reset preview timer for new session

    def force_summarize_pending(self, date: str = None) -> int:
        """Force immediate summarization of unsummarized time slots.

        Groups unsummarized screenshots into cron-aligned time slots and queues
        each slot for summarization. This is useful for backfilling gaps.

        Args:
            date: Optional date string (YYYY-MM-DD) to limit to a specific day.
                If None, processes all unsummarized screenshots.

        Returns:
            Number of time slots queued for summarization.
        """
        # Get unsummarized screenshots to find which time slots need processing
        unsummarized = self.storage.get_unsummarized_screenshots(
            require_session=False, date=date
        )
        if not unsummarized:
            logger.info("No unsummarized screenshots to process")
            return 0

        # Sort by timestamp
        unsummarized = sorted(unsummarized, key=lambda s: s['timestamp'])

        # Group into cron-aligned time slots
        frequency_minutes = self.config.config.summarization.frequency_minutes
        slots_to_process = set()

        for screenshot in unsummarized:
            ts = screenshot['timestamp']
            dt = datetime.fromtimestamp(ts)
            # Get the slot this screenshot belongs to
            slot_start = self._get_schedule_slot(dt, frequency_minutes)
            slot_end = slot_start + timedelta(minutes=frequency_minutes)
            slots_to_process.add((slot_start, slot_end))

        # Sort slots chronologically
        sorted_slots = sorted(slots_to_process, key=lambda x: x[0])

        # Filter out slots where user was entirely AFK
        active_slots = []
        afk_slots = 0
        for slot_start, slot_end in sorted_slots:
            if self.storage.has_active_session_in_range(slot_start, slot_end):
                active_slots.append((slot_start, slot_end))
            else:
                afk_slots += 1

        if afk_slots > 0:
            logger.info(f"Skipping {afk_slots} time slots where user was AFK")

        # Queue each active time slot for summarization
        for slot_start, slot_end in active_slots:
            self._pending_queue.put(('summarize_range', (slot_start, slot_end)))

        logger.info(
            f"Force-queued {len(active_slots)} time slots for summarization "
            f"({frequency_minutes}min intervals, skipped {afk_slots} AFK slots)"
        )
        return len(active_slots)

    def force_summarize_sessions(self, date: str = None) -> int:
        """Force immediate summarization of unsummarized sessions.

        Finds completed sessions that have no corresponding summary and
        queues them for summarization. This is the session-based approach
        (vs the deprecated threshold/time-slot approach).

        Args:
            date: Optional date string (YYYY-MM-DD) to limit to a specific day.
                If None, processes all unsummarized sessions.

        Returns:
            Number of sessions queued for summarization.
        """
        if not self.config.config.summarization.enabled:
            logger.info("Summarization is disabled, skipping force_summarize_sessions")
            return 0

        min_duration = self.config.config.summarization.min_session_duration_seconds
        unsummarized = self.storage.get_sessions_without_summaries(min_duration)

        if not unsummarized:
            logger.info("No unsummarized sessions found")
            return 0

        # Filter by date if specified
        if date:
            filtered = []
            for session in unsummarized:
                session_date = session['start_time'][:10]  # Extract YYYY-MM-DD
                if session_date == date:
                    filtered.append(session)
            unsummarized = filtered
            if not unsummarized:
                logger.info(f"No unsummarized sessions for date {date}")
                return 0

        logger.info(f"Found {len(unsummarized)} unsummarized sessions to process")

        # Queue each session for summarization
        for session in unsummarized:
            session_id = session['id']
            self._pending_queue.put(('session_end', (session_id, datetime.now())))
            logger.info(
                f"Queued session {session_id} for summarization: "
                f"{session['start_time']} - {session['end_time']}"
            )

        return len(unsummarized)

    def get_status(self) -> Dict:
        """Get current worker status.

        Returns:
            Dict with running state, current task, queue size, and mode.
        """
        return {
            "running": self._running,
            "current_task": self._current_task,
            "queue_size": self._pending_queue.qsize(),
            "mode": "activity-based",
        }

    def _run_loop(self):
        """Background loop with activity-based session summarization.

        Processes session_end events from the daemon with debouncing.
        Also processes manual tasks from the queue (regenerate, force).
        Additionally generates daily, weekly, and monthly reports on schedule.
        """
        logger.info("SummarizerWorker run loop started")

        while self._running:
            now = datetime.now()

            # Run startup tasks once
            if not self._startup_backfill_done:
                self._do_startup_backfill()
                self._check_unsummarized_sessions()
                self._startup_backfill_done = True

            # Check if we should generate daily reports (at/after midnight)
            self._maybe_generate_daily_reports(now)

            # Check if we should generate weekly reports (Sunday 00:05)
            self._maybe_generate_weekly_reports(now)

            # Check if we should generate monthly reports (1st of month 00:10)
            self._maybe_generate_monthly_reports(now)

            # Check if we should generate/update preview summary for active session
            self._maybe_generate_preview(now)

            # Process tasks from queue
            try:
                task = self._pending_queue.get(timeout=1)
                task_type, payload = task
                self._current_task = task_type

                try:
                    if task_type == 'session_end':
                        # Activity-based: summarize a completed session
                        session_id, scheduled_at = payload
                        self._process_session_end(session_id, scheduled_at)
                    elif task_type == 'summarize_range':
                        # Force summarize with time range (manual backfill)
                        start_time, end_time = payload
                        self._do_summarize_time_range(start_time, end_time)
                    elif task_type == 'summarize':
                        # Legacy: summarize screenshots list (for force_summarize_pending)
                        self._do_summarize_screenshots(payload)
                    elif task_type == 'regenerate':
                        self._do_regenerate(payload)
                    elif task_type == 'regenerate_report':
                        # Regenerate a hierarchical report (daily/weekly/monthly)
                        period_type, period_date = payload
                        self._do_regenerate_report(period_type, period_date)
                except Exception as e:
                    logger.error(f"Summarization task failed: {e}", exc_info=True)
                finally:
                    self._current_task = None
            except queue.Empty:
                # No tasks, continue to next iteration
                pass

        logger.info("SummarizerWorker run loop stopped")

    def _process_session_end(self, session_id: int, scheduled_at: datetime):
        """Process a session_end event with debouncing and session merging.

        Applies debounce logic: if user returned to activity after the session
        ended but before debounce period expired, skip summarization.

        Also merges consecutive sessions with short gaps (daemon restarts).

        Args:
            session_id: ID of the session that ended
            scheduled_at: When the session_end was scheduled
        """
        if not self.config.config.summarization.enabled:
            logger.debug("Summarization disabled, skipping session_end")
            return

        cfg = self.config.config.summarization
        debounce_seconds = cfg.session_debounce_seconds
        min_duration = cfg.min_session_duration_seconds
        merge_gap = cfg.session_merge_gap_seconds

        # Check debounce: did user return since this was scheduled?
        if self._last_session_active_time and self._last_session_active_time > scheduled_at:
            logger.info(
                f"Skipping session {session_id} - user returned within debounce period"
            )
            return

        # Wait for debounce period (already partially elapsed since scheduled_at)
        elapsed = (datetime.now() - scheduled_at).total_seconds()
        remaining_wait = max(0, debounce_seconds - elapsed)
        if remaining_wait > 0:
            time.sleep(remaining_wait)

        # Re-check after sleeping - user might have returned
        if self._last_session_active_time and self._last_session_active_time > scheduled_at:
            logger.info(
                f"Skipping session {session_id} - user returned during debounce"
            )
            return

        # Get session from storage
        session = self.storage.get_session(session_id)
        if not session or session.get('end_time') is None:
            logger.warning(f"Session {session_id} not found or not ended")
            return

        # Check minimum duration
        duration = session.get('duration_seconds', 0)
        if duration < min_duration:
            logger.info(
                f"Skipping session {session_id} - duration {duration}s < {min_duration}s minimum"
            )
            return

        # Get merged session bounds (handles daemon restart fragmentation)
        logical_start, logical_end = self._get_merged_session_bounds(
            session_id, merge_gap
        )

        if logical_start is None or logical_end is None:
            logger.warning(f"Could not determine bounds for session {session_id}")
            return

        logger.info(
            f"Summarizing session {session_id}: "
            f"{logical_start.strftime('%H:%M')} - {logical_end.strftime('%H:%M')}"
        )

        # Delete any preview summary before generating final summary
        deleted = self.storage.delete_preview_summaries()
        if deleted > 0:
            logger.info(f"Deleted {deleted} preview summary before final summary")

        # Clear session tracking
        self._current_session_start = None
        self._current_session_id = None
        self._last_preview_time = None

        # Use existing summarization method with merged bounds
        self._do_summarize_time_range(logical_start, logical_end)

    def _get_merged_session_bounds(
        self, session_id: int, max_gap_seconds: int
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Find logical session boundaries by merging restart-fragmented sessions.

        Walks backward from the given session, merging any sessions that have
        gaps < max_gap_seconds until a real AFK gap is found.

        Args:
            session_id: ID of the session to find bounds for
            max_gap_seconds: Maximum gap to consider as daemon restart (not AFK)

        Returns:
            Tuple of (logical_start, logical_end) datetimes, or (None, None) if not found
        """
        sessions = self.storage.get_recent_sessions(limit=20)

        if not sessions:
            return (None, None)

        # Find our session in the list
        target_session = None
        target_idx = None
        for i, s in enumerate(sessions):
            if s['id'] == session_id:
                target_session = s
                target_idx = i
                break

        if target_session is None:
            # Session not in recent list, try direct lookup
            target_session = self.storage.get_session(session_id)
            if target_session is None:
                return (None, None)
            # Return its own bounds without merging
            start = datetime.fromisoformat(target_session['start_time'].replace('Z', ''))
            end = datetime.fromisoformat(target_session['end_time'].replace('Z', ''))
            return (start, end)

        # Parse end_time for the target session
        logical_end = datetime.fromisoformat(
            target_session['end_time'].replace('Z', '')
        )
        logical_start = datetime.fromisoformat(
            target_session['start_time'].replace('Z', '')
        )

        # Walk backward through older sessions, merging short gaps
        # Sessions are ordered by end_time DESC, so older sessions have higher indices
        current_start = logical_start
        for prev_session in sessions[target_idx + 1:]:
            prev_end = datetime.fromisoformat(
                prev_session['end_time'].replace('Z', '')
            )
            prev_start = datetime.fromisoformat(
                prev_session['start_time'].replace('Z', '')
            )

            # Calculate gap between previous session end and current session start
            gap_seconds = (current_start - prev_end).total_seconds()

            if 0 < gap_seconds < max_gap_seconds:
                # Short gap - merge this session
                logger.debug(
                    f"Merging session {prev_session['id']} (gap={gap_seconds:.1f}s)"
                )
                logical_start = prev_start
                current_start = prev_start
            else:
                # Real AFK gap found, stop merging
                break

        return (logical_start, logical_end)

    def _check_unsummarized_sessions(self):
        """Check for sessions that ended without summaries (startup recovery).

        Called once at startup to catch sessions that ended while the daemon
        was down or before the summarization could complete.
        """
        if not self.config.config.summarization.enabled:
            return

        min_duration = self.config.config.summarization.min_session_duration_seconds
        unsummarized = self.storage.get_sessions_without_summaries(min_duration)

        if not unsummarized:
            logger.info("No unsummarized sessions found at startup")
            return

        logger.info(f"Found {len(unsummarized)} unsummarized sessions at startup")

        for session in unsummarized:
            session_id = session['id']
            start_time = datetime.fromisoformat(session['start_time'].replace('Z', ''))
            end_time = datetime.fromisoformat(session['end_time'].replace('Z', ''))

            logger.info(
                f"Recovering session {session_id}: "
                f"{start_time.strftime('%Y-%m-%d %H:%M')} - {end_time.strftime('%H:%M')}"
            )

            try:
                self._do_summarize_time_range(start_time, end_time)
            except Exception as e:
                logger.error(f"Failed to recover session {session_id}: {e}")

    def _do_summarize_time_range(self, start_time: datetime, end_time: datetime):
        """Generate summary for a time range.

        This is the primary summarization method used by scheduled runs.
        It gathers screenshots and focus events from the time range and
        sends them to the LLM.

        Skips summarization if the user was AFK for the entire period
        (no active session overlapping with the time range).

        Args:
            start_time: Start of the time range
            end_time: End of the time range
        """
        logger.info(
            f"Summarizing time range: {start_time.strftime('%H:%M')} - "
            f"{end_time.strftime('%H:%M')}"
        )

        # Skip if a summary already exists for this time range (prevents duplicates)
        start_iso = start_time.isoformat()
        end_iso = end_time.isoformat()
        if self.storage.has_summary_for_time_range(start_iso, end_iso):
            logger.info(
                f"Skipping - summary already exists for time range "
                f"({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')})"
            )
            return

        # Skip if user was AFK for the entire period
        if not self.storage.has_active_session_in_range(start_time, end_time):
            logger.info(
                f"Skipping summarization - user was AFK for entire period "
                f"({start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')})"
            )
            return

        # Check if summarizer is available
        if not self.summarizer.is_available():
            logger.error("Summarizer not available (check Ollama and Tesseract)")
            return

        # Get screenshots in the time range (may be empty if screenshots disabled)
        screenshots = self.storage.get_screenshots_in_range(start_time, end_time)
        screenshots = sorted(screenshots, key=lambda s: s['timestamp'])

        # Get focus events for the time range
        focus_events = self.storage.get_focus_events_overlapping_range(
            start_time, end_time
        )
        focus_events = self._clip_focus_event_durations(focus_events, start_time, end_time)

        # Skip if there's nothing to summarize
        if not screenshots and not focus_events:
            logger.info("No screenshots or focus events in time range, skipping")
            return

        # Skip if focus time is below minimum threshold (avoid trivial summaries)
        total_focus_seconds = sum(e.get('duration_seconds', 0) or 0 for e in focus_events)
        min_focus_seconds = getattr(self.config.config.summarization, 'min_focus_seconds', 60)
        if total_focus_seconds < min_focus_seconds:
            logger.info(
                f"Skipping time range - only {total_focus_seconds:.0f}s of tracked focus "
                f"(minimum: {min_focus_seconds}s)"
            )
            return

        logger.info(
            f"Found {len(screenshots)} screenshots and {len(focus_events)} focus events "
            f"in time range"
        )

        # Get OCR texts for unique window titles (if screenshots available)
        ocr_texts = self._gather_ocr(screenshots) if screenshots else []

        # Get previous summary for context continuity
        previous_summary = None
        if self.config.config.summarization.include_previous_summary:
            last = self.storage.get_last_threshold_summary()
            if last:
                previous_summary = last.get('summary')

        # Generate summary
        try:
            summary, inference_ms, prompt_text, screenshot_ids_used, explanation, tags, confidence = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=previous_summary,
                focus_events=focus_events,
            )
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return

        # Build config snapshot
        cfg = self.config.config.summarization
        config_snapshot = {
            'model': cfg.model,
            'threshold': cfg.trigger_threshold,
            'include_focus_context': getattr(cfg, 'include_focus_context', True),
            'include_screenshots': getattr(cfg, 'include_screenshots', True),
            'include_ocr': getattr(cfg, 'include_ocr', True),
            'crop_to_window': cfg.crop_to_window,
            'include_previous_summary': cfg.include_previous_summary,
            'max_samples': cfg.max_samples,
            'sample_interval_minutes': cfg.sample_interval_minutes,
            'focus_weighted_sampling': cfg.focus_weighted_sampling,
            'frequency_minutes': cfg.frequency_minutes,
        }

        # Save to database (reuse ISO strings computed earlier for dedup check)
        summary_id = self.storage.save_threshold_summary(
            start_time=start_iso,
            end_time=end_iso,
            summary=summary,
            screenshot_ids=[s['id'] for s in screenshots],
            model=self.config.config.summarization.model,
            config_snapshot=config_snapshot,
            inference_ms=inference_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags,
            confidence=confidence,
        )

        logger.info(f"Saved summary {summary_id} (conf={confidence:.2f}): {summary[:80]}...")

    def _do_summarize_screenshots(self, screenshots: List[Dict]):
        """Generate summary for a batch of screenshots (legacy method).

        Used by force_summarize_pending for backward compatibility.

        Args:
            screenshots: List of screenshot dicts to summarize
        """
        if not screenshots:
            return

        # Sort by timestamp (ascending) for chronological narrative
        screenshots = sorted(screenshots, key=lambda s: s['timestamp'])

        logger.info(f"Summarizing batch of {len(screenshots)} screenshots...")

        # Check if summarizer is available
        if not self.summarizer.is_available():
            logger.error("Summarizer not available (check Ollama and Tesseract)")
            return

        # Gather focus events (app/window usage breakdown)
        focus_events = self._gather_focus_events(screenshots)

        # Get OCR texts for unique window titles
        ocr_texts = self._gather_ocr(screenshots)

        # Get previous summary for context continuity
        previous_summary = None
        if self.config.config.summarization.include_previous_summary:
            last = self.storage.get_last_threshold_summary()
            if last:
                previous_summary = last.get('summary')

        # Generate summary
        try:
            summary, inference_ms, prompt_text, screenshot_ids_used, explanation, tags, confidence = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=previous_summary,
                focus_events=focus_events,
            )
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return

        # Build config snapshot
        cfg = self.config.config.summarization
        config_snapshot = {
            'model': cfg.model,
            'threshold': cfg.trigger_threshold,
            'include_focus_context': getattr(cfg, 'include_focus_context', True),
            'include_screenshots': getattr(cfg, 'include_screenshots', True),
            'include_ocr': getattr(cfg, 'include_ocr', True),
            'crop_to_window': cfg.crop_to_window,
            'include_previous_summary': cfg.include_previous_summary,
            'max_samples': cfg.max_samples,
            'sample_interval_minutes': cfg.sample_interval_minutes,
            'focus_weighted_sampling': cfg.focus_weighted_sampling,
        }

        # Get timestamps
        first_ts = screenshots[0]['timestamp']
        last_ts = screenshots[-1]['timestamp']
        start_iso = datetime.fromtimestamp(first_ts).isoformat()
        end_iso = datetime.fromtimestamp(last_ts).isoformat()

        # Save to database
        summary_id = self.storage.save_threshold_summary(
            start_time=start_iso,
            end_time=end_iso,
            summary=summary,
            screenshot_ids=[s['id'] for s in screenshots],
            model=self.config.config.summarization.model,
            config_snapshot=config_snapshot,
            inference_ms=inference_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags,
            confidence=confidence,
        )

        logger.info(f"Saved summary {summary_id} (conf={confidence:.2f}): {summary[:80]}...")

    def _do_regenerate(self, summary_id: int):
        """Regenerate an existing summary with current settings.

        Args:
            summary_id: ID of the summary to regenerate
        """
        logger.info(f"Regenerating summary {summary_id}...")

        # Get the original summary
        old_summary = self.storage.get_threshold_summary(summary_id)
        if not old_summary:
            logger.error(f"Summary {summary_id} not found")
            return

        # Get the screenshots
        screenshot_ids = old_summary['screenshot_ids']
        screenshots = []
        for sid in screenshot_ids:
            s = self.storage.get_screenshot_by_id(sid)
            if s:
                screenshots.append(s)

        if not screenshots:
            logger.error(f"No screenshots found for summary {summary_id}")
            return

        # Check if summarizer is available
        if not self.summarizer.is_available():
            logger.error("Summarizer not available (check Ollama and Tesseract)")
            return

        # Use current config (may differ from original)
        ocr_texts = self._gather_ocr(screenshots)
        focus_events = self._gather_focus_events(screenshots)

        # Don't use previous summary for regeneration
        start_time = time.time()
        try:
            summary, inference_ms, prompt_text, _, explanation, tags, confidence = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=None,
                focus_events=focus_events,
            )
        except Exception as e:
            logger.error(f"Regeneration failed: {e}")
            return

        # Build config snapshot
        cfg = self.config.config.summarization
        config_snapshot = {
            'model': cfg.model,
            'threshold': cfg.trigger_threshold,
            'include_focus_context': getattr(cfg, 'include_focus_context', True),
            'include_screenshots': getattr(cfg, 'include_screenshots', True),
            'include_ocr': getattr(cfg, 'include_ocr', True),
            'crop_to_window': cfg.crop_to_window,
            'include_previous_summary': False,  # Not used for regeneration
            'max_samples': cfg.max_samples,
            'sample_interval_minutes': cfg.sample_interval_minutes,
            'focus_weighted_sampling': cfg.focus_weighted_sampling,
        }

        # Update existing summary in-place
        success = self.storage.update_threshold_summary(
            summary_id=summary_id,
            summary=summary,
            model=self.config.config.summarization.model,
            config_snapshot=config_snapshot,
            inference_ms=inference_ms,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags,
            confidence=confidence,
        )

        if success:
            logger.info(f"Regenerated summary {summary_id} (conf={confidence:.2f}): {summary[:100]}...")
        else:
            logger.error(f"Failed to update summary {summary_id} - not found")

    def _gather_ocr(self, screenshots: List[Dict]) -> List[Dict]:
        """Gather OCR texts for unique window titles.

        Args:
            screenshots: List of screenshot dicts

        Returns:
            List of dicts with window_title and ocr_text
        """
        # Skip OCR if not enabled in settings
        if not getattr(self.config.config.summarization, 'include_ocr', True):
            return []

        ocr_texts = []
        seen_titles = set()
        data_dir = Path(self.config.config.storage.data_dir).expanduser()

        for s in screenshots:
            title = s.get('window_title')
            if not title or title in seen_titles:
                continue

            seen_titles.add(title)

            try:
                # Get screenshot path
                filepath = data_dir / "screenshots" / s['filepath']

                # Use cropped version if available and enabled
                if self.config.config.summarization.crop_to_window:
                    cropped_path = self.summarizer.get_cropped_path(s)
                    if cropped_path and Path(cropped_path).exists():
                        filepath = cropped_path

                # Extract OCR
                ocr_text = self.summarizer.extract_ocr(str(filepath))
                ocr_texts.append({
                    'window_title': title,
                    'ocr_text': ocr_text,
                })
            except Exception as e:
                logger.debug(f"OCR failed for '{title}': {e}")

        return ocr_texts

    def _gather_focus_events(self, screenshots: List[Dict]) -> List[Dict]:
        """Gather focus events for the time range of screenshots.

        Gets all focus events that overlap with the screenshot time range
        and clips their durations to the actual range.

        Args:
            screenshots: List of screenshot dicts with timestamps

        Returns:
            List of focus event dicts with durations clipped to the query range
        """
        if not screenshots:
            return []

        try:
            # Get time range from screenshots
            timestamps = [s.get('timestamp', 0) for s in screenshots]
            start_ts = min(timestamps)
            end_ts = max(timestamps)

            # Convert to datetime for storage query
            start_dt = datetime.fromtimestamp(start_ts)
            end_dt = datetime.fromtimestamp(end_ts)

            # Get focus events that overlap with the range
            focus_events = self.storage.get_focus_events_overlapping_range(
                start_dt, end_dt
            )

            # Clip durations to the actual query range
            clipped_events = self._clip_focus_event_durations(
                focus_events, start_dt, end_dt
            )

            logger.info(f"Found {len(clipped_events)} focus events for time range "
                       f"({start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')})")
            return clipped_events

        except Exception as e:
            logger.warning(f"Failed to gather focus events: {e}")
            return []

    def _clip_focus_event_durations(
        self,
        events: List[Dict],
        range_start: datetime,
        range_end: datetime
    ) -> List[Dict]:
        """Clip focus event durations to the specified time range.

        For events that extend beyond the query range, adjusts duration_seconds
        to only count time within the range.

        Args:
            events: List of focus event dicts
            range_start: Start of the query range
            range_end: End of the query range

        Returns:
            List of events with clipped durations
        """
        clipped = []
        for event in events:
            event_copy = dict(event)

            # Parse event times
            event_start_str = event.get('start_time', '')
            event_end_str = event.get('end_time', '')

            try:
                # Handle ISO format with optional microseconds
                if 'T' in event_start_str:
                    event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                else:
                    event_start = datetime.strptime(event_start_str, '%Y-%m-%d %H:%M:%S')

                if event_end_str:
                    if 'T' in event_end_str:
                        event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                    else:
                        event_end = datetime.strptime(event_end_str, '%Y-%m-%d %H:%M:%S')
                else:
                    # Ongoing event - use range_end as the effective end
                    event_end = range_end

                # Remove timezone info for comparison if present
                if event_start.tzinfo:
                    event_start = event_start.replace(tzinfo=None)
                if event_end.tzinfo:
                    event_end = event_end.replace(tzinfo=None)

                # Calculate overlap
                overlap_start = max(event_start, range_start)
                overlap_end = min(event_end, range_end)

                if overlap_start < overlap_end:
                    # There is overlap - calculate clipped duration
                    clipped_duration = (overlap_end - overlap_start).total_seconds()
                    event_copy['duration_seconds'] = clipped_duration
                    clipped.append(event_copy)
                # If no overlap, skip the event

            except (ValueError, TypeError) as e:
                # If we can't parse times, include event with original duration
                logger.debug(f"Could not parse focus event times: {e}")
                clipped.append(event_copy)

        return clipped

    def _maybe_generate_preview(self, now: datetime):
        """Generate or update preview summary for active session if due.

        Called from the run loop. Checks if enough time has passed since
        the last preview and generates/updates the preview summary for
        the current active session.

        Args:
            now: Current datetime
        """
        # Only run if summarization is enabled
        if not self.config.config.summarization.enabled:
            return

        # Only if we have an active session
        if self._current_session_start is None:
            return

        cfg = self.config.config.summarization
        preview_interval = cfg.preview_interval_minutes
        min_duration = cfg.min_session_duration_seconds

        # Check if session has been active long enough to generate a preview
        session_duration = (now - self._current_session_start).total_seconds()
        if session_duration < min_duration:
            return

        # Check if it's time for a preview update
        if self._last_preview_time is not None:
            since_last = (now - self._last_preview_time).total_seconds()
            if since_last < preview_interval * 60:
                return
        else:
            # First preview - wait at least one interval from session start
            since_start = (now - self._current_session_start).total_seconds()
            if since_start < preview_interval * 60:
                return

        # Generate/update preview summary
        self._generate_preview_summary(now)

    def _generate_preview_summary(self, now: datetime):
        """Generate or update the preview summary for the current session.

        Creates a new preview summary or updates an existing one with the
        latest activity data from the current session.

        Args:
            now: Current datetime
        """
        if self._current_session_start is None:
            return

        self._current_task = 'preview'
        try:
            # Check if summarizer is available
            try:
                summarizer = self.summarizer
                if summarizer is None:
                    logger.error("Summarizer not available for preview")
                    return
            except Exception as e:
                logger.error(f"Failed to initialize summarizer for preview: {e}")
                return

            start_time = self._current_session_start
            end_time = now

            logger.info(
                f"Generating preview summary: "
                f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
            )

            # Get screenshots in the time range
            screenshots = self.storage.get_screenshots_in_range(
                start_time,
                end_time
            )

            if not screenshots:
                logger.debug("No screenshots for preview summary")
                return

            # Get focus events for context
            focus_events = self.storage.get_focus_events_in_range(
                start_time,
                end_time
            )

            logger.info(
                f"Preview: Found {len(screenshots)} screenshots and "
                f"{len(focus_events)} focus events"
            )

            # Gather OCR texts for unique window titles
            ocr_texts = self._gather_ocr(screenshots)

            # Generate summary
            cfg = self.config.config.summarization
            try:
                summary_text, inference_ms, prompt_text, screenshot_ids, explanation, tags, confidence = summarizer.summarize_session(
                    screenshots=screenshots,
                    ocr_texts=ocr_texts,
                    previous_summary=None,  # No context for previews
                    focus_events=focus_events,
                )
            except Exception as e:
                logger.error(f"Preview summarization failed: {e}")
                return

            # Check for existing preview to update
            existing_preview = self.storage.get_current_preview_summary()

            config_snapshot = {
                "model": cfg.model,
                "max_samples": cfg.max_samples,
                "include_focus_context": cfg.include_focus_context,
                "include_screenshots": cfg.include_screenshots,
                "include_ocr": cfg.include_ocr,
            }

            if existing_preview:
                # Update existing preview
                self.storage.update_preview_summary(
                    summary_id=existing_preview['id'],
                    end_time=end_time.isoformat(),
                    summary=summary_text,
                    screenshot_ids=screenshot_ids,
                    model=cfg.model,
                    config_snapshot=config_snapshot,
                    inference_ms=inference_ms,
                    explanation=explanation,
                    tags=tags,
                    confidence=confidence,
                )
                logger.info(
                    f"Updated preview summary {existing_preview['id']} "
                    f"(conf={confidence:.2f}): {summary_text[:60]}..."
                )
            else:
                # Create new preview
                summary_id = self.storage.save_threshold_summary(
                    start_time=start_time.isoformat(),
                    end_time=end_time.isoformat(),
                    summary=summary_text,
                    screenshot_ids=screenshot_ids,
                    model=cfg.model,
                    config_snapshot=config_snapshot,
                    inference_ms=inference_ms,
                    explanation=explanation,
                    tags=tags,
                    confidence=confidence,
                    is_preview=True,
                )
                logger.info(
                    f"Created preview summary {summary_id} "
                    f"(conf={confidence:.2f}): {summary_text[:60]}..."
                )

            self._last_preview_time = now

        except Exception as e:
            logger.error(f"Preview generation failed: {e}", exc_info=True)
        finally:
            self._current_task = None

    def _maybe_generate_daily_reports(self, now: datetime):
        """Generate daily report for yesterday if not already generated.

        Called from the run loop. Checks if we've crossed midnight and
        if so, generates a cached daily report for the previous day.

        Args:
            now: Current datetime
        """
        # Only run if summarization is enabled (reuse the same config flag)
        if not self.config.config.summarization.enabled:
            return

        # Get yesterday's date string
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Skip if we already generated today's daily report
        if self._last_daily_report_date == yesterday:
            return

        # Check if we have a cached report for yesterday already
        existing = self.storage.get_cached_report('daily', yesterday)
        if existing:
            # Already have the report, just update tracking
            self._last_daily_report_date = yesterday
            return

        # Generate the daily report for yesterday
        logger.info(f"Generating daily report for {yesterday}")
        self._current_task = 'daily_report'

        try:
            from .reports import ReportGenerator
            generator = ReportGenerator(self.storage, self.summarizer, self.config)
            result = generator.generate_daily_report(yesterday)

            if result:
                logger.info(f"Generated daily report for {yesterday}: {result.get('executive_summary', '')[:80]}...")
            else:
                logger.info(f"No activity found for {yesterday}, skipping daily report")

            # Mark as done for today
            self._last_daily_report_date = yesterday

        except Exception as e:
            logger.error(f"Failed to generate daily report for {yesterday}: {e}", exc_info=True)
        finally:
            self._current_task = None

    def _maybe_generate_weekly_reports(self, now: datetime):
        """Generate weekly report for last week if not already generated.

        Called from the run loop. Checks if we're on Sunday and if so,
        generates a cached weekly report for the previous week.

        Args:
            now: Current datetime
        """
        # Only run if summarization is enabled
        if not self.config.config.summarization.enabled:
            return

        # Only generate on Sunday after 00:05
        if now.weekday() != 6 or now.hour == 0 and now.minute < 5:
            return

        # Get last week's ISO week string
        last_week = now - timedelta(days=7)
        iso_year, iso_week, _ = last_week.isocalendar()
        week_str = f"{iso_year}-W{iso_week:02d}"

        # Skip if we already generated this week's report
        if self._last_weekly_report_week == week_str:
            return

        # Check if we have a cached report already
        existing = self.storage.get_cached_report('weekly', week_str)
        if existing:
            self._last_weekly_report_week = week_str
            return

        # Generate the weekly report
        logger.info(f"Generating weekly report for {week_str}")
        self._current_task = 'weekly_report'

        try:
            from .reports import ReportGenerator
            generator = ReportGenerator(self.storage, self.summarizer, self.config)
            result = generator.generate_weekly_report(week_str)

            if result:
                logger.info(f"Generated weekly report for {week_str}")
            else:
                logger.info(f"No data for {week_str}, skipping weekly report")

            self._last_weekly_report_week = week_str

        except Exception as e:
            logger.error(f"Failed to generate weekly report for {week_str}: {e}", exc_info=True)
        finally:
            self._current_task = None

    def _maybe_generate_monthly_reports(self, now: datetime):
        """Generate monthly report for last month if not already generated.

        Called from the run loop. Checks if we're on the 1st of the month
        and if so, generates a cached monthly report for the previous month.

        Args:
            now: Current datetime
        """
        # Only run if summarization is enabled
        if not self.config.config.summarization.enabled:
            return

        # Only generate on the 1st after 00:10
        if now.day != 1 or now.hour == 0 and now.minute < 10:
            return

        # Get last month's string
        last_month = now.replace(day=1) - timedelta(days=1)
        month_str = last_month.strftime('%Y-%m')

        # Skip if we already generated this month's report
        if self._last_monthly_report_month == month_str:
            return

        # Check if we have a cached report already
        existing = self.storage.get_cached_report('monthly', month_str)
        if existing:
            self._last_monthly_report_month = month_str
            return

        # Generate the monthly report
        logger.info(f"Generating monthly report for {month_str}")
        self._current_task = 'monthly_report'

        try:
            from .reports import ReportGenerator
            generator = ReportGenerator(self.storage, self.summarizer, self.config)
            result = generator.generate_monthly_report(month_str)

            if result:
                logger.info(f"Generated monthly report for {month_str}")
            else:
                logger.info(f"No data for {month_str}, skipping monthly report")

            self._last_monthly_report_month = month_str

        except Exception as e:
            logger.error(f"Failed to generate monthly report for {month_str}: {e}", exc_info=True)
        finally:
            self._current_task = None

    def _do_startup_backfill(self):
        """Generate missing historical reports on startup.

        Backfills missing daily, weekly, and monthly reports for recent periods.
        This runs once when the worker starts.
        """
        if not self.config.config.summarization.enabled:
            return

        logger.info("Running startup backfill for missing reports...")
        self._current_task = 'backfill'

        try:
            from .reports import ReportGenerator
            generator = ReportGenerator(self.storage, self.summarizer, self.config)

            # Backfill daily reports for last 7 days
            daily_count = generator.generate_missing_daily_reports(days_back=7)
            if daily_count > 0:
                logger.info(f"Backfilled {daily_count} daily reports")

            # Backfill weekly reports for last 4 weeks
            weekly_count = generator.generate_missing_weekly_reports(weeks_back=4)
            if weekly_count > 0:
                logger.info(f"Backfilled {weekly_count} weekly reports")

            # Backfill monthly reports for last 3 months
            monthly_count = generator.generate_missing_monthly_reports(months_back=3)
            if monthly_count > 0:
                logger.info(f"Backfilled {monthly_count} monthly reports")

            total = daily_count + weekly_count + monthly_count
            if total > 0:
                logger.info(f"Startup backfill complete: {total} reports generated")
            else:
                logger.info("Startup backfill complete: no missing reports")

        except Exception as e:
            logger.error(f"Startup backfill failed: {e}", exc_info=True)
        finally:
            self._current_task = None

    def queue_regenerate_report(self, period_type: str, period_date: str):
        """Queue a hierarchical report for regeneration.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'
            period_date: Period identifier (e.g., '2024-12-30', '2024-W52', '2024-12')
        """
        self._pending_queue.put(('regenerate_report', (period_type, period_date)))
        logger.info(f"Queued {period_type} report {period_date} for regeneration")

    def _do_regenerate_report(self, period_type: str, period_date: str):
        """Regenerate a hierarchical report with current settings.

        Args:
            period_type: 'daily', 'weekly', or 'monthly'
            period_date: Period identifier
        """
        logger.info(f"Regenerating {period_type} report for {period_date}...")

        try:
            from .reports import ReportGenerator
            generator = ReportGenerator(self.storage, self.summarizer, self.config)

            if period_type == 'daily':
                result = generator.generate_daily_report(period_date, is_regeneration=True)
            elif period_type == 'weekly':
                result = generator.generate_weekly_report(period_date, is_regeneration=True)
            elif period_type == 'monthly':
                result = generator.generate_monthly_report(period_date, is_regeneration=True)
            else:
                logger.error(f"Unknown period type: {period_type}")
                return

            if result:
                logger.info(f"Regenerated {period_type} report for {period_date}")
            else:
                logger.warning(f"No data available to regenerate {period_type} report for {period_date}")

        except Exception as e:
            logger.error(f"Failed to regenerate {period_type} report for {period_date}: {e}", exc_info=True)
