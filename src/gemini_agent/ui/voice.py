"""Push-to-talk voice front-end for the design workflow (post-submission, FINAL_SPRINT §D).

``cli talk`` drives the same ``DesignRunner`` as ``cli run --input-mode ask``; only the terminal
is replaced by a microphone and a loudspeaker:

* **Listening** is push-to-talk: Enter starts the recording, Enter stops it
  (:class:`Microphone`, ``sounddevice``), and the clip is transcribed offline by
  ``faster-whisper`` (:class:`WhisperTranscriber`, model ``small``, CPU int8). Both packages are
  optional (``requirements-voice.txt``), imported lazily, and a missing one fails with a clear
  message (:class:`VoiceDependencyError`). ``--typed`` replaces the microphone by the keyboard.
* **Speaking** reuses ``gemini_agent.speech.Speaker`` (macOS ``say``; elsewhere a no-op with a
  note). Everything that is spoken is also printed.
* **Understanding** an answer is split in two deterministic halves around one LLM call:
  :func:`answer_spec` derives from the ``InputRequest`` what the runner will accept (yes/no,
  one of a few options, a number with a unit, free text; mirrored from
  ``DesignRunner._coerce_answer``), and the normaliser maps the transcript into that form.
  :class:`LLMNormaliser` makes exactly one ``NativeOutput`` call per attempt, validated against
  the spec, logged through ``LLMLogger`` (MEM-04) and falling back to the keyword-based
  :class:`PolicyNormaliser` on failure (AG-03); ``--engine policy`` uses the policy normaliser
  only. The understood value is echoed ("I understood 65 degC — apply? [Y/n]") and confirmed on
  the keyboard before it reaches the runner, which validates it exactly as a typed answer
  (``InvalidAnswer`` → the question is asked again).

Nothing here computes an engineering number (golden rule 1): the normaliser only rewrites what
the user said ("sixty-five" → "65"), the runner keeps every limit check, the ``DesignGuard`` and
``DesignVerifier`` are untouched, and the P10 approval is still the user's yes/no. The spoken P10
text is the verifier's own stored summary (``ui.run_view.run_verifier``) plus the runner's
question.

The post-run dialogue (FINAL_SPRINT §I, ``gemini_agent.ui.dialogue``: what-if by voice) plugs
into :attr:`VoiceSession.dialogue_hook`; :data:`DIALOGUE_DECISION_ID` is its one question form
(adopt / another / stop) the normalisers understand.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

from pydantic import BaseModel, Field

from gemini_agent.logging.llm_logger import LLMCallRecord, LLMLogger
from gemini_agent.runner.design_runner import (
    APPROVAL_ID,
    BASIS_CONFIRM_ID,
    INPUT_DEFAULTS_ID,
    RunResult,
)
from gemini_agent.schemas.common import StrictModel
from gemini_agent.schemas.config import LLMConfig
from gemini_agent.schemas.decision import InputRequest
from gemini_agent.speech import Speaker, request_text
from gemini_agent.ui import run_view

# ----------------------------------------------------------------------------- dependencies

VOICE_REQUIREMENTS = "requirements-voice.txt"
"""Pin file of the optional voice packages (never installed by the main CI)."""
STT_MODEL = "small"
"""faster-whisper model size: a good accuracy/speed trade-off on a laptop CPU (≈ 0.5 GB)."""
STT_MODEL_DOWNLOAD_GB = 0.5
MISSING_DEPENDENCY = (
    "voice: the '{module}' package is not installed (optional voice extra). On the laptop run\n"
    "  uv pip install -r {requirements}\n"
    "(faster-whisper downloads the '{model}' model, about {size:g} GB, on first use). "
    "Without a microphone use `talk --typed`."
)
NORMALISER_AGENT = "voice_normaliser"
"""``agent`` field of the normaliser's records in ``llm_calls.jsonl``."""
DIALOGUE_DECISION_ID = "DIALOGUE-DECISION"
"""Request id of the dialogue's "Adopt it, try another, or stop?" question (``ui.dialogue``)."""
DIALOGUE_DECISIONS: tuple[str, ...] = ("adopt", "another", "stop")
"""Its three answers, in the runner-free form the dialogue acts on."""


