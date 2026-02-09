import logging.config
import os
from pathlib import Path
from typing import Any

from hydra import compose, initialize
from omegaconf import OmegaConf

SENSITIVE_KEYS = {"api_token", "password", "secret_key"}


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


def load_config(config_path: str) -> dict[str, Any]:
    """Load a configuration file using Hydra.

    Args:
        config_path: Relative path from the application base directory,
            including the file name with extension (e.g., 'config/config.yaml').

    Returns:
        The loaded configuration.

    Raises:
        FileNotFoundError: If the config file does not exist.

    """
    # check if the config file exists
    path = Path(config_path)
    if not path.is_file():
        msg = f"Config file not found: {path.resolve()}"
        raise FileNotFoundError(msg)

    # get the directory of this file and the full path of the config file
    this_file_dir = Path(__file__).parent.resolve()
    full_config_path = (Path.cwd() / config_path).parent.resolve()
    relative_config_path = os.path.relpath(full_config_path, start=this_file_dir)

    # load the config file using Hydra
    with initialize(version_base=None, config_path=relative_config_path):
        config_omega = compose(config_name=Path(config_path).stem)
        return OmegaConf.to_container(config_omega, resolve=True)
