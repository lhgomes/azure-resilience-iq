"""
Application settings loader.

Loads application configuration from:
1. APP_CONFIG_PATH environment variable (from .env)
2. Default location: ./config/app_config.yaml (relative to backend)
3. Fallback defaults if file not found
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional

import yaml
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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
        1. Explicit parameter passed to load_settings
        2. Default: ./config/app_config.yaml (relative to backend)
        """
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
                "enabled": False,
                "batch_threshold": 100,
                "max_nodes_per_batch": 50,
            },
            "ai_agent": {
                "chat_agent_id": None,
                "resilience_agent_id": None,
                "annotations_agent_id": None,
                "gateway_base_url": None,
                "embedding_base_url": None,
                "embedding_api_version": "2024-10-21",
                "embedding_subscription_header_name": "api-key",
                "api_version": "2025-05-01",
                "subscription_header_name": "api-key",
                "memory_scope": "subscription_or_workload",
                "run_timeout_seconds": 120,
                "poll_interval_seconds": 1.5,
            },
            "data": {
                "dir": "./data",
                "monitored_resource_types_path": "./config/monitored_resource_types.yaml",
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
                "learn_more_defaults": {},
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
        """Check if LLM should run (merged toggle)."""
        llm_config = self.get_llm_config()
        return llm_config.get("enabled", False)

    def is_annotation_enabled(self) -> bool:
        """Check if LLM annotations should be included (merged toggle)."""
        llm_config = self.get_llm_config()
        return llm_config.get("enabled", False)
    
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
        """Get configured log level."""
        logging_config = self.config.get("logging", {})
        return logging_config.get("level", "INFO").upper()

    def get_llm_generation_config(self) -> Dict[str, Any]:
        """Get provider-neutral generation settings for LLM calls.

        Priority: generic LLM env/config > defaults.
        """
        llm_cfg = self.get_llm_config()
        return {
            "model": (
                os.getenv("LLM_MODEL")
                or llm_cfg.get("model")
            ),
            "max_attempts": int(
                os.getenv("LLM_MAX_ATTEMPTS")
                or llm_cfg.get("max_attempts")
                or 2
            ),
            "max_tokens": int(
                os.getenv("LLM_MAX_TOKENS")
                or llm_cfg.get("max_tokens")
                or 6000
            ),
            "timeout_seconds": int(
                os.getenv("LLM_TIMEOUT_SECONDS")
                or llm_cfg.get("timeout_seconds")
                or 60
            ),
        }

    def get_llm_batching_config(self) -> Dict[str, int]:
        """Get LLM batching configuration values."""
        llm_cfg = self.get_llm_config()
        return {
            "batch_threshold": int(llm_cfg.get("batch_threshold", 100)),
            "max_nodes_per_batch": int(llm_cfg.get("max_nodes_per_batch", 50)),
        }

    def get_ai_agent_config(self) -> Dict[str, Any]:
        """Get Azure AI Foundry agent configuration values."""
        agent_cfg = self.config.get("ai_agent", {})
        return {
            "gateway_base_url": (
                os.getenv("AI_GATEWAY_AGENT_BASE_URL")
                or agent_cfg.get("gateway_base_url")
            ),
            "embedding_base_url": (
                os.getenv("AI_GATEWAY_EMBEDDING_BASE_URL")
                or agent_cfg.get("embedding_base_url")
            ),
            "subscription_key": os.getenv("AI_GATEWAY_SUBSCRIPTION_KEY"),
            "subscription_header_name": (
                os.getenv("AI_GATEWAY_SUBSCRIPTION_HEADER_NAME")
                or agent_cfg.get("subscription_header_name")
                or "api-key"
            ),
            "embedding_subscription_header_name": (
                os.getenv("AI_GATEWAY_EMBEDDING_SUBSCRIPTION_HEADER_NAME")
                or agent_cfg.get("embedding_subscription_header_name")
                or "api-key"
            ),
            "api_version": (
                os.getenv("AI_GATEWAY_API_VERSION")
                or agent_cfg.get("api_version", "2025-05-01")
            ),
            "embedding_api_version": (
                os.getenv("AI_GATEWAY_EMBEDDING_API_VERSION")
                or agent_cfg.get("embedding_api_version", "2024-10-21")
            ),
            "chat_agent_id": os.getenv("AI_GATEWAY_CHAT_AGENT_ID") or agent_cfg.get("chat_agent_id"),
            "resilience_agent_id": (
                os.getenv("AI_GATEWAY_RESILIENCE_AGENT_ID")
                or agent_cfg.get("resilience_agent_id")
            ),
            "annotations_agent_id": (
                os.getenv("AI_GATEWAY_ANNOTATIONS_AGENT_ID")
                or agent_cfg.get("annotations_agent_id")
            ),
            "memory_scope": os.getenv("AI_GATEWAY_MEMORY_SCOPE") or agent_cfg.get("memory_scope", "subscription_or_workload"),
            "run_timeout_seconds": int(agent_cfg.get("run_timeout_seconds", 120)),
            "poll_interval_seconds": float(agent_cfg.get("poll_interval_seconds", 1.5)),
        }

    def get_agent_id_for_flow(self, flow: str) -> Optional[str]:
        """Get configured agent id for a chat orchestration flow.

        Flow values:
        - chat
        - resilience
        - annotations
        """
        ai_agent_config = self.get_ai_agent_config()
        flow_map = {
            "chat": ai_agent_config.get("chat_agent_id"),
            "resilience": ai_agent_config.get("resilience_agent_id"),
            "annotations": ai_agent_config.get("annotations_agent_id"),
        }
        selected = flow_map.get((flow or "").strip().lower())
        if selected and str(selected).strip():
            return str(selected).strip()
        return None

    def is_chat_available(self) -> bool:
        """
        Check if chat feature is available (all required config is present).

        Required configuration (APIM + Foundry mode):
        - llm.enabled=true
        - ai_agent.gateway_base_url
                - flow-specific agent ids:
                    AI_GATEWAY_CHAT_AGENT_ID / AI_GATEWAY_RESILIENCE_AGENT_ID / AI_GATEWAY_ANNOTATIONS_AGENT_ID
        - AI_GATEWAY_SUBSCRIPTION_KEY

        Returns:
            True if all required chat configuration is present
        """
        ai_agent_config = self.get_ai_agent_config()

        required_flow_ids = [
            ai_agent_config.get("chat_agent_id"),
            ai_agent_config.get("resilience_agent_id"),
            ai_agent_config.get("annotations_agent_id"),
        ]

        required_fields = [
            ai_agent_config.get("gateway_base_url"),
            ai_agent_config.get("subscription_key"),
            *required_flow_ids,
        ]

        return self.use_real_llm() and all(
            field is not None and str(field).strip() != "" for field in required_fields
        )

    def get_data_dir(self) -> str:
        """Get base data directory for filesystem artifacts."""
        data_cfg = self.config.get("data", {})
        return data_cfg.get("dir", "./data")

    def get_monitored_resource_types_path(self) -> str:
        """Path to monitored resource types allowlist."""
        data_cfg = self.config.get("data", {})
        return data_cfg.get("monitored_resource_types_path", "./config/monitored_resource_types.yaml")
    
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

    def get_learn_more_defaults(self) -> Dict[str, str]:
        """Get mapping of resource type prefixes to default Microsoft Learn URLs."""
        resilience_config = self.get_resilience_config()
        defaults = resilience_config.get("learn_more_defaults", {})
        # Normalize keys to lowercase for case-insensitive matching
        return {k.lower(): v for k, v in defaults.items()}


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
    log_level_str = settings.get_log_level() if settings else "INFO"
    
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