class VoiceDependencyError(RuntimeError):
    """An optional voice package (``faster-whisper``, ``sounddevice``) is missing."""


def import_voice_module(module: str) -> Any:
    """Import an optional voice package, or raise :class:`VoiceDependencyError` with the fix."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise VoiceDependencyError(
            MISSING_DEPENDENCY.format(
                module=module,
                requirements=VOICE_REQUIREMENTS,
                model=STT_MODEL,
                size=STT_MODEL_DOWNLOAD_GB,
            )
        ) from exc


# ----------------------------------------------------------------------------- audio in

SAMPLE_RATE_HZ = 16_000
"""Whisper's native sample rate; recording at it avoids a resampling step."""
InputFn = Callable[[str], str]
"""``input``-like prompt function. Defaults are resolved at call time (``None`` → the built-in
``input``) so tests can script the keyboard by monkeypatching ``builtins.input``."""


class Microphone:
    """Push-to-talk recorder: Enter starts, Enter stops; returns float32 mono samples.

    ``input_fn`` is ``input`` (tests pass a scripted one); ``out`` receives the prompts.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        input_fn: InputFn | None = None,
        out: TextIO | None = None,
    ) -> None:
        """Nothing is imported or opened until :meth:`record`."""
        self.sample_rate_hz = sample_rate_hz
        self.input_fn = input_fn
        self.out = out

    def _wait(self, prompt: str) -> bool:
        print(prompt, end="", flush=True, file=self.out or sys.stdout)
        try:
            (self.input_fn or input)("")
        except EOFError:
            print(file=self.out or sys.stdout)
            return False
        return True

    def record(self) -> Any:
        """Record one clip (``numpy.ndarray`` float32, shape ``(n,)``; empty on EOF)."""
        sounddevice = import_voice_module("sounddevice")
        numpy = import_voice_module("numpy")
        chunks: list[Any] = []

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            chunks.append(numpy.array(indata, dtype=numpy.float32, copy=True))

        if not self._wait("  [voice] press Enter to start recording ... "):
            return numpy.zeros(0, dtype=numpy.float32)
        with sounddevice.InputStream(
            samplerate=self.sample_rate_hz, channels=1, dtype="float32", callback=callback
        ):
            self._wait("  [voice] recording - press Enter to stop ... ")
        if not chunks:
            return numpy.zeros(0, dtype=numpy.float32)
        samples = numpy.concatenate(chunks)
        return samples[:, 0] if samples.ndim > 1 else samples


class WhisperTranscriber:
    """Offline speech-to-text with ``faster-whisper`` (CTranslate2, CPU int8).

    The model is loaded on the first :meth:`transcribe` (first run downloads it, ≈ 0.5 GB for
    ``small``, into the Hugging Face cache). CTranslate2 has no Metal backend; on Apple silicon
    the ``small`` model runs on the CPU in a few seconds per utterance.
    """

    def __init__(
        self,
        model_size: str = STT_MODEL,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = "en",
        beam_size: int = 5,
    ) -> None:
        """Store the settings; nothing is loaded yet."""
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self._model: Any = None

    def load(self) -> Any:
        """Load (and on first use download) the model; return it."""
        if self._model is None:
            whisper = import_voice_module("faster_whisper")
            self._model = whisper.WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, audio: Any) -> str:
        """Text of one clip ('' for an empty clip)."""
        if audio is None or len(audio) == 0:
            return ""
        model = self.load()
        segments, _info = model.transcribe(
            audio, language=self.language, beam_size=self.beam_size, vad_filter=True
        )
        return " ".join(str(segment.text).strip() for segment in segments).strip()


def whisper_listener(
    model_size: str = STT_MODEL, *, input_fn: InputFn | None = None, out: TextIO | None = None
) -> Callable[[], str]:
    """``listen()`` = push-to-talk recording + faster-whisper transcription."""
    microphone = Microphone(input_fn=input_fn, out=out)
    transcriber = WhisperTranscriber(model_size)

    def listen() -> str:
        return transcriber.transcribe(microphone.record())

    return listen


def typed_listener(input_fn: InputFn | None = None) -> Callable[[], str]:
    """``--typed``: the keyboard stands in for the microphone (no optional package needed)."""

    def listen() -> str:
        try:
            return (input_fn or input)("  [typed] your answer: ")
        except EOFError:
            return ""

    return listen


# ----------------------------------------------------------------------------- answer spec

AnswerKind = Literal["yes_no", "choice", "number", "text"]


class AnswerSpec(StrictModel):
    """What the runner accepts for one ``InputRequest`` (mirror of ``_coerce_answer``)."""

    kind: AnswerKind
    options: tuple[str, ...] = ()
    unit: str | None = None
    default_text: str | None = Field(
        default=None, description="Answer that applies when the user asks for the default."
    )

    def describe(self) -> str:
        """Short spoken/printed hint of the expected answer form."""
        if self.kind == "yes_no":
            return "yes or no"
        if self.kind == "choice":
            return "one of " + ", ".join(self.options)
        if self.kind == "number":
            return "a number" + (f" in {self.unit}" if self.unit else "")
        return "a short text"


def answer_spec(request: InputRequest) -> AnswerSpec:
    """Derive the expected answer form of ``request`` (branches of ``_coerce_answer``).

    The approval has no usable default (``pending`` is not an answer), so "the default" is
    rejected there and the user must say yes or no.
    """
    default = request.default
    if request.request_id == APPROVAL_ID:
        return AnswerSpec(kind="yes_no", options=("yes", "no"))
    if request.request_id == DIALOGUE_DECISION_ID:  # no default: nothing is adopted by silence
        return AnswerSpec(kind="choice", options=DIALOGUE_DECISIONS)
    if request.assumption_id == BASIS_CONFIRM_ID:
        return AnswerSpec(kind="choice", options=("keep", "change"), default_text="keep")
    if request.assumption_id == INPUT_DEFAULTS_ID:
        return AnswerSpec(kind="choice", options=("defaults", "yes"), default_text="defaults")
    if isinstance(default, int | float) and not isinstance(default, bool):
        return AnswerSpec(kind="number", unit=request.unit, default_text=f"{default:g}")
    if isinstance(default, dict) and "interpretation" in default:
        return AnswerSpec(
            kind="choice", options=("A", "B"), default_text=str(default["interpretation"])
        )
    shown = default if isinstance(default, str) else json.dumps(default)
    return AnswerSpec(kind="text", unit=request.unit, default_text=shown)


_NUMBER = re.compile(r"(?<![\w-])[-+]?\d+(?:[.,]\d+)?")
"""First numeric token; a sign glued to a word is not one ('DB-31 is 65' → 65)."""
_WORD_PREFIXES = re.compile(
    r"^(?:option|interpretation|letter|choice|answer|the|number|value|it is|it s|i say|i d say|"
    r"i want|i choose|choose|pick|use|take|go with|say|please|so|well|um|uh)\b\s*"
)
_PUNCTUATION = re.compile(r"[^\w\s.,+-]")
_YES_WORDS = frozenset({"yes", "yeah", "yep", "yup", "approve", "approved", "ok", "okay", "sure"})
_NO_WORDS = frozenset({"no", "nope", "reject", "rejected", "negative", "disapprove"})
_DEFAULT_WORDS = frozenset({"default", "defaults"})
_NEGATION_WORDS = frozenset({"not", "don", "dont", "never", "cancel", "decline", "neither", "nor"})
"""A negation next to a yes-word ('do not approve', "don't approve") is never read as yes:
the answer is unclear and asked again (reviewer finding; Enter on the echo would approve)."""


def canonical_value(spec: AnswerSpec, value: str) -> str:
    """Return ``value`` in the runner's form or raise ``ValueError`` (used by both normalisers).

    yes/no words → ``yes``/``no``; an option is matched case-insensitively and returned as
    listed; a number is the first numeric token (``,`` decimal accepted) and must be finite;
    free text is stripped.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty answer")
    if spec.kind == "yes_no":
        words = {word.strip(".,+-") for word in _PUNCTUATION.sub(" ", text.lower()).split()}
        if words & _NEGATION_WORDS and words & _YES_WORDS:
            raise ValueError(f"'{text}' sounds negated; say yes or no")
        if words & _YES_WORDS and not words & _NO_WORDS:
            return "yes"
        if words & _NO_WORDS and not words & _YES_WORDS:
            return "no"
        raise ValueError(f"'{text}' is not yes or no")
    if spec.kind == "choice":
        lowered = text.lower().strip(" .,!?")
        for option in spec.options:
            if lowered == option.lower():
                return option
        raise ValueError(f"'{text}' is not one of {', '.join(spec.options)}")
    if spec.kind == "number":
        match = _NUMBER.search(text)
        if match is None:
            raise ValueError(f"'{text}' holds no number")
        number = float(match.group(0).replace(",", "."))
        if not math.isfinite(number):
            raise ValueError(f"'{text}' is not a finite number")
        return f"{number:g}"
    return text


