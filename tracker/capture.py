"""Screenshot Capture and Perceptual Hashing Module.

This module provides functionality for capturing desktop screenshots and computing
perceptual hashes for duplicate detection. It uses the MSS library for fast 
cross-platform screen capture and implements the difference hash (dhash) algorithm
for perceptual comparison of images.

The module handles:
- Primary monitor screenshot capture
- WebP compression for space efficiency
- Perceptual hashing using dhash algorithm
- Organized filesystem storage (YYYY/MM/DD structure)
- Duplicate image detection via Hamming distance
- Error handling for display server issues

Key Classes:
    ScreenCapture: Main class for screenshot operations
    ScreenCaptureError: Custom exception for capture-related errors

Dependencies:
    - mss: Multi-platform screenshot library
    - PIL (Pillow): Image processing and format conversion
    - hashlib: Hash computation utilities (unused in current implementation)
    - pathlib: Modern filesystem path handling

Example:
    >>> from tracker.capture import ScreenCapture
    >>> capture = ScreenCapture()
    >>> filepath, dhash = capture.capture_screen()
    >>> print(f"Screenshot saved: {filepath}")
    >>> print(f"Perceptual hash: {dhash}")
"""

import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple
import mss
from PIL import Image
import logging

logger = logging.getLogger(__name__)


class ScreenCaptureError(Exception):
    """Custom exception for screen capture related errors.
    
    This exception is raised when screenshot capture fails due to:
    - Display server connection issues (X11 not running)
    - Monitor access problems
    - Image processing errors
    - Filesystem permission issues
    
    Attributes:
        message (str): Human-readable description of the error
        
    Example:
        >>> try:
        ...     capture = ScreenCapture()
        ...     filepath, dhash = capture.capture_screen()
        ... except ScreenCaptureError as e:
        ...     print(f"Screenshot failed: {e}")
    """
    pass


