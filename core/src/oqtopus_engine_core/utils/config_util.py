import logging.config
import os
import re
from pathlib import Path
from typing import Any

import yaml

SENSITIVE_KEYS = {"api_token", "password", "secret_key"}

# Pattern matching ${VAR} and ${VAR, default}
_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?:,\s*([^}]+))?\}")


def mask_sensitive_info(config: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive information in the given DictConfig.

    This function replaces the values of predefined sensitive keys
    (e.g., "api_token", "password", "secret_key") with a masked string.
    It processes nested DictConfig recursively.

    Args:
        config: DictConfig to mask.

    Returns:
        A new dictionary with sensitive values masked.

    """
    masked: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            # Recursively mask nested dictionaries
            masked[key] = mask_sensitive_info(value)
        elif key in SENSITIVE_KEYS:
            masked[key] = "***MASKED***"
        else:
            masked[key] = value
    return masked


def setup_logging(logging_cfg: dict) -> None:
    """Set up logging configuration from a dict.

    Args:
        logging_cfg: The logging configuration to apply.

    Raises:
        TypeError: If the configuration cannot be converted to a dictionary.

    """
    if not isinstance(logging_cfg, dict):
        msg = (
            f"Logging configuration must be convertible to a dict, but got "
            f"{type(logging_cfg).__name__}."
        )
        raise TypeError(msg)
    logging.config.dictConfig(logging_cfg)


def _replace_env(match: re.Match) -> str:
    """Replace ${VAR} or ${VAR, default} with the appropriate string.

    Internal callback used by re.sub before YAML parsing.

    Rules:
      - If the environment variable VAR exists → substitute its value (raw string)
      - If default is provided → substitute it directly (YAML will type-cast it)
      - If no default is provided → substitute empty string ""

    Args:
        match: The regex match object containing the variable name and optional default.

    Returns:
        The string to substitute in place of the matched pattern.

    """
    var_name = match.group(1)
    default_raw = match.group(2)

    # Environment variable exists → use it directly
    if var_name in os.environ:
        return os.environ[var_name]

    # No env → default exists
    if default_raw is not None:
        return default_raw

    # No env and no default → empty string
    return '""'  # Valid YAML string literal


def load_config(config_path: str) -> dict[str, Any]:
    """Load a YAML configuration file.

    Supported syntax:
        ${VAR}              → If VAR not set, becomes "" (empty string)
        ${VAR, default}     → If VAR not set, "default" is inserted literally,
                              and then YAML type-casts it.

    Behavior:
      1. Read the entire YAML file as a raw string.
      2. Perform string-level substitution for ${...} patterns.
      3. Pass the substituted string to PyYAML, allowing YAML to determine types.

    This design ensures:
      - No custom smart-casting logic is needed.
      - Default values are type-cast by YAML (10→int, false→bool, etc.).
      - Environment variable values are applied before YAML parsing.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Parsed configuration as a Python dict with correct types.

    """
    # Step 1: Load file as raw text
    with Path(config_path).open(encoding="utf-8") as f:
        raw_text = f.read()

    # Step 2: Replace ${VAR} / ${VAR, default} before YAML parsing
    replaced_text = _PATTERN.sub(_replace_env, raw_text)

    # Step 3: Let YAML parse and type-cast the final text
    return yaml.safe_load(replaced_text) or {}
