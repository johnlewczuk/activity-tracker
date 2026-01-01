"""Configuration management system for Activity Tracker.

This module provides a hierarchical configuration system using YAML files and
Python dataclasses. It supports loading, saving, and updating configuration
values at runtime with proper validation and defaults.

Key Features:
- YAML-based configuration files
- Dataclass-based type safety
- Hierarchical configuration sections
- Default values for all settings
- Live updates with automatic save
- Backward compatibility with missing fields

Configuration Sections:
- capture: Screenshot capture settings
- afk: AFK detection and session management
- summarization: AI summarization settings
- storage: Data storage and retention
- web: Web server configuration
- privacy: Privacy controls and exclusions

Example:
    >>> from tracker.config import ConfigManager
    >>> config_mgr = ConfigManager()
    >>> print(config_mgr.config.capture.interval_seconds)
    30
    >>> config_mgr.update('capture', 'interval_seconds', 60)
    >>> config_mgr.save()
"""

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import yaml

logger = logging.getLogger(__name__)


@dataclass
class CaptureConfig:
    """Screenshot capture configuration.

    Attributes:
        interval_seconds: Time between screenshots (default: 30)
        format: Image format - webp, png, jpg (default: webp)
        quality: Compression quality 1-100 for lossy formats (default: 80)
        capture_active_monitor_only: Only capture monitor with focused window (default: True)
        stability_threshold_seconds: Min focus duration before capture (default: 5.0)
        max_interval_multiplier: Force capture after interval * this (default: 2.0)
        skip_transient_windows: Skip notifications, popups, etc. (default: True)
    """
    interval_seconds: int = 30
    format: str = "webp"
    quality: int = 80
    capture_active_monitor_only: bool = True
    stability_threshold_seconds: float = 5.0
    max_interval_multiplier: float = 2.0
    skip_transient_windows: bool = True


@dataclass
class AFKConfig:
    """AFK detection and session management configuration.

    Attributes:
        timeout_seconds: Seconds of inactivity before marking as AFK (default: 180)
        min_session_minutes: Minimum session duration to keep (default: 5)
    """
    timeout_seconds: int = 180
    min_session_minutes: int = 5


@dataclass
class SummarizationConfig:
    """AI summarization configuration.

    Simplified settings with presets for common use cases.

    User-facing settings:
        enabled: Enable automatic summarization (default: True)
        model: Ollama model to use (default: gemma3:12b-it-qat)
        frequency_minutes: How often to generate summaries (default: 15)
        quality_preset: Quick/Balanced/Thorough - sets underlying params (default: balanced)

    Content mode (multi-select - what to include in LLM request):
        include_focus_context: Include window titles and time spent (default: True)
        include_screenshots: Include screenshot images (default: True)
        include_ocr: Include OCR text extraction (default: True)

    Advanced settings:
        ollama_host: Ollama API host URL (default: http://localhost:11434)
        crop_to_window: Use cropped window screenshots (default: True)
        trigger_threshold: Computed from frequency_minutes (screenshots before summarizing)
        max_samples: Set by quality_preset (max screenshots to LLM)
        include_previous_summary: Set by quality_preset (context continuity)
        focus_weighted_sampling: Set by quality_preset (weight by focus time)
        sample_interval_minutes: Computed from frequency (target interval between samples)
    """
    # User-facing settings
    enabled: bool = True
    model: str = "gemma3:12b-it-qat"
    frequency_minutes: int = 15  # 5, 15, 30, 60
    quality_preset: str = "balanced"  # quick, balanced, thorough

    # Content mode (multi-select checkboxes)
    include_focus_context: bool = True  # Window titles + duration
    include_screenshots: bool = True     # Screenshot images
    include_ocr: bool = True             # OCR text extraction

    # Advanced settings
    ollama_host: str = "http://localhost:11434"
    crop_to_window: bool = True

    # Underlying settings (set by quality_preset or computed)
    trigger_threshold: int = 30          # Computed: frequency_minutes * 60 / capture_interval
    max_samples: int = 10                # Set by preset: quick=5, balanced=10, thorough=15
    include_previous_summary: bool = True  # Set by preset: quick=False, balanced/thorough=True
    focus_weighted_sampling: bool = True   # Set by preset: quick=False, balanced/thorough=True
    sample_interval_minutes: int = 10      # Computed from frequency


