"""
Vision-based activity summarization using OCR and LLM.

This module provides hybrid summarization capabilities by combining
Tesseract OCR for text extraction with Ollama vision LLMs for
contextual understanding of developer activity.

Uses Ollama via HTTP API for Docker container compatibility.
"""

import base64
import io
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _install_package(package: str) -> bool:
    """Attempt to install a package using pip.

    Args:
        package: Name of the package to install.

    Returns:
        True if installation succeeded, False otherwise.
    """
    try:
        logger.info(f"Auto-installing missing dependency: {package}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install {package}: {e}")
        return False


# Auto-install requests if missing
try:
    import requests
except ImportError:
    logger.warning("requests not found, attempting auto-install...")
    if _install_package("requests"):
        import requests
        logger.info("requests installed successfully")
    else:
        raise ImportError("Failed to install requests - vision module unavailable")

# Auto-install Pillow if missing
try:
    from PIL import Image
except ImportError:
    logger.warning("Pillow not found, attempting auto-install...")
    if _install_package("Pillow"):
        from PIL import Image
        logger.info("Pillow installed successfully")
    else:
        raise ImportError("Failed to install Pillow - vision module unavailable")

# Default Ollama Docker container URL
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


class HybridSummarizer:
    """
    Combines OCR and vision LLM for activity summarization.

    Uses Tesseract for OCR text extraction and Ollama with a vision-capable
    model to generate contextual summaries from screenshots.

    Connects to Ollama via HTTP API for Docker container compatibility.

    Attributes:
        model: The Ollama model to use for vision inference.
        timeout: Timeout in seconds for LLM calls.
        ollama_host: Base URL for Ollama API (default: http://localhost:11434).
        max_samples: Maximum screenshots to send to LLM per session.
    """

    def __init__(
        self,
        model: str = "gemma3:12b-it-qat",
        timeout: int = 120,
        ollama_host: str = None,
        max_samples: int = 10,
        sample_interval_minutes: int = 10,
        focus_weighted_sampling: bool = True,
        # New content mode flags (multi-select)
        include_focus_context: bool = True,
        include_screenshots: bool = True,
        include_ocr: bool = True,
        # Legacy (backward compatibility)
        summarization_mode: str = None,
    ):
        """
        Initialize the HybridSummarizer.

        Args:
            model: Ollama model name to use for vision inference.
            timeout: Timeout in seconds for LLM calls.
            ollama_host: Base URL for Ollama API. Defaults to http://localhost:11434.
            max_samples: Maximum screenshots to send to LLM (default 10).
            sample_interval_minutes: Target interval between samples (default 10).
            focus_weighted_sampling: Weight sampling by focus duration (default True).
            include_focus_context: Include window titles and duration info (default True).
            include_screenshots: Include screenshot images (default True).
            include_ocr: Include OCR text extraction (default True).
            summarization_mode: Deprecated - use include_* flags instead.
        """
        self.model = model
        self.timeout = timeout
        self.ollama_host = ollama_host or DEFAULT_OLLAMA_HOST
        self.max_samples = max_samples
        self.sample_interval_minutes = sample_interval_minutes
        self.focus_weighted_sampling = focus_weighted_sampling

        # Handle legacy summarization_mode for backward compatibility
        if summarization_mode:
            self.include_screenshots = summarization_mode != "ocr_only"
            self.include_ocr = summarization_mode != "screenshots_only"
            self.include_focus_context = True  # Always include focus context in legacy mode
        else:
            self.include_focus_context = include_focus_context
            self.include_screenshots = include_screenshots
            self.include_ocr = include_ocr

    def _call_ollama_api(
        self,
        prompt: str,
        images: list[str] = None,
    ) -> str:
        """
        Call Ollama API via HTTP (Docker-compatible).

        Args:
            prompt: The text prompt to send.
            images: Optional list of base64-encoded images.

        Returns:
            The model's response text.

        Raises:
            RuntimeError: If the API call fails.
        """
        url = f"{self.ollama_host}/api/chat"

        message = {"role": "user", "content": prompt}
        if images:
            message["images"] = images

        payload = {
            "model": self.model,
            "messages": [message],
            "stream": False,
            "keep_alive": "1h",  # Keep model loaded for 1 hour for faster responses
        }

        start_time = time.time()
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()

            inference_time = time.time() - start_time
            logger.info(f"LLM inference completed in {inference_time:.2f}s")

            result = response.json()
            return result["message"]["content"]

        except requests.exceptions.Timeout:
            inference_time = time.time() - start_time
            logger.error(f"Ollama API timed out after {inference_time:.2f}s")
            raise RuntimeError(f"Ollama API timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Cannot connect to Ollama at {self.ollama_host}: {e}")
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.ollama_host}. "
                "Ensure the Ollama Docker container is running."
            ) from e
        except requests.exceptions.HTTPError as e:
            logger.error(f"Ollama API error: {e}")
            raise RuntimeError(f"Ollama API error: {e}") from e
        except Exception as e:
            inference_time = time.time() - start_time
            logger.error(f"LLM inference failed after {inference_time:.2f}s: {e}")
            raise RuntimeError(f"Ollama inference failed: {e}") from e

    def summarize_hour(self, screenshot_paths: list[str]) -> str:
        """
        Summarize developer activity from a set of screenshots.

        Takes 4-6 screenshot paths, extracts OCR from the middle screenshot
        for grounding, and uses a vision LLM to generate a summary.

        Args:
            screenshot_paths: List of 4-6 screenshot file paths.

        Returns:
            A 2-3 sentence summary of the developer activity.

        Raises:
            ValueError: If screenshot_paths is empty.
            RuntimeError: If Ollama is unavailable or the model fails.
        """
        if not screenshot_paths:
            raise ValueError("screenshot_paths cannot be empty")

        # Pick middle screenshot for OCR grounding
        middle_idx = len(screenshot_paths) // 2
        middle_path = screenshot_paths[middle_idx]

        # Extract OCR text (proceed without it if it fails)
        ocr_text = self._extract_ocr(middle_path)
        if ocr_text:
            logger.debug(f"Extracted OCR text ({len(ocr_text)} chars) from {middle_path}")
        else:
            logger.warning(f"OCR extraction failed or returned empty for {middle_path}")

        # Prepare all images for LLM
        images_base64 = []
        for path in screenshot_paths:
            try:
                img_b64 = self._prepare_image(path)
                images_base64.append(img_b64)
            except Exception as e:
                logger.warning(f"Failed to prepare image {path}: {e}")

        if not images_base64:
            raise RuntimeError("Failed to prepare any images for LLM")

        # Build prompt
        if ocr_text:
            prompt = (
                f"Summarize the developer activity across these screenshots in 2-3 sentences. "
                f"Here's OCR text from one screenshot for context: {ocr_text}"
            )
        else:
            prompt = "Summarize the developer activity across these screenshots in 2-3 sentences."

        return self._call_ollama_api(prompt, images_base64)

    def summarize_session(
        self,
        screenshots: list[dict],
        ocr_texts: list[dict],
        previous_summary: str = None,
        focus_events: list[dict] = None,
    ) -> tuple[str, int, str, list, str, float]:
        """
        Summarize a session with context continuity and focus tracking data.

        Takes screenshots from a session, OCR text from unique windows,
        optionally the previous session's summary for context, and focus
        events for time-based activity breakdown.

        Args:
            screenshots: List of dicts with {id, filepath, window_title, timestamp}.
            ocr_texts: List of dicts with {window_title, ocr_text}.
            previous_summary: Optional previous session summary for continuity.
            focus_events: Optional list of focus events from storage.get_focus_events_in_range().

        Returns:
            Tuple of (summary text, inference time in ms, prompt text, screenshot IDs used,
            explanation, confidence).

        Raises:
            ValueError: If screenshots is empty.
            RuntimeError: If Ollama is unavailable or the model fails.
        """
        if not screenshots:
            raise ValueError("screenshots cannot be empty")

        start_time = time.time()

        # Sample screenshots (focus-weighted if enabled and focus data available)
        if self.focus_weighted_sampling and focus_events:
            sampled = self._sample_screenshots_weighted(
                screenshots, focus_events, self.max_samples, self.sample_interval_minutes
            )
            logger.info(f"Focus-weighted sampled {len(sampled)} of {len(screenshots)} screenshots")
        else:
            sampled = self._sample_screenshots_uniform(
                screenshots, self.max_samples, self.sample_interval_minutes
            )
            logger.info(f"Uniformly sampled {len(sampled)} of {len(screenshots)} screenshots")

        # Extract IDs of screenshots actually used
        screenshot_ids_used = [s["id"] for s in sampled]

        # Prepare images for LLM (use cropped versions for better focus)
        images_base64 = []
        if self.include_screenshots:
            for s in sampled:
                try:
                    # Get cropped version (falls back to full if no geometry)
                    img_path = self._get_cropped_screenshot(s)
                    img_b64 = self._prepare_image(img_path)
                    images_base64.append(img_b64)
                except Exception as e:
                    logger.warning(f"Failed to prepare image {s['filepath']}: {e}")

            if not images_base64:
                logger.warning("Failed to prepare any images for LLM")

        # Format OCR texts
        ocr_section = ""
        if self.include_ocr and ocr_texts:
            ocr_lines = []
            for item in ocr_texts:
                title = item.get("window_title", "Unknown")
                text = item.get("ocr_text", "")
                if text:
                    # Truncate long OCR text
                    text_preview = text[:500] + "..." if len(text) > 500 else text
                    ocr_lines.append(f"[{title}]: {text_preview}")
            if ocr_lines:
                ocr_section = "\n".join(ocr_lines)

        # Build focus context from events (if enabled)
        focus_context = ""
        if self.include_focus_context and focus_events:
            focus_context = self._build_focus_context(focus_events)

        # Ensure we have something to send (images, OCR, or focus context)
        if not images_base64 and not ocr_section and not focus_context:
            raise RuntimeError("No content to send to LLM (no images, OCR text, or focus context)")

        # Build prompt
        prompt_parts = [
            "You are summarizing a developer's work activity.",
            "",
        ]

        if previous_summary:
            prompt_parts.append(f"Previous context: {previous_summary}")
            prompt_parts.append("")

        if focus_context:
            prompt_parts.append("## Time Breakdown (from focus tracking)")
            prompt_parts.append(focus_context)
            prompt_parts.append("")

        if ocr_section:
            prompt_parts.append("## Window Content (OCR)")
            prompt_parts.append(ocr_section)
            prompt_parts.append("")

        if images_base64:
            prompt_parts.append(f"## Screenshots")
            prompt_parts.append(f"{len(images_base64)} screenshots attached showing actual screen content.")
            prompt_parts.append("")

        # Adjust guidance based on what content is actually available
        basis_parts = []
        if focus_context:
            basis_parts.append("the time breakdown")
        if ocr_section:
            basis_parts.append("OCR text")
        if images_base64:
            basis_parts.append("screenshots")

        if len(basis_parts) == 1:
            basis = basis_parts[0]
        elif len(basis_parts) == 2:
            basis = f"{basis_parts[0]} and {basis_parts[1]}"
        else:
            basis = f"{basis_parts[0]}, {basis_parts[1]}, and {basis_parts[2]}"

        prompt_parts.extend([
            f"Based on {basis}, describe the PRIMARY activities.",
            "",
            "Your response MUST follow this exact format:",
            "",
            "SUMMARY: [1-2 sentences, max 25 words describing the main activities]",
            "",
            "EXPLANATION: [What you observed that led to this summary. Mention specific windows, text, or visual elements. If activity doesn't clearly belong to a project, say so.]",
            "",
            "CONFIDENCE: [A number from 0.0 to 1.0. 1.0 = very confident with clear evidence. 0.5 = moderate, some ambiguity. 0.0 = guessing, unclear content]",
            "",
            "Guidelines for SUMMARY:",
            "- If activity clearly belongs to a project, mention the project name",
            "- If activity is general (browsing, reading, communication), describe the activity type",
            "- If unclear, use generic description rather than guessing a project",
            "- Format: \"[Action verb] [what] in/for [project/context]\"",
            "",
            "Examples of good SUMMARY lines (DO NOT copy - describe what YOU see):",
            "- Clear project: \"Implementing focus tracking in activity-tracker daemon.py\"",
            "- General activity: \"Reading documentation and browsing Stack Overflow\"",
            "- Communication: \"Slack conversations and email correspondence\"",
            "- Unclear: \"Code review and documentation reading\"",
            "",
            "CRITICAL: Describe ONLY what is visible. Never invent or assume activities.",
        ])

        prompt = "\n".join(prompt_parts)

        # Build full API request info for debugging
        content_mode = []
        if self.include_focus_context:
            content_mode.append("focus_context")
        if self.include_screenshots:
            content_mode.append("screenshots")
        if self.include_ocr:
            content_mode.append("ocr")

        # Format screenshot IDs used (for UI display)
        screenshot_ids_str = ", ".join(str(sid) for sid in screenshot_ids_used) if screenshot_ids_used else "none"

        api_request_info = (
            f"Model: {self.model}\n"
            f"Content: {', '.join(content_mode)}\n"
            f"Images: {len(images_base64)} base64-encoded JPEG images (max 1024px)\n"
            f"Screenshot IDs used: [{screenshot_ids_str}]\n"
            f"Endpoint: {self.ollama_host}/api/chat\n\n"
            f"Prompt:\n{prompt}"
        )

        # Call LLM (pass images only if we have them)
        response = self._call_ollama_api(prompt, images_base64 if images_base64 else None)

        inference_ms = int((time.time() - start_time) * 1000)

        # Parse structured response
        summary, explanation, confidence = self._parse_summary_response(response)

        return summary, inference_ms, api_request_info, screenshot_ids_used, explanation, confidence

    def _parse_summary_response(self, response: str) -> tuple[str, str, float]:
        """Parse structured response into (summary, explanation, confidence).

        Expected format:
            SUMMARY: ...
            EXPLANATION: ...
            CONFIDENCE: 0.X

        Args:
            response: Raw response from LLM.

        Returns:
            Tuple of (summary, explanation, confidence).
            Falls back gracefully if parsing fails.
        """
        summary = ""
        explanation = ""
        confidence = 0.5  # Default if not parseable

        lines = response.strip().split('\n')
        current_section = None

        for line in lines:
            line_stripped = line.strip()
            line_upper = line_stripped.upper()

            if line_upper.startswith('SUMMARY:'):
                summary = line_stripped[8:].strip()
                current_section = 'summary'
            elif line_upper.startswith('EXPLANATION:'):
                explanation = line_stripped[12:].strip()
                current_section = 'explanation'
            elif line_upper.startswith('CONFIDENCE:'):
                conf_str = line_stripped[11:].strip()
                try:
                    confidence = float(conf_str)
                    confidence = max(0.0, min(1.0, confidence))
                except ValueError:
                    pass
                current_section = 'confidence'
            elif current_section == 'summary' and line_stripped:
                # Multi-line summary
                summary += ' ' + line_stripped
            elif current_section == 'explanation' and line_stripped:
                # Multi-line explanation
                explanation += ' ' + line_stripped

        # Fallback: if no structured format, use entire response as summary
        if not summary:
            summary = response.strip()
            explanation = "Model did not follow structured format."
            confidence = 0.3

        return summary.strip(), explanation.strip(), confidence

    def _build_focus_context(self, focus_events: list[dict]) -> str:
        """
        Build human-readable focus context from events.

        Aggregates focus events by app and window, showing time spent
        and context switches to help the LLM understand work patterns.
        For terminals, includes process introspection details (what command
        was running, working directory).

        Example output:
            - VS Code (tracker/daemon.py): 45m 23s [longest: 18m]
            - Tilix (vim daemon.py in activity-tracker): 30m 12s
            - Firefox (GitHub PR #1234): 12m 5s [3 visits]
            Total: 87m, 14 context switches

        Args:
            focus_events: List of focus event dicts from storage.

        Returns:
            Formatted string describing focus activity.
        """
        if not focus_events:
            return "No focus data available."

        # Aggregate by app + enriched title (with terminal context if available)
        aggregated = {}
        for event in focus_events:
            app_name = event.get('app_name', 'Unknown')
            title = self._truncate_title(event.get('window_title', ''))

            # Enrich with terminal context if available
            terminal_context = event.get('terminal_context')
            if terminal_context:
                enriched_title = self._parse_terminal_context(terminal_context)
                if enriched_title:
                    title = enriched_title

            key = (app_name, title)
            if key not in aggregated:
                aggregated[key] = {
                    'total_seconds': 0,
                    'visit_count': 0,
                    'longest_session': 0
                }
            duration = event.get('duration_seconds', 0) or 0
            aggregated[key]['total_seconds'] += duration
            aggregated[key]['visit_count'] += 1
            aggregated[key]['longest_session'] = max(
                aggregated[key]['longest_session'],
                duration
            )

        # Sort by total time
        sorted_items = sorted(
            aggregated.items(),
            key=lambda x: -x[1]['total_seconds']
        )

        lines = []
        for (app, title), stats in sorted_items[:8]:  # Top 8
            duration_str = self._format_duration(stats['total_seconds'])

            extra = []
            if stats['longest_session'] >= 300:  # 5+ min focus worth noting
                extra.append(f"longest: {self._format_duration(stats['longest_session'])}")
            if stats['visit_count'] > 1:
                extra.append(f"{stats['visit_count']} visits")

            extra_str = f" [{', '.join(extra)}]" if extra else ""

            if title and title != app:
                lines.append(f"- {app} ({title}): {duration_str}{extra_str}")
            else:
                lines.append(f"- {app}: {duration_str}{extra_str}")

        # Summary stats
        total_seconds = sum(e.get('duration_seconds', 0) or 0 for e in focus_events)
        context_switches = self._count_context_switches(focus_events)

        lines.append(f"\nTotal tracked: {self._format_duration(total_seconds)}, {context_switches} context switches")

        return '\n'.join(lines)

    def _parse_terminal_context(self, context_json: str) -> str:
        """Parse terminal context JSON and return enriched title.

        Args:
            context_json: JSON string with terminal introspection data.

        Returns:
            Enriched title string like "vim daemon.py in activity-tracker"
            or empty string if parsing fails.
        """
        import json
        try:
            ctx = json.loads(context_json)
            parts = []

            # Main process (skip shells for cleaner display)
            fg_process = ctx.get('foreground_process', '')
            shell = ctx.get('shell', '')
            if fg_process and fg_process not in {'bash', 'zsh', 'fish', 'sh', 'dash'}:
                # Include command args if they add context (e.g., "vim daemon.py")
                full_cmd = ctx.get('full_command', '')
                if full_cmd and ' ' in full_cmd:
                    # Get first meaningful arg (skip flags like -m, --version)
                    cmd_parts = full_cmd.split()
                    arg = None
                    for part in cmd_parts[1:]:
                        if not part.startswith('-') and len(part) > 1:
                            arg = part
                            break
                    if arg:
                        # Truncate long paths to just filename
                        if '/' in arg:
                            arg = arg.split('/')[-1]
                        if len(arg) < 30:
                            parts.append(f"{fg_process} {arg}")
                        else:
                            parts.append(fg_process)
                    else:
                        parts.append(fg_process)
                else:
                    parts.append(fg_process)
            elif shell:
                parts.append(f"{shell} (idle)")

            # Working directory (just the project name)
            cwd = ctx.get('working_directory', '')
            if cwd:
                from pathlib import Path
                dir_name = Path(cwd).name
                if dir_name and dir_name not in parts:
                    parts.append(f"in {dir_name}")

            # SSH indicator
            if ctx.get('is_ssh'):
                parts.append("[ssh]")

            # Tmux session
            tmux = ctx.get('tmux_session')
            if tmux:
                parts.append(f"[tmux:{tmux}]")

            return ' '.join(parts) if parts else ''

        except (json.JSONDecodeError, TypeError, AttributeError):
            return ''

    def _truncate_title(self, title: str, max_len: int = 50) -> str:
        """Truncate and clean window title for display."""
        if not title:
            return ""
        # Remove common browser/editor suffixes
        for suffix in [' - Google Chrome', ' - Mozilla Firefox', ' - Visual Studio Code',
                       ' — Mozilla Firefox', ' - Chromium', ' - Code - OSS']:
            title = title.replace(suffix, '')

        if len(title) > max_len:
            return title[:max_len-3] + '...'
        return title

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as human readable duration."""
        if not seconds:
            return "0s"
        seconds = float(seconds)
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s" if secs else f"{mins}m"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def _count_context_switches(self, events: list[dict]) -> int:
        """Count app switches in event list."""
        if len(events) < 2:
            return 0
        sorted_events = sorted(events, key=lambda x: x.get('start_time', ''))
        return sum(
            1 for i in range(1, len(sorted_events))
            if sorted_events[i].get('app_name') != sorted_events[i-1].get('app_name')
        )

    def _sample_screenshots_uniform(
        self,
        screenshots: list[dict],
        max_n: int,
        interval_minutes: int = 10
    ) -> list[dict]:
        """
        Uniformly sample screenshots across a session's time range.

        Selects screenshots evenly distributed across the session duration,
        targeting approximately 1 screenshot per interval_minutes.

        Args:
            screenshots: List of screenshot dicts with timestamp field.
            max_n: Maximum number of screenshots to return.
            interval_minutes: Target interval between samples.

        Returns:
            List of selected screenshot dicts.
        """
        if len(screenshots) <= max_n:
            return screenshots

        # Calculate ideal sample count based on interval
        if screenshots:
            timestamps = [s.get("timestamp", 0) for s in screenshots]
            duration_minutes = (max(timestamps) - min(timestamps)) / 60
            ideal_samples = max(1, int(duration_minutes / interval_minutes))
            target_count = min(ideal_samples, max_n)
        else:
            target_count = max_n

        # Uniform sampling
        step = len(screenshots) / target_count
        indices = [int(i * step) for i in range(target_count)]

        return [screenshots[i] for i in indices]

    def _sample_screenshots_weighted(
        self,
        screenshots: list[dict],
        focus_events: list[dict],
        max_n: int,
        interval_minutes: int = 10
    ) -> list[dict]:
        """
        Sample screenshots weighted by focus duration per app/window.

        Screenshots from apps with more focus time get proportionally more
        representation in the sample. This ensures the LLM sees screenshots
        that reflect actual work patterns (e.g., 80% terminal time = ~80%
        terminal screenshots).

        Args:
            screenshots: List of screenshot dicts with app_name field.
            focus_events: List of focus event dicts with app_name and duration.
            max_n: Maximum number of screenshots to return.
            interval_minutes: Target interval between samples.

        Returns:
            List of selected screenshot dicts weighted by focus time.
        """
        if len(screenshots) <= max_n:
            return screenshots

        if not focus_events:
            return self._sample_screenshots_uniform(screenshots, max_n, interval_minutes)

        # Calculate ideal sample count based on interval
        timestamps = [s.get("timestamp", 0) for s in screenshots]
        duration_minutes = (max(timestamps) - min(timestamps)) / 60 if timestamps else 0
        ideal_samples = max(1, int(duration_minutes / interval_minutes))
        target_count = min(ideal_samples, max_n, len(screenshots))

        if target_count >= len(screenshots):
            return screenshots

        # Calculate total focus time per app
        app_focus_time = {}
        for event in focus_events:
            app = event.get('app_name', 'unknown') or 'unknown'
            duration = event.get('duration_seconds', 0) or 0
            app_focus_time[app] = app_focus_time.get(app, 0) + duration

        total_focus_time = sum(app_focus_time.values())
        if total_focus_time == 0:
            return self._sample_screenshots_uniform(screenshots, max_n, interval_minutes)

        # Group screenshots by app
        app_screenshots = {}
        for ss in screenshots:
            app = ss.get('app_name', 'unknown') or 'unknown'
            if app not in app_screenshots:
                app_screenshots[app] = []
            app_screenshots[app].append(ss)

        # Allocate samples per app based on focus time proportion
        sampled = []
        remaining_quota = target_count

        # Sort apps by focus time (most time first)
        sorted_apps = sorted(app_focus_time.keys(), key=lambda a: -app_focus_time.get(a, 0))

        for app in sorted_apps:
            if remaining_quota <= 0:
                break

            app_ss = app_screenshots.get(app, [])
            if not app_ss:
                continue

            # Calculate quota for this app based on focus proportion
            focus_ratio = app_focus_time.get(app, 0) / total_focus_time
            app_quota = max(1, int(target_count * focus_ratio))
            app_quota = min(app_quota, remaining_quota, len(app_ss))

            # Uniformly sample within this app's screenshots
            if len(app_ss) <= app_quota:
                sampled.extend(app_ss)
            else:
                step = len(app_ss) / app_quota
                indices = [int(i * step) for i in range(app_quota)]
                sampled.extend([app_ss[i] for i in indices])

            remaining_quota -= len(sampled) - (target_count - remaining_quota)

        # If we still have quota, fill from apps without focus data
        if len(sampled) < target_count:
            apps_with_focus = set(app_focus_time.keys())
            for app, app_ss in app_screenshots.items():
                if app in apps_with_focus:
                    continue
                for ss in app_ss:
                    if ss not in sampled and len(sampled) < target_count:
                        sampled.append(ss)

        # Sort by timestamp to maintain chronological order
        sampled.sort(key=lambda s: s.get('timestamp', 0))

        logger.debug(
            f"Focus-weighted sampling: {len(sampled)} screenshots from "
            f"{len(set(s.get('app_name') for s in sampled))} apps"
        )

        return sampled

    def extract_ocr(self, image_path: str) -> str:
        """
        Public wrapper for OCR extraction.

        Args:
            image_path: Path to the image file.

        Returns:
            Extracted text, or empty string on failure.
        """
        return self._extract_ocr(image_path)

    def get_cropped_path(self, screenshot: dict) -> str:
        """
        Public wrapper to get cropped screenshot path.

        Args:
            screenshot: Screenshot dict with filepath and optional geometry.

        Returns:
            Path to cropped screenshot (or original if no geometry).
        """
        return self._get_cropped_screenshot(screenshot)

    def _extract_ocr(self, image_path: str) -> str:
        """
        Extract text from an image using Tesseract OCR.

        Loads the image, resizes to max 1920px width (preserving aspect ratio),
        and runs Tesseract with --psm 3 (fully automatic page segmentation).

        Args:
            image_path: Path to the image file.

        Returns:
            Extracted text, or empty string on failure.
        """
        try:
            # Load and resize image
            with Image.open(image_path) as img:
                # Resize to max 1920px width, preserving aspect ratio
                max_width = 1920
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

                # Also cap height at 1080 for consistency
                max_height = 1080
                if img.height > max_height:
                    ratio = max_height / img.height
                    new_width = int(img.width * ratio)
                    img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)

                # Save to temp file for tesseract
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                    img.save(tmp_path, "PNG")

            # Run tesseract
            result = subprocess.run(
                ["tesseract", tmp_path, "stdout", "--psm", "3"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

            if result.returncode != 0:
                logger.warning(f"Tesseract returned non-zero: {result.stderr}")
                return ""

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            logger.warning("Tesseract timed out")
            return ""
        except FileNotFoundError:
            logger.warning("Tesseract not installed or not in PATH")
            return ""
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")
            return ""

    def _get_cropped_screenshot(self, screenshot: dict) -> str:
        """
        Return path to cropped version of screenshot (cached).

        Creates a cropped version focused on the active window for improved
        OCR and LLM accuracy. Falls back to full screenshot if no geometry data.

        Args:
            screenshot: Screenshot dict with filepath and optional window_x/y/width/height.

        Returns:
            Path to cropped screenshot (or original if no geometry or crop already exists).

        Note:
            Cropped images are cached as {original}_crop.webp files.
            Edge cases handled:
            - No geometry data: returns full screenshot
            - Fullscreen apps: crop matches original
            - Window partially off-screen: clamped to valid range
        """
        filepath = screenshot['filepath']

        # Check if we have window geometry
        if not all([screenshot.get('window_x') is not None,
                   screenshot.get('window_y') is not None,
                   screenshot.get('window_width'),
                   screenshot.get('window_height')]):
            # No geometry data, use full screenshot
            return filepath

        # Check cache - cropped version should be next to original
        crop_path = filepath.replace('.webp', '_crop.webp')
        if Path(crop_path).exists():
            return crop_path

        try:
            # Load full screenshot and crop to window
            with Image.open(filepath) as img:
                # Get geometry
                x = screenshot['window_x']
                y = screenshot['window_y']
                w = screenshot['window_width']
                h = screenshot['window_height']

                # Clamp coordinates to image bounds (handle partially off-screen windows)
                x = max(0, min(x, img.width))
                y = max(0, min(y, img.height))
                x2 = max(0, min(x + w, img.width))
                y2 = max(0, min(y + h, img.height))

                # Check if crop would be different from original (handle fullscreen)
                if x == 0 and y == 0 and x2 == img.width and y2 == img.height:
                    # Fullscreen or same size, return original
                    return filepath

                # Crop to window bounds
                cropped = img.crop((x, y, x2, y2))

                # Save cropped version
                cropped.save(crop_path, 'WEBP', quality=85)
                logger.debug(f"Created cropped screenshot: {crop_path}")

                return crop_path

        except Exception as e:
            logger.warning(f"Failed to crop screenshot {filepath}: {e}")
            # Fall back to full screenshot
            return filepath

    def _prepare_image(self, path: str) -> str:
        """
        Prepare an image for the LLM by resizing and converting to base64.

        Resizes the image to max 1024px (preserving aspect ratio) and
        encodes it as base64 for use with Ollama.

        Args:
            path: Path to the image file.

        Returns:
            Base64-encoded image string.

        Raises:
            Exception: If image loading or processing fails.
        """
        with Image.open(path) as img:
            # Resize to max 1024px on longest side
            max_size = 1024
            if img.width > max_size or img.height > max_size:
                if img.width > img.height:
                    ratio = max_size / img.width
                else:
                    ratio = max_size / img.height
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Convert to RGB if necessary (for JPEG encoding)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Encode to base64
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)

            return base64.b64encode(buffer.read()).decode("utf-8")

    def is_available(self) -> bool:
        """
        Check if both Ollama and Tesseract are available.

        Verifies that:
        1. Tesseract is installed and in PATH
        2. Ollama Docker container is running and the configured model is available

        Returns:
            True if both dependencies are available, False otherwise.
        """
        # Check tesseract
        tesseract_available = shutil.which("tesseract") is not None
        if not tesseract_available:
            logger.warning("Tesseract not found in PATH")
            return False

        # Check Ollama via HTTP API (Docker container)
        try:
            # Check if Ollama is running
            response = requests.get(
                f"{self.ollama_host}/api/tags",
                timeout=5,
            )
            response.raise_for_status()

            # List models to check if our model exists
            data = response.json()
            model_names = [m.get("name", "") for m in data.get("models", [])]

            # Check if our model is available (handle tag variations)
            model_base = self.model.split(":")[0]
            model_found = any(m.startswith(model_base) for m in model_names)

            if not model_found:
                logger.warning(f"Model {self.model} not found in Ollama")
                return False

            return True

        except requests.exceptions.ConnectionError:
            logger.warning(
                f"Cannot connect to Ollama at {self.ollama_host}. "
                "Ensure the Ollama Docker container is running."
            )
            return False
        except Exception as e:
            logger.warning(f"Ollama check failed: {e}")
            return False

    def generate_text(self, prompt: str) -> str:
        """
        Generate text from a prompt using the LLM (no images).

        This is a simple text-only LLM call for generating summaries,
        reports, or other text based on a prompt.

        Args:
            prompt: The text prompt to send to the model.

        Returns:
            The model's response text.

        Raises:
            RuntimeError: If Ollama is unavailable or the model fails.
        """
        return self._call_ollama_api(prompt)

    def summarize_day(self, hourly_summaries: list[dict]) -> str:
        """
        Combine hourly summaries into a daily rollup summary.

        Takes a list of hourly summaries and uses a text-only LLM call
        to generate a consolidated daily summary.

        Args:
            hourly_summaries: List of dicts with {"hour": int, "summary": str}

        Returns:
            A 2-3 sentence consolidated daily summary.

        Raises:
            ValueError: If hourly_summaries is empty.
            RuntimeError: If Ollama is unavailable or the model fails.
        """
        if not hourly_summaries:
            raise ValueError("hourly_summaries cannot be empty")

        # Format summaries for prompt
        formatted = []
        for item in sorted(hourly_summaries, key=lambda x: x["hour"]):
            hour = item["hour"]
            summary = item["summary"]
            formatted.append(f"{hour}:00 - {summary}")

        summaries_text = "\n".join(formatted)

        prompt = (
            "Combine these hourly work summaries into a 2-3 sentence daily summary. "
            "Focus on main accomplishments and themes.\n\n"
            f"{summaries_text}"
        )

        return self._call_ollama_api(prompt)
