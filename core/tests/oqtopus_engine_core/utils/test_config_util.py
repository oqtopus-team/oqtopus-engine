import os
import tempfile
import textwrap
from pathlib import Path

import pytest

from oqtopus_engine_core.utils.config_util import load_config

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_config_file():
    """
    Fixture that creates a temporary YAML file for each test.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.yaml"
        yield path


def write_config(path: Path, content: str):
    """
    Helper to write YAML content to a temporary file.
    """
    path.write_text(textwrap.dedent(content), encoding="utf-8")

# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_var_with_default_when_env_missing(temp_config_file):
    """
    When ${VAR, default} is used and the environment variable is not set,
    YAML should type-cast the default value correctly.
    """
    write_config(
        temp_config_file,
        """
        timeout: ${TIMEOUT, 10}
        debug: ${DEBUG, false}
        """
    )

    # Ensure env is not present
    os.environ.pop("TIMEOUT", None)
    os.environ.pop("DEBUG", None)

    cfg = load_config(temp_config_file)

    assert cfg["timeout"] == 10          # YAML int
    assert cfg["debug"] is False         # YAML bool


def test_var_with_default_when_env_present(temp_config_file):
    """
    When ${VAR, default} is used and the environment variable exists,
    the env value should override the default and YAML should cast it.
    """
    write_config(
        temp_config_file,
        """
        timeout: ${TIMEOUT, 10}
        debug: ${DEBUG, false}
        """
    )

    os.environ["TIMEOUT"] = "20"
    os.environ["DEBUG"] = "true"

    cfg = load_config(temp_config_file)

    assert cfg["timeout"] == 20          # cast by YAML
    assert cfg["debug"] is True          # cast by YAML


def test_var_without_default_env_missing(temp_config_file):
    """
    When ${VAR} is used without a default and VAR is missing,
    the loader should substitute an empty string ("").
    """
    write_config(
        temp_config_file,
        """
        host: ${HOST}
        """
    )

    os.environ.pop("HOST", None)

    cfg = load_config(temp_config_file)

    assert cfg["host"] == ""             # empty string as designed


def test_var_without_default_env_present(temp_config_file):
    """
    When ${VAR} is used without a default and VAR is present,
    YAML should receive the raw string and parse it as-is.
    """
    write_config(
        temp_config_file,
        """
        host: ${HOST}
        """
    )

    os.environ["HOST"] = "example.com"

    cfg = load_config(temp_config_file)

    assert cfg["host"] == "example.com"


def test_default_type_casting_complex(temp_config_file):
    """
    Ensure YAML can type-cast more complex default patterns as well.
    """
    write_config(
        temp_config_file,
        """
        ratio: ${RATIO, 0.75}
        flag: ${FLAG, true}
        items: ${ITEMS, [1, 2, 3]}
        """
    )

    os.environ.pop("RATIO", None)
    os.environ.pop("FLAG", None)
    os.environ.pop("ITEMS", None)

    cfg = load_config(temp_config_file)

    assert cfg["ratio"] == 0.75
    assert cfg["flag"] is True
    assert cfg["items"] == [1, 2, 3]     # YAML loads a Python list


def test_mixed_env_and_default(temp_config_file):
    """
    Mixed case: some values come from env, others from default.
    """
    write_config(
        temp_config_file,
        """
        timeout: ${TIMEOUT, 10}
        host: ${HOST}
        debug: ${DEBUG, false}
        """
    )

    os.environ["TIMEOUT"] = "50"
    os.environ.pop("HOST", None)
    os.environ["DEBUG"] = "false"

    cfg = load_config(temp_config_file)

    assert cfg["timeout"] == 50
    assert cfg["host"] == ""
    assert cfg["debug"] is False