@dataclass
class StorageConfig:
    """Data storage and retention configuration.

    Attributes:
        data_dir: Directory for storing screenshots and database (default: ~/activity-tracker-data)
        max_days_retention: Delete data older than this (0 = unlimited, default: 90)
        max_gb_storage: Maximum storage space in GB (0 = unlimited, default: 50.0)
    """
    data_dir: str = "~/activity-tracker-data"
    max_days_retention: int = 90
    max_gb_storage: float = 50.0


@dataclass
class WebConfig:
    """Web server configuration.

    Attributes:
        host: Host address to bind to (default: 127.0.0.1)
        port: Port number for web interface (default: 55555)
    """
    host: str = "127.0.0.1"
    port: int = 55555


@dataclass
class PrivacyConfig:
    """Privacy controls and exclusion rules.

    Attributes:
        excluded_apps: App class names to exclude from tracking
        excluded_titles: Window title patterns to exclude
        blur_screenshots: Blur sensitive areas (future feature, default: False)
    """
    excluded_apps: list[str] = field(default_factory=lambda: [
        "1password",
        "keepass",
        "bitwarden",
        "gnome-keyring"
    ])
    excluded_titles: list[str] = field(default_factory=lambda: [
        "Private Browsing",
        "Incognito",
        "InPrivate"
    ])
    blur_screenshots: bool = False


@dataclass
class TrackingConfig:
    """Window focus tracking configuration.

    Attributes:
        min_focus_duration: Ignore focus events shorter than this (default: 1.0)
        track_window_titles: Track window titles - disable for privacy (default: True)
        transient_window_classes: Window classes to skip (notifications, popups, etc.)
    """
    min_focus_duration: float = 1.0
    track_window_titles: bool = True
    transient_window_classes: list[str] = field(default_factory=lambda: [
        "notification",
        "popup",
        "tooltip",
        "dropdown",
        "menu",
        "Dunst",
        "notify-osd",
        "xfce4-notifyd",
        "plank",
        "cairo-dock",
        # GNOME shell overview and desktop icons
        "gnome-shell",
        "Gjs",
        "Desktop Icons",
    ])