class NormalisedAnswer(StrictModel):
    """Structured output of the normaliser (the LLM's NativeOutput schema)."""

    heard: str = Field(description="The transcript exactly as given.")
    interpretation: Literal["value", "default", "unclear"] = Field(
        description="'value' when the transcript holds an answer of the expected form, "
        "'default' when the user asked for the default, 'unclear' otherwise. Never guess."
    )
    value: str | None = Field(
        default=None,
        description="For 'value': the answer in the expected form only (digits for a number, "
        "the option as listed, 'yes' or 'no'); otherwise null.",
    )


def check_normalised(spec: AnswerSpec, output: NormalisedAnswer) -> str | None:
    """Return the validation error of ``output`` against ``spec`` (None when acceptable)."""
    if output.interpretation == "default":
        if spec.default_text is None:
            return "this question has no default: answer with 'value' yes or no, or 'unclear'"
        return None
    if output.interpretation == "unclear":
        return None
    if output.value is None:
        return "interpretation 'value' needs a value"
    try:
        canonical_value(spec, output.value)
    except ValueError as exc:
        return f"{exc}; the expected answer is {spec.describe()}"
    return None


# ----------------------------------------------------------------------------- normalisers


class Normaliser(Protocol):
    """Map a transcript to the runner's answer form (one of the implementations below)."""

    name: str

    def normalise(self, request: InputRequest, transcript: str) -> NormalisedAnswer:
        """Return the understood answer; ``interpretation='unclear'`` when nothing matched."""
        ...


