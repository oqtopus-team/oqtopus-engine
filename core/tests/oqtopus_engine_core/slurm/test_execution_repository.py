from pathlib import Path

import pytest

from oqtopus_engine_core.slurm import SingleProcessLock


def test_single_process_lock_rejects_second_owner(tmp_path: Path) -> None:
    first = SingleProcessLock(tmp_path / "engine.lock")
    second = SingleProcessLock(tmp_path / "engine.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another simulator engine"):
            second.acquire()
    finally:
        first.release()


def test_single_process_lock_expands_home_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    lock = SingleProcessLock("~/.state/engine.lock")

    lock.acquire()
    lock.release()

    assert (tmp_path / ".state" / "engine.lock").is_file()
