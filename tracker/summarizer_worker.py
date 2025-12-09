"""Background summarization worker for threshold-based summaries.

This module implements a background worker that monitors screenshot count
and triggers summarization when the threshold is reached. Summaries are
generated asynchronously without blocking the main capture loop.

The worker supports:
- Automatic threshold-based summarization
- Regeneration of existing summaries with new settings
- Context continuity via previous summary inclusion
- OCR extraction for unique window titles
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

        # Recreate summarizer if model or host changed
        if self._summarizer is None or self._summarizer_model != current_model:
            from .vision import HybridSummarizer
            logger.info(f"Creating summarizer with model: {current_model}")
            self._summarizer = HybridSummarizer(
                model=current_model,
                ollama_host=current_host,
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
        """Check if threshold reached and queue summarization.

        Called after each screenshot capture to check if the number of
        unsummarized screenshots has reached the trigger threshold.
        """
        if not self.config.config.summarization.enabled:
            return

        try:
            unsummarized = self.storage.get_unsummarized_screenshots()
            threshold = self.config.config.summarization.trigger_threshold

            if len(unsummarized) >= threshold:
                # Queue the batch for summarization
                batch = unsummarized[:threshold]
                self._pending_queue.put(('summarize', batch))
                logger.info(
                    f"Queued {len(batch)} screenshots for summarization "
                    f"(threshold: {threshold})"
                )
        except Exception as e:
            logger.error(f"Error checking summarization threshold: {e}")

    def queue_regenerate(self, summary_id: int):
        """Queue a summary for regeneration.

        Args:
            summary_id: ID of the summary to regenerate
        """
        self._pending_queue.put(('regenerate', summary_id))
        logger.info(f"Queued summary {summary_id} for regeneration")

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

        Args:
            screenshots: List of screenshot dicts to summarize
        """
        if not screenshots:
            return

        logger.info(f"Summarizing batch of {len(screenshots)} screenshots...")

        # Check if summarizer is available
        if not self.summarizer.is_available():
            logger.error("Summarizer not available (check Ollama and Tesseract)")
            return

        # Get OCR texts for unique window titles
        ocr_texts = self._gather_ocr(screenshots)

        # Get previous summary for context
        previous_summary = None
        if self.config.config.summarization.include_previous_summary:
            last = self.storage.get_last_threshold_summary()
            if last:
                previous_summary = last.get('summary')

        # Generate summary
        start_time = time.time()
        try:
            summary, inference_ms, prompt_text, screenshot_ids_used = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=previous_summary,
            )
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return

        # Build config snapshot
        config_snapshot = {
            'model': self.config.config.summarization.model,
            'threshold': self.config.config.summarization.trigger_threshold,
            'ocr_enabled': self.config.config.summarization.ocr_enabled,
            'crop_to_window': self.config.config.summarization.crop_to_window,
            'include_previous_summary': self.config.config.summarization.include_previous_summary,
        }

        # Get timestamps from screenshots
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
        )

        logger.info(f"Saved summary {summary_id}: {summary[:100]}...")

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

        # Don't use previous summary for regeneration
        start_time = time.time()
        try:
            summary, inference_ms, prompt_text, _ = self.summarizer.summarize_session(
                screenshots=screenshots,
                ocr_texts=ocr_texts,
                previous_summary=None,
            )
        except Exception as e:
            logger.error(f"Regeneration failed: {e}")
            return

        # Build config snapshot
        config_snapshot = {
            'model': self.config.config.summarization.model,
            'threshold': self.config.config.summarization.trigger_threshold,
            'ocr_enabled': self.config.config.summarization.ocr_enabled,
            'crop_to_window': self.config.config.summarization.crop_to_window,
            'include_previous_summary': False,  # Not used for regeneration
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
        )

        logger.info(f"Regenerated summary {summary_id} -> {new_id}: {summary[:100]}...")

    def _gather_ocr(self, screenshots: List[Dict]) -> List[Dict]:
        """Gather OCR texts for unique window titles.

        Args:
            screenshots: List of screenshot dicts

        Returns:
            List of dicts with window_title and ocr_text
        """
        if not self.config.config.summarization.ocr_enabled:
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
