import fcntl
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def update_json[T](
    path: Path,
    factory: Callable[[], dict[str, Any]],
    update: Callable[[dict[str, Any]], T],
) -> T:
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else factory()
            )
            result = update(state)
            temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with temporary_path.open("w", encoding="utf-8") as stream:
                json.dump(state, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(path)
            return result
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def replace_json(path: Path, value: dict[str, Any]) -> None:
    def replace(state: dict[str, Any]) -> None:
        state.clear()
        state.update(value)

    update_json(path, dict, replace)
