"""``--speak``: read questions, verdicts and rationales aloud (post-submission, FINAL_SPRINT §C).

The CLI prints everything exactly as before; with ``--speak`` it additionally speaks each
``InputRequest`` question with its default, the P10 summary with the verifier verdict, the DR-11
rationale and, for ``whatif``, the before → after verdicts with the record's rationale. Speech
uses the macOS ``say`` command through ``subprocess``; where ``say`` is not on the PATH (Linux,
Windows, CI) the speaker is a no-op that prints one note. Nothing is spoken that was not printed,
and no number is computed here (the texts are the CLI's own strings).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any, TextIO

from gemini_agent.schemas.decision import InputRequest

SAY_COMMAND = "say"
"""macOS text-to-speech command (``say "text"``)."""
SAY_TIMEOUT_S = 120.0
"""Upper bound per utterance so a stuck speech daemon never blocks the run."""
UNAVAILABLE_NOTE = (
    "--speak: the '{command}' command is not available on this system; speech is disabled "
    "(output is printed as usual)."
)


class Speaker:
    """Speak short texts with the ``say`` command; a no-op (with one note) where it is absent.

    ``enabled`` is the ``--speak`` flag. ``available`` is resolved once from the PATH. Every text
    is also kept in ``spoken`` so callers and tests can see what was (or would have been) said.
    """

    def __init__(
        self,
        enabled: bool = False,
        *,
        command: str = SAY_COMMAND,
        out: TextIO | None = None,
    ) -> None:
        """``out`` receives the one-time unavailability note (default stdout)."""
        self.enabled = enabled
        self.command = command
        self.out = out
        self.available = bool(enabled) and shutil.which(command) is not None
        self.spoken: list[str] = []
        self._noted = False

    def say(self, text: str) -> bool:
        """Speak ``text``; return True when the command ran (exit status 0)."""
        text = " ".join(str(text).split())
        if not self.enabled or not text:
            return False
        self.spoken.append(text)
        if not self.available:
            if not self._noted:
                self._noted = True
                print(UNAVAILABLE_NOTE.format(command=self.command), file=self.out or sys.stdout)
            return False
        try:
            # "--" ends the options so a text starting with "-" is never read as a flag.
            proc = subprocess.run(
                [self.command, "--", text],
                check=False,
                timeout=SAY_TIMEOUT_S,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"--speak: {type(exc).__name__}: {exc}", file=self.out or sys.stdout)
            return False
        return proc.returncode == 0


def request_text(request: InputRequest) -> str:
    """Spoken form of an ``InputRequest``: the question, then its default (and unit)."""
    default = request.default
    shown = json.dumps(default) if isinstance(default, (dict, list)) else str(default)
    unit = f" {request.unit}" if request.unit else ""
    return f"{request.question} The default is {shown}{unit}."


def speaking_ask(speaker: Speaker, ask_fn: Callable[[InputRequest], Any]) -> Callable[..., Any]:
    """Wrap an ``ask_fn`` so each question is spoken (with its default) before it is asked."""

    def ask(request: InputRequest) -> Any:
        speaker.say(request_text(request))
        return ask_fn(request)

    return ask


__all__ = ["SAY_COMMAND", "Speaker", "request_text", "speaking_ask"]