@dataclass
class Config:
    """Top-level configuration container.

    Attributes:
        capture: Screenshot capture settings
        afk: AFK detection settings
        summarization: AI summarization settings
        storage: Storage and retention settings
        web: Web server settings
        privacy: Privacy controls
        tracking: Window focus tracking settings
    """
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    afk: AFKConfig = field(default_factory=AFKConfig)
    summarization: SummarizationConfig = field(default_factory=SummarizationConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    tracking: TrackingConfig = field(default_factory=TrackingConfig)


class ConfigManager:
    """Manages configuration loading, saving, and updates.

    Handles YAML configuration file I/O with automatic creation of default
    configuration and merging of user settings with defaults.

    Attributes:
        DEFAULT_PATH: Default configuration file location
        path: Actual configuration file path being used
        config: Current configuration object

    Example:
        >>> config_mgr = ConfigManager()
        >>> config_mgr.config.capture.interval_seconds = 60
        >>> config_mgr.save()
        >>>
        >>> # Update and save in one call
        >>> config_mgr.update('capture', 'quality', 90)
    """

    DEFAULT_PATH = Path("~/.config/activity-tracker/config.yaml").expanduser()

    def __init__(self, path: Optional[Path] = None):
        """Initialize ConfigManager.

        Args:
            path: Custom config file path (uses DEFAULT_PATH if None)
        """
        self.path = Path(path).expanduser() if path else self.DEFAULT_PATH
        self.config = self._load()

    def _load(self) -> Config:
        """Load configuration from YAML file.

        Returns:
            Config object with loaded or default values

        Note:
            Missing fields use defaults from dataclass definitions.
            Invalid YAML returns default Config.
        """
        if self.path.exists():
            try:
                with open(self.path) as f:
                    data = yaml.safe_load(f) or {}
                logger.info(f"Loaded configuration from {self.path}")
                return self._dict_to_config(data)
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to load config from {self.path}: {e}")
                logger.info("Using default configuration")
                return Config()
        else:
            logger.info(f"No config file at {self.path}, using defaults")
            return Config()

    def _dict_to_config(self, data: dict) -> Config:
        """Recursively construct Config from dictionary.

        Args:
            data: Dictionary from YAML file

        Returns:
            Config object with values from dict merged with defaults

        Note:
            Uses ** unpacking to merge dict values with dataclass defaults.
            Missing keys in dict will use dataclass default values.
            Unknown keys are filtered out for backward compatibility.
        """
        def filter_known_fields(data_dict: dict, dataclass_type) -> dict:
            """Filter dict to only include fields known by the dataclass."""
            import dataclasses
            known_fields = {f.name for f in dataclasses.fields(dataclass_type)}
            filtered = {k: v for k, v in data_dict.items() if k in known_fields}
            unknown = set(data_dict.keys()) - known_fields
            if unknown:
                logger.debug(f"Ignoring unknown config fields: {unknown}")
            return filtered

        # Get data for each section, defaulting to empty dict if missing
        capture_data = filter_known_fields(data.get('capture', {}), CaptureConfig)
        afk_data = filter_known_fields(data.get('afk', {}), AFKConfig)
        summarization_data = filter_known_fields(data.get('summarization', {}), SummarizationConfig)
        storage_data = filter_known_fields(data.get('storage', {}), StorageConfig)
        web_data = filter_known_fields(data.get('web', {}), WebConfig)
        privacy_data = filter_known_fields(data.get('privacy', {}), PrivacyConfig)
        tracking_data = filter_known_fields(data.get('tracking', {}), TrackingConfig)

        return Config(
            capture=CaptureConfig(**capture_data),
            afk=AFKConfig(**afk_data),
            summarization=SummarizationConfig(**summarization_data),
            storage=StorageConfig(**storage_data),
            web=WebConfig(**web_data),
            privacy=PrivacyConfig(**privacy_data),
            tracking=TrackingConfig(**tracking_data),
        )

    def save(self) -> None:
        """Save current configuration to YAML file.

        Creates parent directories if they don't exist.
        Formats YAML with proper indentation and no flow style.

        Raises:
            OSError: If file write fails
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'w') as f:
                yaml.dump(
                    asdict(self.config),
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2
                )
            logger.info(f"Saved configuration to {self.path}")
        except OSError as e:
            logger.error(f"Failed to save config to {self.path}: {e}")
            raise

    def update(self, section: str, key: str, value) -> bool:
        """Update a single configuration value and save.

        Args:
            section: Config section name (e.g., 'capture', 'afk')
            key: Setting name within section (e.g., 'interval_seconds')
            value: New value to set

        Returns:
            True if value was changed and saved, False if unchanged or invalid

        Example:
            >>> config_mgr.update('capture', 'interval_seconds', 60)
            True
            >>> config_mgr.update('invalid_section', 'key', 'value')
            False
        """
        section_obj = getattr(self.config, section, None)
        if section_obj is None:
            logger.warning(f"Invalid config section: {section}")
            return False

        if not hasattr(section_obj, key):
            logger.warning(f"Invalid config key: {section}.{key}")
            return False

        old_value = getattr(section_obj, key)
        if old_value != value:
            setattr(section_obj, key, value)
            self.save()
            logger.info(f"Updated {section}.{key}: {old_value} → {value}")
            return True

        logger.debug(f"No change for {section}.{key} (already {value})")
        return False

    def to_dict(self) -> dict:
        """Convert configuration to dictionary.

        Returns:
            Dictionary representation of entire configuration

        Useful for serialization or API responses.
        """
        return asdict(self.config)

    def reload(self) -> None:
        """Reload configuration from file.

        Useful for picking up external changes to the config file.
        """
        self.config = self._load()
        logger.info("Configuration reloaded")

    def create_default_file(self) -> None:
        """Create default configuration file.

        Creates the config file with default values if it doesn't exist.
        Useful for initial setup or reset to defaults.
        """
        if not self.path.exists():
            self.save()
            logger.info(f"Created default configuration at {self.path}")
        else:
            logger.warning(f"Configuration file already exists at {self.path}")


# Singleton instance for easy access throughout the application
_default_config_manager: Optional[ConfigManager] = None


def get_config_manager(path: Optional[Path] = None) -> ConfigManager:
    """Get or create the default ConfigManager instance.

    Args:
        path: Optional custom config path (only used on first call)

    Returns:
        ConfigManager singleton instance

    Example:
        >>> config = get_config_manager()
        >>> print(config.config.capture.interval_seconds)
    """
    global _default_config_manager
    if _default_config_manager is None:
        _default_config_manager = ConfigManager(path)
    return _default_config_manager
