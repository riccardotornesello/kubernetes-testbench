"""
Execution of the external commands (kind, k3d, minikube, cilium, liqoctl, ...).

The output of the commands is shown (on stdout) only with `--log-level DEBUG`.
Otherwise it is captured and, if the command fails, its last lines are
included in the raised exception, so that the failure can be diagnosed.
"""

import logging
import shlex
import subprocess
from collections.abc import Mapping, Sequence

from core.log import get_logger

log = get_logger(__name__)

# Lines of the captured output included in the error of a failed command
ERROR_OUTPUT_LINES = 40


class CommandError(subprocess.CalledProcessError):
    """A command exited with a non-zero status. Includes its output tail."""

    def __str__(self) -> str:
        message = (
            f"Command '{shlex.join(self.cmd)}' failed with exit status "
            f"{self.returncode}"
        )
        output = "\n".join(
            part.rstrip() for part in (self.stdout, self.stderr) if part
        ).strip()
        if output:
            lines = output.splitlines()[-ERROR_OUTPUT_LINES:]
            message += ":\n" + "\n".join(f"  {line}" for line in lines)
        return message


def _show_output() -> bool:
    """Whether the command output is shown (DEBUG level)."""
    return logging.getLogger().isEnabledFor(logging.DEBUG)


def run_command(
    args: Sequence[str],
    *,
    capture: bool = False,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Runs `args`, raising CommandError if it fails.

    capture=True: stdout is returned (`result.stdout`), never shown.
    capture=False: stdout and stderr are shown with `--log-level DEBUG`,
    captured otherwise (and included in the error if the command fails).
    """
    args = list(args)
    log.debug("Running command", command=shlex.join(args))

    if capture:
        streams = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    elif _show_output():
        streams = {}
    else:
        # stderr merged into stdout, to keep the order of the messages
        streams = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}

    result = subprocess.run(args, input=input, env=env, text=True, **streams)
    if result.returncode != 0:
        raise CommandError(result.returncode, args, result.stdout, result.stderr)
    return result
