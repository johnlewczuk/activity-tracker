"""Background summarization worker for threshold-based summaries.

This module implements a background worker that monitors screenshot count
and triggers summarization when the threshold is reached. Summaries are
generated asynchronously without blocking the main capture loop.

The worker supports:
- Automatic threshold-based summarization
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
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import ActivityStorage
    from .config import ConfigManager

logger = logging.getLogger(__name__)


class SummarizerWorker:
    """Background worker for threshold-based summarization.

    Monitors the screenshot count and triggers summarization when the
    configured threshold is reached. Runs in a background thread.

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
        self._pending_queue: queue.Queue = queue.Queue()
        self._current_task: Optional[str] = None
        self._last_check_count = 0

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
                # Use summarization_mode if it's not the default
                # (i.e., user explicitly set it to something other than ocr_and_screenshots)
                summarization_mode=cfg.summarization_mode if cfg.summarization_mode != "ocr_and_screenshots" else None,
                # New content mode flags (only used if summarization_mode is None/default)
                include_focus_context=getattr(cfg, 'include_focus_context', True),
                include_screenshots=getattr(cfg, 'include_screenshots', True),
                include_ocr=getattr(cfg, 'include_ocr', True),
            )
            self._summarizer_model = current_model
        return self._summarizer

    def start(self):
        """Start the background worker thread."""
        if self._running:
            logger.warning("SummarizerWorker already running")
            return

        self._running = True
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
        """Check if enough time has passed and queue summarization.

        Called after each screenshot capture to check if frequency_minutes
        has elapsed since the last summary. This is duration-based triggering
        rather than count-based.
        """
        if not self.config.config.summarization.enabled:
            return

        try:
            frequency_minutes = self.config.config.summarization.frequency_minutes

            # Get the last summary to check when summarization job last ran
            last_summary = self.storage.get_last_threshold_summary()

            if last_summary:
                # Use created_at (when job ran), not end_time (screenshot timestamp)
                created_at_str = last_summary.get('created_at', '')
                try:
                    if 'T' in created_at_str:
                        last_run = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                        if last_run.tzinfo:
                            last_run = last_run.replace(tzinfo=None)
                    else:
                        last_run = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    last_run = None

                if last_run:
                    elapsed = datetime.now() - last_run
                    elapsed_minutes = elapsed.total_seconds() / 60

                    if elapsed_minutes < frequency_minutes:
                        # Not enough time since last summarization job
                        return

            # Either no previous summary or enough time has passed
            unsummarized = self.storage.get_unsummarized_screenshots()

            if len(unsummarized) >= 1:
                # Queue all unsummarized screenshots
                self._pending_queue.put(('summarize', unsummarized))
                logger.info(
                    f"Queued {len(unsummarized)} screenshots for summarization "
                    f"(frequency: {frequency_minutes}min)"
                )
        except Exception as e:
            logger.error(f"Error checking summarization trigger: {e}")

    def queue_regenerate(self, summary_id: int):
        """Queue a summary for regeneration.

        Args:
            summary_id: ID of the summary to regenerate
        """
        self._pending_queue.put(('regenerate', summary_id))
        logger.info(f"Queued summary {summary_id} for regeneration")

    def force_summarize_pending(self) -> int:
        """Force immediate summarization of all pending screenshots.

        Splits screenshots into time-based batches based on frequency_minutes
        setting, so each summary covers approximately that time period.

        Returns:
            Number of screenshots queued for summarization.
        """
        unsummarized = self.storage.get_unsummarized_screenshots()
        if not unsummarized:
            logger.info("No unsummarized screenshots to process")
            return 0

        # Sort by timestamp
        unsummarized = sorted(unsummarized, key=lambda s: s['timestamp'])

        # Split into time-based batches using frequency_minutes
        frequency_minutes = self.config.config.summarization.frequency_minutes
        frequency_seconds = frequency_minutes * 60

        batches = []
        current_batch = []
        batch_start_ts = None

        for screenshot in unsummarized:
            ts = screenshot['timestamp']

            if batch_start_ts is None:
                batch_start_ts = ts
                current_batch = [screenshot]
            elif ts - batch_start_ts < frequency_seconds:
                # Still within the time window
                current_batch.append(screenshot)
            else:
                # Start a new batch
                if current_batch:
                    batches.append(current_batch)
                batch_start_ts = ts
                current_batch = [screenshot]

        # Don't forget the last batch
        if current_batch:
            batches.append(current_batch)

        # Queue each batch separately
        for batch in batches:
            self._pending_queue.put(('summarize', batch))

        logger.info(
            f"Force-queued {len(unsummarized)} screenshots in {len(batches)} batches "
            f"({frequency_minutes}min intervals)"
        )
        return len(unsummarized)

    def get_status(self) -> Dict:
        """Get current worker status.

        Returns:
            Dict with running state, current task, and queue size.
        """
        return {
            "running": self._running,
            "current_task": self._current_task,
            "queue_size": self._pending_queue.qsize(),
        }

    def _run_loop(self):
        """Background loop processing summarization queue."""
        logger.info("SummarizerWorker run loop started")

        while self._running:
            try:
                task = self._pending_queue.get(timeout=1)
            except queue.Empty:
                continue

            task_type, payload = task
            self._current_task = task_type

            try:
                if task_type == 'summarize':
                    self._do_summarize(payload)
                elif task_type == 'regenerate':
                    self._do_regenerate(payload)
            except Exception as e:
                logger.error(f"Summarization task failed: {e}", exc_info=True)
            finally:
                self._current_task = None

        logger.info("SummarizerWorker run loop stopped")

    def _do_summarize(self, screenshots: List[Dict]):
        """Generate summary for a batch of screenshots.

        Sends all screenshots to the LLM along with focus events that show
        app/window usage breakdown. The LLM interprets the activity based on
        the actual time spent per app/window.

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
            summary, inference_ms, prompt_text, screenshot_ids_used = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=previous_summary,
                focus_events=focus_events,
            )
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return

        # Build config snapshot
        config_snapshot = {
            'model': self.config.config.summarization.model,
            'threshold': self.config.config.summarization.trigger_threshold,
            'summarization_mode': self.config.config.summarization.summarization_mode,
            'crop_to_window': self.config.config.summarization.crop_to_window,
            'include_previous_summary': self.config.config.summarization.include_previous_summary,
            'max_samples': self.config.config.summarization.max_samples,
            'sample_interval_minutes': self.config.config.summarization.sample_interval_minutes,
            'focus_weighted_sampling': self.config.config.summarization.focus_weighted_sampling,
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
        )

        logger.info(f"Saved summary {summary_id}: {summary[:80]}...")

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
            summary, inference_ms, prompt_text, _ = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=None,
                focus_events=focus_events,
            )
        except Exception as e:
            logger.error(f"Regeneration failed: {e}")
            return

        # Build config snapshot
        config_snapshot = {
            'model': self.config.config.summarization.model,
            'threshold': self.config.config.summarization.trigger_threshold,
            'summarization_mode': self.config.config.summarization.summarization_mode,
            'crop_to_window': self.config.config.summarization.crop_to_window,
            'include_previous_summary': False,  # Not used for regeneration
            'max_samples': self.config.config.summarization.max_samples,
            'sample_interval_minutes': self.config.config.summarization.sample_interval_minutes,
            'focus_weighted_sampling': self.config.config.summarization.focus_weighted_sampling,
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
        )

        logger.info(f"Regenerated summary {summary_id} -> {new_id}: {summary[:100]}...")

    def _gather_ocr(self, screenshots: List[Dict]) -> List[Dict]:
        """Gather OCR texts for unique window titles.

        Args:
            screenshots: List of screenshot dicts

        Returns:
            List of dicts with window_title and ocr_text
        """
        mode = self.config.config.summarization.summarization_mode
        if mode == "screenshots_only":
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
            focus_events = self.storage.get_focus_events_overlapping_range(start_dt, end_dt)

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
