"""Guard the invariant that job_repository_update_step is the first step
of every pipeline in every shipped config file.

steps/job_repository_update_step.py is where a job's terminal status
transition is reported to the job repository, and its fallback-to-failed
logic (see JobRepositoryUpdateStep.post_process) is the last line of defense
that guarantees a job always reaches a terminal repository status.

That guarantee only holds for jobs that are actually tracked in the
repository. Internal jobs created mid-pipeline (e.g. estimation sub-circuits,
auto-combined buffer jobs) are designed to stop at a join or split before
ever reaching index 0 again - see the pipeline step ordering discussion in
the PR-2 handoff notes. This is a property of *where in the list*
job_repository_update_step sits, not something enforced by the framework
itself: reordering a pipeline's steps, or inserting a step before it, would
silently break the invariant. This test catches that.
"""

from pathlib import Path
from typing import Any

import pytest
from oqtopus_util.config import load_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
# Only files that define pipeline_manager.pipelines (excludes logging.yaml /
# sse_engine_logging.yaml, which have no such key).
CONFIG_FILES = sorted(CONFIG_DIR.glob("*config.yaml"))


def _load_pipelines(config_path: Path) -> list[dict[str, Any]]:
    # Use the same loader the app uses (oqtopus_util.config.load_config),
    # since these files rely on its ${VAR, default} substitution syntax,
    # which plain yaml.safe_load cannot parse (e.g. grpc_options below).
    config = load_config(str(config_path))
    return config["pipeline_manager"]["pipelines"]


@pytest.mark.parametrize("config_path", CONFIG_FILES, ids=lambda p: p.name)
def test_job_repository_update_step_is_first_in_every_pipeline(
    config_path: Path,
) -> None:
    pipelines = _load_pipelines(config_path)
    assert pipelines, f"no pipelines found in {config_path.name}"

    for pipeline in pipelines:
        steps = pipeline["steps"]
        assert steps, f"pipeline {pipeline['name']!r} in {config_path.name} has no steps"
        assert steps[0] == "job_repository_update_step", (
            f"pipeline {pipeline['name']!r} in {config_path.name} must start with "
            f"'job_repository_update_step' (found {steps[0]!r} instead); jobs "
            "that never reach index 0 skip the terminal-status fallback in "
            "JobRepositoryUpdateStep.post_process"
        )
