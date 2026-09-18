"""Getting a typed judgment into the workflow — three interchangeable sources.

The workflow does not care where the judgment came from. Swapping the source
is a one-line change, which is what makes deterministic replay possible:

    JevSource()       -> a live call to TypeSafe's System One model
    RecordedSource()  -> replays a previously recorded call (no network)
    StubSource()      -> a fixed answer for tests

All three return the SAME validated `Judgment` object. If Jev returns
something that does not validate, we do not silently coerce it — this module
raises `JudgmentError`, and both drivers turn that into a recorded
`FAILED_VALIDATION` terminal rather than a route.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from .state import Intervention, Judgment

# --------------------------------------------------------------------------
# The question we ask. Keep it narrow: one coherent judgment per question.
# --------------------------------------------------------------------------

INSTRUCTIONS = (
    "Which single intervention does the supplied business assessment provide "
    "explicit evidence for?"
)
CRITERIA = {
    Intervention.REVIEW_FOLLOW_UP.value: (
        "The evidence shows inconsistent customer review requests after completed "
        "or paid work."
    ),
    Intervention.PAYMENT_FOLLOW_UP.value: (
        "The evidence shows overdue or unpaid invoices are the primary operational problem."
    ),
    Intervention.NOT_A_FIT.value: (
        "The evidence does not support either intervention, or explicitly says the "
        "current process works."
    ),
}
GAP_INSTRUCTIONS = (
    "Does the assessment explicitly evidence a review-request gap after completed work?"
)

TYPESAFE_MODEL = "jev-latest"


class JudgmentError(RuntimeError):
    """The judgment source failed or returned something unusable."""


class JudgmentSource(Protocol):
    name: str

    def judge(self, assessment: str) -> Judgment: ...


# --------------------------------------------------------------------------
# Live: TypeSafe System One (Jev)
# --------------------------------------------------------------------------


@dataclass
class JevSource:
    """Live Jev call. Credentials are read from the environment by the SDK."""

    model: str = TYPESAFE_MODEL
    timeout: float = 30.0
    name: str = field(default="jev", init=False)
    last_usage: dict[str, int] = field(default_factory=dict, init=False)

    def judge(self, assessment: str) -> Judgment:
        try:
            from typesafe_sdk import Choice, Noul, TypeSafeClient
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise JudgmentError("typesafe-sdk is not installed") from exc

        try:
            with TypeSafeClient(timeout=self.timeout) as client:
                response = client.system_one(
                    state={"assessment": assessment},
                    questions={
                        "intervention": Choice(
                            instructions=INSTRUCTIONS, criteria=CRITERIA
                        ),
                        "review_gap": Noul(instructions=GAP_INSTRUCTIONS),
                    },
                    model=self.model,
                )
        except Exception as exc:  # SDK error types vary across versions
            raise JudgmentError(f"TypeSafe call failed: {exc}") from exc

        choice = response.answers["intervention"]
        noul = response.answers["review_gap"]
        self.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return _build(choice.choice, choice.confidence, noul.noul, source="jev")


# --------------------------------------------------------------------------
# Replay: a recorded live call (deterministic, offline)
# --------------------------------------------------------------------------


@dataclass
class RecordedSource:
    """Replays a recording. Same interface, zero network, byte-identical result."""

    recording_path: Path
    name: str = field(default="recorded", init=False)

    def judge(self, assessment: str) -> Judgment:
        if not self.recording_path.is_file():
            raise JudgmentError(f"no recording at {self.recording_path}")
        payload = json.loads(self.recording_path.read_text(encoding="utf-8"))
        if payload.get("assessment_sha") and payload["assessment_sha"] != _sha(assessment):
            raise JudgmentError("recording was made against a different assessment")
        return _build(
            payload["intervention"],
            float(payload["confidence"]),
            float(payload["review_gap"]),
            source="recorded",
        )


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class StubSource:
    """Fixed answer for tests. Makes failures reproducible on purpose."""

    intervention: Intervention = Intervention.REVIEW_FOLLOW_UP
    confidence: float = 0.99
    review_gap: float = 0.97
    name: str = field(default="stub", init=False)

    def judge(self, assessment: str) -> Judgment:  # noqa: ARG002 - interface symmetry
        return _build(
            self.intervention.value,
            self.confidence,
            self.review_gap,
            source="stub",
        )


def _build(raw_choice: str, confidence: float, review_gap: float, *, source: str) -> Judgment:
    """Validate before the workflow ever sees it."""
    try:
        return Judgment(
            intervention=Intervention(raw_choice),
            confidence=confidence,
            review_gap_evidenced=review_gap,
            source=source,
        )
    except (ValidationError, ValueError) as exc:
        raise JudgmentError(f"judgment failed validation: {exc}") from exc


def source_from_env() -> JudgmentSource:
    """`AGENT_LAB_JUDGMENT=jev|recorded|stub` — explicit, never guessed."""
    selected = os.getenv("AGENT_LAB_JUDGMENT", "stub").strip()
    recording = Path(
        os.getenv("AGENT_LAB_RECORDING", "recordings/northside-garden-care.json")
    )
    if selected == "jev":
        return JevSource()
    if selected == "recorded":
        return RecordedSource(recording_path=recording)
    if selected == "stub":
        return StubSource()
    raise JudgmentError(f"unknown AGENT_LAB_JUDGMENT={selected!r}")
