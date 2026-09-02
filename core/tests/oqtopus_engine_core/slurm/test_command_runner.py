import sys

import pytest

from oqtopus_engine_core.slurm import CommandRunner


@pytest.mark.asyncio
async def test_command_runner_passes_arguments_without_shell_expansion():
    result = await CommandRunner().run(
        (sys.executable, "-c", "import sys; print(sys.argv[1])", "$(unsafe)"),
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout == "$(unsafe)\n"
    assert result.stderr == ""