class PolicyNormaliser:
    """Deterministic keyword normaliser (``--engine policy``; also the LLM fallback, AG-03).

    Rules: a transcript that is (or ends with) a default word asks for the default; yes/no
    words decide a yes/no question; a leading filler ("option", "I'd say", "the" …) is removed
    and the rest must equal an option for a choice (so the article "a" never selects option A);
    the first numeric token answers a number; free text is kept.
    """

    name = "policy"

    def normalise(self, request: InputRequest, transcript: str) -> NormalisedAnswer:
        """Keyword mapping; never raises."""
        spec = answer_spec(request)
        heard = transcript.strip()
        cleaned = _PUNCTUATION.sub(" ", heard.lower()).strip().rstrip(".,")
        words = [word.strip(".,") for word in cleaned.split()]
        if (
            spec.default_text is not None
            and words
            and not set(words) & _NEGATION_WORDS
            and (words[-1] in _DEFAULT_WORDS or (len(words) <= 3 and set(words) & _DEFAULT_WORDS))
        ):
            return NormalisedAnswer(heard=heard, interpretation="default")
        candidate = cleaned
        for _ in range(4):  # strip stacked fillers: "I'd say option A"
            stripped = _WORD_PREFIXES.sub("", candidate).strip()
            if stripped == candidate:
                break
            candidate = stripped
        for attempt in (candidate, heard):
            try:
                value = canonical_value(spec, attempt)
            except ValueError:
                continue
            return NormalisedAnswer(heard=heard, interpretation="value", value=value)
        return NormalisedAnswer(heard=heard, interpretation="unclear")


