"""Typed state, transitions and terminal states for the lab workflow.

The whole point of this module: *code owns control flow*. A model may supply
data that a router reads, but every allowed transition is declared here.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Base model used everywhere: no extra keys, immutable once built."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# The judgment a model is allowed to produce. It is DATA, not control flow.
# --------------------------------------------------------------------------


class Intervention(str, Enum):
    REVIEW_FOLLOW_UP = "review_follow_up"
    PAYMENT_FOLLOW_UP = "payment_follow_up"
    NOT_A_FIT = "not_a_fit"


class Judgment(Strict):
    """One narrow, typed decision about a business assessment."""

    intervention: Intervention
    confidence: float = Field(ge=0.0, le=1.0)
    review_gap_evidenced: float = Field(ge=0.0, le=1.0)
    source: Literal["jev", "recorded", "stub"]


# --------------------------------------------------------------------------
# Control flow: every edge the runtime is permitted to take.
# --------------------------------------------------------------------------


class Stage(str, Enum):
    INTAKE = "intake"
    CLASSIFY = "classify"
    ROUTE = "route"
    PREPARE = "prepare"
    VERIFY = "verify"
    APPROVE = "awaiting_approval"
    DONE = "done"


class Terminal(str, Enum):
    SUCCESS = "SUCCESS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_BUDGET = "FAILED_BUDGET"
    REJECTED = "REJECTED"


#: Terminals that mean "this pass stopped" rather than "this run is over".
#: Only these may be cleared to continue a later pass.
RESUMABLE: frozenset[Terminal] = frozenset({Terminal.NEEDS_REVIEW})


class NotResumable(RuntimeError):
    pass


class Transition(str, Enum):
    """Allow-list of legal moves. The router refuses anything not listed."""

    TO_CLASSIFY = "intake->classify"
    TO_ROUTE = "classify->route"
    TO_PREPARE = "route->prepare"
    TO_VERIFY = "prepare->verify"
    TO_APPROVE = "verify->awaiting_approval"
    TO_DONE = "awaiting_approval->done"
    ESCALATE_REVIEW = "route->needs_review"
    ESCALATE_VALIDATION = "verify->failed_validation"
    ESCALATE_BUDGET = "any->failed_budget"
    REJECT = "route->rejected"


ALLOWED: dict[Transition, tuple[Stage | None, Stage | None]] = {
    Transition.TO_CLASSIFY: (Stage.INTAKE, Stage.CLASSIFY),
    Transition.TO_ROUTE: (Stage.CLASSIFY, Stage.ROUTE),
    Transition.TO_PREPARE: (Stage.ROUTE, Stage.PREPARE),
    Transition.TO_VERIFY: (Stage.PREPARE, Stage.VERIFY),
    Transition.TO_APPROVE: (Stage.VERIFY, Stage.APPROVE),
    Transition.TO_DONE: (Stage.APPROVE, Stage.DONE),
    Transition.ESCALATE_REVIEW: (None, None),
    Transition.ESCALATE_VALIDATION: (None, None),
    Transition.ESCALATE_BUDGET: (None, None),
    Transition.REJECT: (None, None),
}


class Budget(Strict):
    """Hard caps. A loop with no cap is a bug, not a workflow."""

    max_steps: int = 12
    used_steps: int = 0

    def spend(self) -> "Budget":
        if self.used_steps >= self.max_steps:
            raise BudgetExceeded(f"step budget of {self.max_steps} exhausted")
        return Budget(max_steps=self.max_steps, used_steps=self.used_steps + 1)


class BudgetExceeded(RuntimeError):
    pass


class RunState(Strict):
    """The typed state object passed between nodes."""

    run_id: str
    stage: Stage = Stage.INTAKE
    terminal: Terminal | None = None
    budget: Budget = Budget()
    assessment_text: str = ""
    judgment: Judgment | None = None
    draft: str | None = None
    draft_digest: str | None = None
    approval_token: str | None = None
    notes: tuple[str, ...] = ()
    events: tuple[str, ...] = ()

    def resume(self) -> "RunState":
        """Continue a paused pass. Only a resumable terminal may be cleared.

        This is the difference between `NEEDS_REVIEW` (a pause: an approval may
        still arrive) and `FAILED_VALIDATION` / `REJECTED` (this run is over).
        """
        if self.terminal is None:
            return self
        if self.terminal not in RESUMABLE:
            raise NotResumable(
                f"{self.terminal.value} is a final terminal; a new run is required"
            )
        return self.model_copy(update={"terminal": None})

    def moved(self, transition: Transition) -> "RunState":
        """Return a NEW state moved along a legal transition, or raise."""
        if transition is Transition.ESCALATE_BUDGET:
            return self.model_copy(
                update={
                    "terminal": Terminal.FAILED_BUDGET,
                    "events": self.events + (transition.value,),
                }
            )

        source, target = ALLOWED[transition]

        escalations = {
            Transition.ESCALATE_REVIEW: Terminal.NEEDS_REVIEW,
            Transition.ESCALATE_VALIDATION: Terminal.FAILED_VALIDATION,
            Transition.REJECT: Terminal.REJECTED,
        }
        if transition in escalations:
            if source is not None and self.stage is not source:
                raise IllegalTransition(f"{transition.value} not legal from {self.stage.value}")
            return self.model_copy(
                update={
                    "terminal": escalations[transition],
                    "events": self.events + (transition.value,),
                }
            )

        if self.stage is not source:
            raise IllegalTransition(f"{transition.value} not legal from {self.stage.value}")
        terminal = Terminal.SUCCESS if target is Stage.DONE else None
        return self.model_copy(
            update={
                "stage": target,
                "terminal": terminal,
                "events": self.events + (transition.value,),
            }
        )


class IllegalTransition(RuntimeError):
    pass
