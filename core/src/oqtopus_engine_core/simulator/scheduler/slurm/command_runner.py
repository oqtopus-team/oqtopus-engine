import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# ruff: noqa: DOC201, DOC501


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured result of an argv-based subprocess invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandTimeoutError(TimeoutError):
    """Raised after a subprocess exceeds its configured timeout."""


class CommandRunner:
    """Run external commands without invoking a shell."""

    @staticmethod
    async def run(
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute argv and capture decoded stdout and stderr."""
        if not argv:
            message = "argv must not be empty"
            raise ValueError(message)

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            message = f"command timed out after {timeout_seconds} seconds: {argv[0]}"
            raise CommandTimeoutError(message) from exc

        returncode = process.returncode
        if returncode is None:
            message = f"command did not report an exit status: {argv[0]}"
            raise RuntimeError(message)
        return CommandResult(
            argv=tuple(argv),
            returncode=returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
