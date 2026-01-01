"""Background summarization worker with cron-like scheduling.

This module implements a background worker that triggers summarization at
fixed time intervals (like cron). For example, with frequency_minutes=15,
summaries run at hh:00, hh:15, hh:30, hh:45.

The worker supports:
- Cron-like scheduled summarization (at fixed clock times)
- Time-range based summarization (not dependent on screenshots)
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
    """Background worker with cron-like scheduled summarization.

    Triggers summarization at fixed time intervals based on frequency_minutes.
    For example, with frequency_minutes=15, runs at hh:00, hh:15, hh:30, hh:45.

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
        self._pending_queue: queue.Queue = queue.Queue()  # For regenerate/force tasks
        self._current_task: Optional[str] = None
        self._next_scheduled_run: Optional[datetime] = None
        self._last_summarized_end: Optional[datetime] = None  # Track last summarized period
        self._last_daily_report_date: Optional[str] = None  # Track date of last daily report

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

        # Initialize scheduling
        self._next_scheduled_run = self._get_next_scheduled_time()
        self._last_summarized_end = self._find_last_summarized_time()

        frequency_minutes = self.config.config.summarization.frequency_minutes
        logger.info(
            f"SummarizerWorker starting with {frequency_minutes}min intervals. "
            f"Next run at {self._next_scheduled_run.strftime('%H:%M')}"
        )

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

    def get_status(self) -> Dict:
        """Get current worker status.

        Returns:
            Dict with running state, current task, queue size, and next scheduled run.
        """
        return {
            "running": self._running,
            "current_task": self._current_task,
            "queue_size": self._pending_queue.qsize(),
            "next_scheduled_run": (
                self._next_scheduled_run.isoformat()
                if self._next_scheduled_run else None
            ),
        }

    def _run_loop(self):
        """Background loop with cron-like scheduling.

        Runs summarization at fixed clock times based on frequency_minutes.
        Also processes manual tasks from the queue (regenerate, force).
        Additionally generates daily reports at midnight.
        """
        logger.info("SummarizerWorker run loop started")

        while self._running:
            now = datetime.now()

            # Check if we should generate daily reports (at/after midnight)
            self._maybe_generate_daily_reports(now)

            # Check if it's time for scheduled summarization
            if (self._next_scheduled_run and
                now >= self._next_scheduled_run and
                self.config.config.summarization.enabled):

                slot_time = self._next_scheduled_run
                start_time, end_time = self._get_time_range_for_slot(slot_time)

                logger.info(
                    f"Scheduled summarization triggered at {now.strftime('%H:%M:%S')} "
                    f"for slot {start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
                )

                self._current_task = 'scheduled_summarize'
                try:
                    self._do_summarize_time_range(start_time, end_time)
                except Exception as e:
                    logger.error(f"Scheduled summarization failed: {e}", exc_info=True)
                finally:
                    self._current_task = None

                # Schedule next run
                self._next_scheduled_run = self._get_next_scheduled_time()
                logger.info(f"Next scheduled run at {self._next_scheduled_run.strftime('%H:%M')}")

            # Process manual tasks from queue (regenerate, force_summarize)
            try:
                task = self._pending_queue.get(timeout=1)
                task_type, payload = task
                self._current_task = task_type

                try:
                    if task_type == 'summarize_range':
                        # Force summarize with time range
                        start_time, end_time = payload
                        self._do_summarize_time_range(start_time, end_time)
                    elif task_type == 'summarize':
                        # Legacy: summarize screenshots list (for force_summarize_pending)
                        self._do_summarize_screenshots(payload)
                    elif task_type == 'regenerate':
                        self._do_regenerate(payload)
                except Exception as e:
                    logger.error(f"Summarization task failed: {e}", exc_info=True)
                finally:
                    self._current_task = None
            except queue.Empty:
                # No manual tasks, continue to next iteration
                pass

        logger.info("SummarizerWorker run loop stopped")

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

        # Get focus events for the time range (exclude AFK periods with NULL session_id)
        focus_events = self.storage.get_focus_events_overlapping_range(
            start_time, end_time, require_session=True
        )
        focus_events = self._clip_focus_event_durations(focus_events, start_time, end_time)

        # Skip if there's nothing to summarize
        if not screenshots and not focus_events:
            logger.info("No screenshots or focus events in time range, skipping")
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

        # Find the original root ID (in case this is already a regeneration)
        root_id = summary_id
        if old_summary.get('regenerated_from'):
            root_id = old_summary['regenerated_from']

        # Save as new entry linked to original
        new_id = self.storage.save_threshold_summary(
            start_time=old_summary['start_time'],
            end_time=old_summary['end_time'],
            summary=summary,
            screenshot_ids=screenshot_ids,
            model=self.config.config.summarization.model,
            config_snapshot=config_snapshot,
            inference_ms=inference_ms,
            regenerated_from=root_id,
            prompt_text=prompt_text,
            explanation=explanation,
            tags=tags,
            confidence=confidence,
        )

        logger.info(f"Regenerated summary {summary_id} -> {new_id} (conf={confidence:.2f}): {summary[:100]}...")

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

            # Get focus events that overlap with the range (exclude AFK periods)
            focus_events = self.storage.get_focus_events_overlapping_range(
                start_dt, end_dt, require_session=True
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