NORMALISER_INSTRUCTIONS = (
    "You are the ear of an engineering assistant. A user answered a question by voice; the "
    "transcript may contain filler words, spelled-out numbers or units. Return the answer in "
    "exactly the expected form: a number as digits (copy the number the user said, never "
    "convert units or compute anything), an option exactly as listed, or yes/no. If the user "
    "asks for the default (e.g. 'keep the default', 'whatever the default is') return "
    "interpretation 'default'. If the transcript does not clearly hold an answer of the "
    "expected form return 'unclear'. Never guess a value the user did not say."
)
NORMALISER_REQUEST_TOKENS = 4096
"""Upper bound on tokens of one normaliser call (prompt + replies incl. retries); the prompt is
a few hundred tokens and the reply a few dozen, so this only stops a runaway model."""


class NormaliserFailure(RuntimeError):
    """The LLM normaliser produced no valid output (retries exhausted, timeout, HTTP error)."""


def normaliser_prompt(request: InputRequest, spec: AnswerSpec, transcript: str) -> str:
    """User prompt of one normaliser call (question, expected form, transcript)."""
    expected: dict[str, Any] = {"kind": spec.kind}
    if spec.options:
        expected["options"] = list(spec.options)
    if spec.unit:
        expected["unit"] = spec.unit
    if spec.default_text is not None:
        expected["default"] = spec.default_text
    return (
        f"QUESTION: {request.question}\n"
        f"EXPECTED: {json.dumps(expected)}\n"
        f"TRANSCRIPT: {transcript}"
    )


@dataclass
class LLMNormaliser:
    """One ``NativeOutput`` LLM call per transcript, validated, logged, policy fallback.

    ``model`` is the Pydantic AI model (the CLI shares the runner's Ollama model so the HTTP
    client is closed once; tests pass a ``FunctionModel``). Every call, including failures, is
    written to the run's ``llm_calls.jsonl`` with ``agent`` :data:`NORMALISER_AGENT` and the
    ``decision_source`` ``llm`` or ``policy_fallback`` (MEM-04). ``fallbacks`` counts the
    transcripts the policy normaliser had to handle.
    """

    llm_config: LLMConfig
    model: Any
    logger: LLMLogger
    run_id: str = ""
    fallback: PolicyNormaliser = field(default_factory=PolicyNormaliser)
    name: str = "llm"
    fallbacks: int = 0
    calls: int = 0
    _agent: Any = field(default=None, repr=False)

    def _get_agent(self) -> Any:
        from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext

        if self._agent is not None:
            return self._agent
        agent: Agent[AnswerSpec, NormalisedAnswer] = Agent(
            output_type=NativeOutput(NormalisedAnswer),
            instructions=NORMALISER_INSTRUCTIONS,
            deps_type=AnswerSpec,
            retries=self.llm_config.max_output_retries,
            name=NORMALISER_AGENT,
        )

        @agent.output_validator
        def _validate(ctx: RunContext[AnswerSpec], output: NormalisedAnswer) -> NormalisedAnswer:
            error = check_normalised(ctx.deps, output)
            if error:
                raise ModelRetry(error)
            return output

        self._agent = agent
        return agent

    def _settings(self) -> dict[str, Any]:
        from gemini_agent.agents import engine

        return engine.model_settings(
            self.llm_config, temperature=self.llm_config.temperatures.extraction
        )

    def _log(
        self,
        request: InputRequest,
        messages: Any,
        output: BaseModel | None,
        usage: Any,
        start: float,
        source: str,
        error: str | None,
    ) -> None:
        record = LLMCallRecord.from_messages(
            run_id=self.run_id,
            agent=NORMALISER_AGENT,
            phase=request.phase.value,
            model=str(getattr(self.model, "model_name", type(self.model).__name__)),
            messages=list(messages),
            output=output,
            usage=usage,
            latency_s=time.perf_counter() - start,
            decision_source=source,
            error=error,
        )
        self.logger.log(record)

    def _call(self, request: InputRequest, spec: AnswerSpec, transcript: str) -> NormalisedAnswer:
        from pydantic_ai import UsageLimits, capture_run_messages

        agent = self._get_agent()
        limits = UsageLimits(
            request_limit=self.llm_config.max_output_retries + 2,
            total_tokens_limit=NORMALISER_REQUEST_TOKENS,
        )
        start = time.perf_counter()
        self.calls += 1
        with capture_run_messages() as messages:
            try:
                result = agent.run_sync(
                    normaliser_prompt(request, spec, transcript),
                    model=self.model,
                    deps=spec,
                    usage_limits=limits,
                    model_settings=self._settings(),
                )
            except Exception as exc:  # any LLM failure falls back to the policy (AG-03)
                error = f"{type(exc).__name__}: {exc}"
                self._log(request, messages, None, None, start, "policy_fallback", error)
                raise NormaliserFailure(error) from exc
        output: NormalisedAnswer = result.output
        self._log(request, result.all_messages(), output, result.usage, start, "llm", None)
        return output

    def normalise(self, request: InputRequest, transcript: str) -> NormalisedAnswer:
        """LLM first; on failure the policy normaliser decides (counted in ``fallbacks``)."""
        spec = answer_spec(request)
        try:
            return self._call(request, spec, transcript)
        except NormaliserFailure:
            self.fallbacks += 1
            return self.fallback.normalise(request, transcript)


