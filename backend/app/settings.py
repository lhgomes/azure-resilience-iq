"""
Application settings loader.

Loads application configuration from:
1. APP_CONFIG_PATH environment variable (from .env)
2. Default location: ./config/app_config.yaml (relative to backend)
3. Fallback defaults if file not found
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

LOGGER = logging.getLogger(__name__)


class AppSettings:
    """Application configuration loader and manager."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.
        
        Args:
            config_path: Optional path to config file. If not provided,
                        will use APP_CONFIG_PATH env var or default location.
        """
        self.config_path = config_path or self._get_config_path()
        self.config = self._load_config()
    
    @staticmethod
    def _get_config_path() -> str:
        """
        Determine config file path.
        
        Priority:
        1. APP_CONFIG_PATH environment variable
        2. Default: ./config/app_config.yaml (relative to backend)
        """
        # Check environment variable first
        env_path = os.getenv("APP_CONFIG_PATH")
        if env_path:
            return env_path
        
        # Default path relative to backend directory
        return "./config/app_config.yaml"
    
    def _load_config(self) -> Dict[str, Any]:
        """
        Load configuration from YAML file.
        
        Returns:
            Dictionary with application configuration
        """
        try:
            # Resolve path relative to current working directory
            config_file = Path(self.config_path)
            
            if not config_file.exists():
                LOGGER.warning(
                    f"Config file not found at {self.config_path}, using defaults"
                )
                return self._get_defaults()
            
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
            
            LOGGER.debug(f"Configuration loaded from {self.config_path}")
            return config or self._get_defaults()
            
        except Exception as e:
            LOGGER.error(f"Error loading config from {self.config_path}: {e}")
            LOGGER.debug("Using default configuration")
            return self._get_defaults()
    
    @staticmethod
    def _get_defaults() -> Dict[str, Any]:
        """
        Get default configuration if file not found.
        
        Returns:
            Dictionary with default application configuration
        """
        return {
            "logging": {
                "level": "INFO",
            },
            "llm": {
                "use_real_llm": False,
                "annotation_enabled": False,
            },
            "resilience": {
                "category_weights": {
                    "High Availability": 0.40,
                    "Disaster Recovery": 0.30,
                    "Monitoring and Alerting": 0.20,
                    "Security": 0.10,
                },
                "impact_weights": {
                    "High": 0.5,
                    "Medium": 0.3,
                    "Low": 0.2,
                },
                "aprl_root": "./backend/aprl",
                "rules_dir": "./config/resiliency_rules",
            },
        }
    
    # Convenience methods for accessing common settings
    
    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM configuration section."""
        return self.config.get("llm", {})
    
    def get_resilience_config(self) -> Dict[str, Any]:
        """Get resilience configuration section."""
        return self.config.get("resilience", {})
    
    def get_category_weights(self) -> Dict[str, float]:
        """Get resilience category weights."""
        resilience_config = self.get_resilience_config()
        return resilience_config.get("category_weights", {})
    
    def get_impact_weights(self) -> Dict[str, float]:
        """Get impact-to-weight mapping for individual checks."""
        resilience_config = self.get_resilience_config()
        return resilience_config.get("impact_weights", {})
    
    def use_real_llm(self) -> bool:
        """Check if real LLM should be used."""
        llm_config = self.get_llm_config()
        return llm_config.get("use_real_llm", False)
    
    def is_annotation_enabled(self) -> bool:
        """Check if LLM annotations are enabled."""
        llm_config = self.get_llm_config()
        return llm_config.get("annotation_enabled", False)
    
    def get_aprl_root(self) -> str:
        """Get APRL root directory (absolute path)."""
        resilience_config = self.get_resilience_config()
        aprl_path = resilience_config.get("aprl_root", "aprl")
        
        # Convert to absolute path if relative
        aprl_path_obj = Path(aprl_path)
        if not aprl_path_obj.is_absolute():
            # Relative to backend directory where settings.py lives
            backend_root = Path(__file__).parent.parent
            aprl_path_obj = backend_root / aprl_path
        
        return str(aprl_path_obj)
    
    def get_log_level(self) -> str:
        """Get configured log level (can be overridden by LOG_LEVEL env var)."""
        # Check environment variable first
        env_level = os.getenv("LOG_LEVEL")
        if env_level:
            return env_level.upper()
        
        # Fall back to config
        logging_config = self.config.get("logging", {})
        return logging_config.get("level", "INFO").upper()
    
    def get_rules_dir(self) -> str:
        """Get resilience rules directory."""
        resilience_config = self.get_resilience_config()
        return resilience_config.get("rules_dir", "./config/resiliency_rules")
    
    def get_zone_irrelevant_types(self) -> set:
        """Get set of resource types that don't require zone configuration.
        
        Returns:
            Set of lowercase resource type strings (e.g., 'microsoft.network/virtualnetworks')
        """
        resilience_config = self.get_resilience_config()
        type_list = resilience_config.get("zone_irrelevant_types", [])
        return {t.lower() for t in type_list}


# Global settings instance
_app_settings: Optional[AppSettings] = None


def load_settings(config_path: Optional[str] = None) -> AppSettings:
    """
    Load and cache application settings.
    
    Args:
        config_path: Optional path to config file
        
    Returns:
        AppSettings instance
    """
    global _app_settings
    if _app_settings is None:
        _app_settings = AppSettings(config_path)
    return _app_settings


def get_settings() -> AppSettings:
    """
    Get cached application settings.
    
    Returns:
        AppSettings instance
        
    Raises:
        RuntimeError: If settings have not been loaded yet
    """
    global _app_settings
    if _app_settings is None:
        raise RuntimeError("Settings not loaded. Call load_settings() first.")
    return _app_settings


def configure_logging(settings: Optional[AppSettings] = None) -> None:
    """
    Configure logging for the application.
    
    Args:
        settings: Optional AppSettings instance. If not provided, will use default INFO level.
    """
    log_level_str = "INFO"
    
    if settings:
        log_level_str = settings.get_log_level()
    else:
        # Check environment variable
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    
    # Convert string to logging level
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,  # Override any existing configuration
    )
    
    # Set Azure SDK loggers to WARNING to reduce noise
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)
