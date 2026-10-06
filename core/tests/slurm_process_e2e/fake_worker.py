import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary_path.replace(path)


def main() -> None:
    request_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    release_path = Path(os.environ["FAKE_SLURM_WORKER_RELEASE"])
    while not release_path.exists():
        time.sleep(0.02)

    request = json.loads(request_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "succeeded",
        "job_type": request["job_type"],
        "duration_seconds": 0.01,
    }
    if request["job_type"] == "sampling":
        width = len(request["measurement_mapping"])
        result["counts"] = {"0" * width: request["shots"]}
    else:
        result["exp_value"] = [1.0, 0.0]
    _write_atomic(result_path, result)


if __name__ == "__main__":
    main()
