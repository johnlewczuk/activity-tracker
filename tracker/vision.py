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
        model: str = "gemma3:27b-it-qat",
        timeout: int = 120,
        ollama_host: str = None,
        max_samples: int = 10,
    ):
        """
        Initialize the HybridSummarizer.

        Args:
            model: Ollama model name to use for vision inference.
            timeout: Timeout in seconds for LLM calls.
            ollama_host: Base URL for Ollama API. Defaults to http://localhost:11434.
            max_samples: Maximum screenshots to send to LLM (default 10).
        """
        self.model = model
        self.timeout = timeout
        self.ollama_host = ollama_host or DEFAULT_OLLAMA_HOST
        self.max_samples = max_samples

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
    ) -> tuple[str, int, str, list]:
        """
        Summarize a session with context continuity.

        Takes screenshots from a session, OCR text from unique windows,
        and optionally the previous session's summary for context.

        Args:
            screenshots: List of dicts with {id, filepath, window_title, timestamp}.
            ocr_texts: List of dicts with {window_title, ocr_text}.
            previous_summary: Optional previous session summary for continuity.

        Returns:
            Tuple of (summary text, inference time in ms, prompt text, screenshot IDs used).

        Raises:
            ValueError: If screenshots is empty.
            RuntimeError: If Ollama is unavailable or the model fails.
        """
        if not screenshots:
            raise ValueError("screenshots cannot be empty")

        start_time = time.time()

        # Sample screenshots uniformly
        sampled = self._sample_screenshots(screenshots, self.max_samples)
        logger.info(f"Sampled {len(sampled)} of {len(screenshots)} screenshots")

        # Extract IDs of screenshots actually used
        screenshot_ids_used = [s["id"] for s in sampled]

        # Prepare images for LLM (use cropped versions for better focus)
        images_base64 = []
        for s in sampled:
            try:
                # Get cropped version (falls back to full if no geometry)
                img_path = self._get_cropped_screenshot(s)
                img_b64 = self._prepare_image(img_path)
                images_base64.append(img_b64)
            except Exception as e:
                logger.warning(f"Failed to prepare image {s['filepath']}: {e}")

        if not images_base64:
            raise RuntimeError("Failed to prepare any images for LLM")

        # Format OCR texts
        ocr_section = ""
        if ocr_texts:
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

        # Build prompt
        prompt_parts = [
            "You are summarizing a developer's work session.",
            "",
        ]

        if previous_summary:
            prompt_parts.append(f"Previous session context: {previous_summary}")
            prompt_parts.append("")

        if ocr_section:
            prompt_parts.append("Window titles and OCR text from this session:")
            prompt_parts.append(ocr_section)
            prompt_parts.append("")

        prompt_parts.extend([
            "Based on the screenshots and text above, write ONE sentence (max 20 words) describing the main activity.",
            'Format: "[Action verb] [what] in/for [project/context]"',
            "Examples:",
            '- "Debugging portal permissions in activity-tracker service"',
            '- "Building dataset with 1000 images for object detection"',
            '- "Implementing hybrid mode for virtual device simulation"',
            '- "Reviewing pull request for authentication changes"',
            "",
            "Be specific. Use actual filenames, project names, and technical terms visible in the screenshots.",
        ])

        prompt = "\n".join(prompt_parts)

        # Build full API request info for debugging
        api_request_info = (
            f"Model: {self.model}\n"
            f"Images: {len(images_base64)} base64-encoded JPEG images (max 1024px)\n"
            f"Endpoint: {self.ollama_host}/api/chat\n\n"
            f"Prompt:\n{prompt}"
        )

        # Call LLM
        response = self._call_ollama_api(prompt, images_base64)

        inference_ms = int((time.time() - start_time) * 1000)
        return response.strip(), inference_ms, api_request_info, screenshot_ids_used

    def _sample_screenshots(
        self, screenshots: list[dict], max_n: int
    ) -> list[dict]:
        """
        Uniformly sample screenshots across a session's time range.

        Selects screenshots evenly distributed across the session duration,
        targeting approximately 1 screenshot per 10 minutes.

        Args:
            screenshots: List of screenshot dicts with timestamp field.
            max_n: Maximum number of screenshots to return.

        Returns:
            List of selected screenshot dicts.
        """
        if len(screenshots) <= max_n:
            return screenshots

        # Calculate ideal sample count (~1 per 10 min)
        if screenshots:
            timestamps = [s.get("timestamp", 0) for s in screenshots]
            duration_minutes = (max(timestamps) - min(timestamps)) / 60
            ideal_samples = max(1, int(duration_minutes / 10))
            target_count = min(ideal_samples, max_n)
        else:
            target_count = max_n

        # Uniform sampling
        step = len(screenshots) / target_count
        indices = [int(i * step) for i in range(target_count)]

        return [screenshots[i] for i in indices]

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
