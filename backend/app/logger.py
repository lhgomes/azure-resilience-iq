"""
Centralized logging configuration for Azure Resilience IQ backend.

This module provides a unified logging setup that:
- Loads log level from config file (app_config.yaml)
- Respects LOG_LEVEL environment variable
- Accepts CLI parameter override
- Suppresses verbose Azure SDK logging
"""

import logging
from typing import Optional

from app.settings import get_settings


def setup_logging(log_level: Optional[str] = None) -> logging.Logger:
    """
    Configure application logging with hierarchical precedence.
    
    Precedence (highest to lowest):
    1. CLI parameter (log_level argument)
    2. LOG_LEVEL environment variable
    3. Config file (app_config.yaml)
    4. Default (INFO)
    
    Args:
        log_level: Optional log level override from CLI parameter.
                  Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL
    
    Returns:
        Configured root logger
    """
    # Determine effective log level with precedence
    effective_level = _get_effective_log_level(log_level)
    
    # Convert string to logging constant
    numeric_level = getattr(logging, effective_level.upper(), logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        force=True  # Override any existing configuration
    )
    
    # Suppress verbose Azure SDK logging
    _configure_azure_loggers()
    
    logger = logging.getLogger()
    logger.debug(f"Logging configured at {effective_level} level (source: {_get_level_source(log_level)})")
    
    return logger


def _get_effective_log_level(cli_override: Optional[str]) -> str:
    """
    Determine effective log level from CLI or config.
    
    Args:
        cli_override: Optional CLI parameter value
    
    Returns:
        Effective log level as uppercase string
    """
    # 1. CLI parameter has highest priority
    if cli_override:
        return cli_override.upper()

    # 2. Config file
    try:
        settings = get_settings()
        config_level = settings.get_log_level()
        if config_level:
            return config_level.upper()
    except Exception:
        pass  # Fall through to default
    
    # 3. Default
    return "INFO"


def _get_level_source(cli_override: Optional[str]) -> str:
    """Get human-readable source of log level for debug message."""
    if cli_override:
        return "CLI parameter"
    try:
        settings = get_settings()
        if settings.get_log_level():
            return "config file"
    except Exception:
        pass
    return "default"


def _configure_azure_loggers() -> None:
    """Suppress verbose logging from Azure SDK libraries."""
    azure_loggers = [
        'azure',
        'azure.core',
        'azure.core.pipeline',
        'azure.core.pipeline.policies',
        'azure.identity',
        'azure.mgmt',
        'msal',
        'urllib3'
    ]
    
    for logger_name in azure_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for the specified module.
    
    Args:
        name: Logger name, typically __name__ from the calling module
    
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