# ----------------------------------------------------------------------------- the session

ConfirmFn = Callable[[str], bool]
DialogueHook = Callable[[RunResult], Any]
MAX_ATTEMPTS = 3
"""Recordings per question before the runner's default applies (as an empty typed answer)."""
REQUEST_PROMPT = (
    "Describe the design request. Press Enter, speak, and press Enter again when you are done."
)
NOTHING_HEARD = "I did not hear anything. Let's try again."
APPROVAL_SUFFIX = "Say yes to approve or no to reject."


def confirm_terminal(question: str, input_fn: InputFn | None = None) -> bool:
    """``<question> [Y/n]`` on the keyboard; Enter or anything but n/no means yes."""
    try:
        answer = (input_fn or input)(f"{question} [Y/n] ")
    except EOFError:
        return False
    return answer.strip().lower() not in {"n", "no"}


@dataclass
class VoiceSession:
    """The talk loop's state: how to listen, speak, understand and confirm.

    ``listen`` returns one transcript (microphone + STT, or the keyboard with ``--typed``);
    ``confirm`` is the keyboard ``[Y/n]`` check; ``run_dir`` lets the P10 approval speak the
    verifier's stored summary. :meth:`ask` is the runner's ``ask_fn``. ``transcripts`` and the
    speaker's ``spoken`` list record the dialogue for tests and the hand-off.
    """

    speaker: Speaker
    listen: Callable[[], str]
    normaliser: Normaliser
    confirm: ConfirmFn = confirm_terminal
    out: TextIO | None = None
    run_dir: Path | None = None
    max_attempts: int = MAX_ATTEMPTS
    transcripts: list[str] = field(default_factory=list)
    answers: list[tuple[str, str | None]] = field(default_factory=list)
    dialogue_hook: DialogueHook | None = None
    """FINAL_SPRINT §I hook: called once with the ``RunResult`` after the run and its report,
    while the runner (and the Ollama model shared with ``normaliser``) is still open, so the hook
    may make LLM calls. ``cli talk`` installs ``ui.dialogue.DialogueLoop.run`` here (speak "What
    would you like to change?" → record → lever mapping → what-if → adopt / try another / stop);
    its return value is kept by the loop object, not by this session."""

    def say(self, text: str) -> None:
        """Print ``text`` and speak it (nothing is spoken that is not printed)."""
        print(text, file=self.out or sys.stdout)
        self.speaker.say(text)

    def hear(self) -> str:
        """One transcript (whitespace-normalised, '' when nothing was heard), recorded."""
        heard = " ".join(str(self.listen() or "").split())
        if heard:
            self.transcripts.append(heard)
        return heard

    def request_prompt(self) -> str | None:
        """Record the design request (step a) and confirm its transcript; None if given up."""
        self.say(REQUEST_PROMPT)
        for _ in range(self.max_attempts):
            heard = self.hear()
            if not heard:
                self.say(NOTHING_HEARD)
                continue
            print(f'I heard: "{heard}"', file=self.out or sys.stdout)
            if self.confirm("use it?"):
                return heard
            self.say("Let's record the request again.")
        return None

    def question_text(self, request: InputRequest) -> str:
        """Spoken form of a question; the P10 approval adds the verifier's stored summary."""
        if request.request_id != APPROVAL_ID:
            return request_text(request)
        summary = ""
        if self.run_dir is not None:
            try:
                report = run_view.run_verifier(self.run_dir)
            except (OSError, ValueError):  # unreadable store or result: the question suffices
                report = None
            if report and report.get("summary"):
                summary = f"Verifier summary: {report['summary']} "
        return f"{summary}{request.question} {APPROVAL_SUFFIX}"

    def ask(self, request: InputRequest) -> str | None:
        """(b)/(c) The runner's ``ask_fn``: speak, listen, normalise, echo, confirm.

        Returns the confirmed answer text (the runner validates it) or None after
        ``max_attempts`` unusable recordings, which the runner treats like Enter in ask mode:
        the register default applies and is recorded as an assumption.
        """
        spec = answer_spec(request)
        default = request.default
        shown = json.dumps(default) if isinstance(default, dict) else default
        unit = f" {request.unit}" if request.unit else ""
        print(
            f"\n[{request.phase.value}] {request.question}\n  default: {shown}{unit}"
            f"\n  expected: {spec.describe()}",
            file=self.out or sys.stdout,
        )
        self.speaker.say(self.question_text(request))
        for _ in range(self.max_attempts):
            heard = self.hear()
            if not heard:
                self.say(NOTHING_HEARD)
                continue
            understood = self.normaliser.normalise(request, heard)
            if understood.interpretation == "unclear":
                self.say(f'I heard "{heard}" but expected {spec.describe()}. Please repeat.')
                continue
            if understood.interpretation == "default":
                value = spec.default_text
                if value is None:  # defensive: check_normalised rejects this for the approval
                    self.say(f"This question has no default; please answer {spec.describe()}.")
                    continue
            else:
                try:  # the runner's form ("65 degrees" → "65", "Yes." → "yes"), never the raw text
                    value = canonical_value(spec, str(understood.value))
                except ValueError as exc:
                    self.say(f"I could not use that answer ({exc}). Please repeat.")
                    continue
            echo_unit = f" {spec.unit}" if spec.kind == "number" and spec.unit else ""
            print(f'I heard: "{heard}"', file=self.out or sys.stdout)
            if self.confirm(f"I understood {value}{echo_unit} - apply?"):
                self.answers.append((request.request_id, value))
                return value
            self.say("Let's try again.")
        self.say("No answer was confirmed; the default applies.")
        self.answers.append((request.request_id, None))
        return None

    def after_run(self, result: RunResult) -> None:
        """Run the post-run dialogue hook (§I) when one is installed; otherwise nothing."""
        if self.dialogue_hook is not None:
            self.dialogue_hook(result)


__all__ = [
    "DIALOGUE_DECISIONS",
    "DIALOGUE_DECISION_ID",
    "AnswerSpec",
    "LLMNormaliser",
    "Microphone",
    "NormalisedAnswer",
    "Normaliser",
    "PolicyNormaliser",
    "VoiceDependencyError",
    "VoiceSession",
    "WhisperTranscriber",
    "answer_spec",
    "canonical_value",
    "check_normalised",
    "confirm_terminal",
    "import_voice_module",
    "typed_listener",
    "whisper_listener",
]