class ScreenCapture:
    """Handles screenshot capture and perceptual hashing operations.
    
    This class provides methods for capturing desktop screenshots, computing
    perceptual hashes for duplicate detection, and organizing files in a 
    hierarchical directory structure. It uses MSS for fast screen capture
    and implements the dhash algorithm for perceptual comparison.
    
    The class automatically creates the required directory structure and
    saves images in WebP format with 80% quality for space efficiency.
    
    Attributes:
        output_dir (Path): Base directory for storing screenshots
        
    Example:
        >>> capture = ScreenCapture("/home/user/screenshots")
        >>> filepath, dhash = capture.capture_screen()
        >>> 
        >>> # Compare two screenshots for similarity
        >>> similar = capture.are_similar(dhash1, dhash2, threshold=10)
    """
    
    def __init__(self, output_dir: str = "~/activity-tracker-data/screenshots"):
        """Initialize the screen capture instance.
        
        Creates the output directory structure if it doesn't exist. The directory
        will be expanded to handle tilde (~) notation for home directory.
        
        Args:
            output_dir (str): Directory path to save screenshots. Defaults to
                ~/activity-tracker-data/screenshots. Can use tilde notation.
                
        Raises:
            OSError: If directory creation fails due to permission issues.
                
        Note:
            The directory structure will be created as:
            output_dir/YYYY/MM/DD/{timestamp}_{hash}.webp
        """
        self.output_dir = Path(output_dir).expanduser()
        # TODO: Permission errors - check write permissions before attempting to create directories
        # Should handle cases where user doesn't have write access to parent directory
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise ScreenCaptureError(f"Permission denied creating output directory {self.output_dir}: {e}") from e
        
    def capture_screen(self, filename: Optional[str] = None, region: Optional[dict] = None) -> Tuple[str, str]:
        """Capture a screenshot of the primary monitor or specific region and compute its perceptual hash.

        This method captures the display (either primary monitor or specified region),
        converts it to WebP format with 80% quality compression, and generates a
        perceptual hash for duplicate detection. The file is automatically saved
        in a YYYY/MM/DD directory structure.

        Args:
            filename (Optional[str]): Custom filename without extension. If None,
                generates timestamp-based filename: YYYYMMDD_HHMMSS_{hash_prefix}
            region (Optional[dict]): Specific monitor region to capture with keys:
                - left (int): X offset from screen origin
                - top (int): Y offset from screen origin
                - width (int): Region width in pixels
                - height (int): Region height in pixels
                If None, captures primary monitor (default behavior)

        Returns:
            Tuple[str, str]: A tuple containing:
                - filepath (str): Absolute path to the saved screenshot file
                - dhash_hex (str): 16-character hexadecimal perceptual hash

        Raises:
            ScreenCaptureError: If capture fails due to:
                - X11 display server not available
                - Monitor access issues
                - Image processing errors
                - Filesystem permission problems

        Example:
            >>> capture = ScreenCapture()
            >>> # Capture primary monitor
            >>> filepath, dhash = capture.capture_screen()
            >>> print(f"Saved: {filepath}")
            >>>
            >>> # Capture specific monitor
            >>> region = {'left': 3840, 'top': 0, 'width': 1920, 'height': 1080}
            >>> filepath, dhash = capture.capture_screen(region=region)

        Note:
            Screenshots are saved as WebP files with .webp extension automatically added.
        """
        try:
            with mss.mss() as sct:
                # Determine monitor/region to capture
                if region:
                    # Capture specific region (for multi-monitor support)
                    monitor = {
                        'left': region['left'],
                        'top': region['top'],
                        'width': region['width'],
                        'height': region['height']
                    }
                else:
                    # Capture primary monitor (default behavior)
                    if len(sct.monitors) < 2:
                        raise ScreenCaptureError("No monitors detected")
                    monitor = sct.monitors[1]  # monitors[0] is all monitors combined

                # Capture screenshot
                screenshot = sct.grab(monitor)

                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

                # Generate dhash before saving
                dhash = self._generate_dhash(img)

                # Create timestamped filepath if no filename provided
                if filename is None:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{timestamp}_{dhash[:8]}"

                # Ensure directory structure exists (YYYY/MM/DD)
                from datetime import datetime
                now = datetime.now()
                date_dir = self.output_dir / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
                # TODO: Permission errors - handle case where date directory creation fails due to permissions
                try:
                    date_dir.mkdir(parents=True, exist_ok=True)
                except PermissionError as e:
                    raise ScreenCaptureError(f"Permission denied creating date directory {date_dir}: {e}") from e

                # Save as WebP with 80% quality
                filepath = date_dir / f"{filename}.webp"
                # TODO: Permission errors - handle case where file save fails due to permissions or disk full
                try:
                    img.save(filepath, "WEBP", quality=80, method=6)
                except (PermissionError, OSError) as e:
                    raise ScreenCaptureError(f"Failed to save screenshot to {filepath}: {e}") from e

                logger.info(f"Screenshot saved: {filepath}")
                return str(filepath), dhash

        except OSError as e:
            if "cannot connect to display" in str(e).lower():
                # TODO: Wayland compatibility - error message assumes X11, should detect display server type
                # and provide appropriate guidance for both X11 and Wayland
                raise ScreenCaptureError("Cannot connect to display server. Is X11 running?") from e
            else:
                raise ScreenCaptureError(f"Display server error: {e}") from e
        except Exception as e:
            raise ScreenCaptureError(f"Failed to capture screenshot: {e}") from e
    
    def _generate_dhash(self, img: Image.Image, hash_size: int = 8) -> str:
        """Generate difference hash (dhash) for perceptual image comparison.
        
        Implements the dhash algorithm by resizing the image to a small grid,
        converting to grayscale, and computing horizontal gradients. This creates
        a perceptual fingerprint that's robust to minor changes but sensitive
        to significant structural differences.
        
        The algorithm:
        1. Resize image to (hash_size+1) x hash_size pixels
        2. Convert to grayscale
        3. Compare adjacent pixels horizontally
        4. Create bit vector from comparisons
        5. Convert to hexadecimal string
        
        Args:
            img (Image.Image): PIL Image object to generate hash for
            hash_size (int): Size of the hash grid, creating hash_size^2 bit hash.
                Default is 8, producing a 64-bit hash.
                
        Returns:
            str: 16-character hexadecimal string representing the perceptual hash.
                
        Note:
            Dhash is more robust than average hash for detecting rotated or
            slightly modified images while remaining computationally efficient.
        """
        # Resize to hash_size+1 x hash_size for difference calculation
        img = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        
        # Convert to grayscale
        img = img.convert("L")
        
        # Calculate horizontal gradient
        pixels = list(img.getdata())
        difference = []
        
        for row in range(hash_size):
            row_start = row * (hash_size + 1)
            for col in range(hash_size):
                pixel_left = pixels[row_start + col]
                pixel_right = pixels[row_start + col + 1]
                difference.append(pixel_left > pixel_right)
        
        # Convert boolean array to hex string
        decimal_value = 0
        for i, bit in enumerate(difference):
            if bit:
                decimal_value |= (1 << i)
        
        return f"{decimal_value:016x}"
    
    def compare_hashes(self, hash1: str, hash2: str) -> int:
        """Compare two perceptual hashes using Hamming distance.
        
        Calculates the Hamming distance between two dhashes by XORing their
        binary representations and counting the differing bits. Lower distances
        indicate more similar images.
        
        Args:
            hash1 (str): First perceptual hash as hexadecimal string
            hash2 (str): Second perceptual hash as hexadecimal string
            
        Returns:
            int: Hamming distance (0-64 for 8x8 dhash). 0 means identical,
                64 means completely different.
                
        Raises:
            ValueError: If hash lengths differ (incompatible hash sizes)
            
        Example:
            >>> capture = ScreenCapture()
            >>> distance = capture.compare_hashes("a1b2c3d4e5f67890", "a1b2c3d4e5f67891")
            >>> print(f"Images differ by {distance} bits")
        """
        if len(hash1) != len(hash2):
            raise ValueError("Hash lengths must be equal")
        
        # Convert hex to int and XOR
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        xor_result = int1 ^ int2
        
        # Count set bits (Hamming distance)
        return bin(xor_result).count('1')
    
    def are_similar(self, hash1: str, hash2: str, threshold: int = 10) -> bool:
        """Determine if two images are perceptually similar.
        
        Uses Hamming distance between dhashes to determine similarity.
        The default threshold of 10 bits works well for detecting near-duplicates
        while avoiding false positives from minor changes.
        
        Args:
            hash1 (str): First perceptual hash as hexadecimal string
            hash2 (str): Second perceptual hash as hexadecimal string  
            threshold (int): Maximum Hamming distance for similarity. Default 10.
                Lower values = more strict similarity detection.
                
        Returns:
            bool: True if Hamming distance <= threshold, False otherwise
            
        Example:
            >>> capture = ScreenCapture()
            >>> similar = capture.are_similar(hash1, hash2, threshold=5)
            >>> if similar:
            ...     print("Images are very similar")
            
        Note:
            Typical threshold guidelines:
            - 0-5: Nearly identical (minor compression differences)
            - 6-10: Similar with small changes (default range)
            - 11-20: Noticeably different but related
            - 20+: Completely different images
        """
        return self.compare_hashes(hash1, hash2) <= threshold