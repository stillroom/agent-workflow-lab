"""The workflow, written twice on purpose.

`run_plain()` is a hand-written explicit state machine: a loop, a router, an
allow-list of transitions, hard caps, and terminal states. Nothing is hidden.

`graph_workflow.py` ports the SAME node functions to pydantic-graph. Both must
produce identical events and digests — that is the acceptance test.

Read this file first. The framework version is short precisely because the
semantics already live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .approvals import Approval, ApprovalStore
from .encoding import bind_digest, canonical_digest
from .judgment import JudgmentSource
from .runlog import RunEvent, RunLog
from .state import (
    BudgetExceeded,
    IllegalTransition,
    Intervention,
    RunState,
    Stage,
    Terminal,
    Transition,
)

NodeFn = Callable[[RunState, "Deps"], tuple[RunState, Transition, dict]]


@dataclass
class Deps:
    """Everything a node may touch. Passed in — never imported globally."""

    judgment: JudgmentSource
    approvals: ApprovalStore
    log: RunLog


# --------------------------------------------------------------------------
# Nodes. Each one does one job and declares the single transition it may take.
# --------------------------------------------------------------------------


def n_intake(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Validate the typed input. Nothing else happens until this passes."""
    problems: list[str] = []
    if not state.assessment_text.strip():
        problems.append("assessment_text is empty")
    if len(state.assessment_text) > 20_000:
        problems.append("assessment_text exceeds 20000 characters")
    if problems:
        raise ValueError("; ".join(problems))
    return state, Transition.TO_CLASSIFY, {"chars": len(state.assessment_text)}


def n_classify(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Call the judgment source, validate, and record usage."""
    if not state.assessment_text.strip():
        raise ValueError("assessment text is empty; nothing to classify")

    judgment = deps.judgment.judge(state.assessment_text)
    usage = getattr(deps.judgment, "last_usage", {}) or {}
    next_state = state.model_copy(update={"judgment": judgment})
    detail = {
        "source": judgment.source,
        "intervention": judgment.intervention.value,
        "confidence": judgment.confidence,
        "review_gap": judgment.review_gap_evidenced,
        "usage": usage,
    }
    return next_state, Transition.TO_ROUTE, detail


def n_route(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Deterministic router. Code decides — the model only supplied data."""
    assert state.judgment is not None, "router reached without a judgment"
    judgment = state.judgment

    if judgment.intervention is Intervention.NOT_A_FIT:
        return state, Transition.REJECT, {"reason": "no evidenced fit"}

    # Thresholds live in code, are visible, and are evaluated on YOUR data.
    if judgment.confidence < 0.60:
        return state, Transition.ESCALATE_REVIEW, {
            "reason": "low confidence",
            "confidence": judgment.confidence,
        }

    return state, Transition.TO_PREPARE, {"intervention": judgment.intervention.value}


def n_prepare(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Deterministically compose the artifact. No model writes this text."""
    assert state.judgment is not None
    intervention = state.judgment.intervention

    if intervention is Intervention.REVIEW_FOLLOW_UP:
        opening = "Follow up every completed job with a review request."
    else:
        opening = "Follow up overdue invoices on a fixed weekly rhythm."

    draft = (
        f"{opening}\n"
        "Steps:\n"
        "1. Trigger on the existing completed-or-paid event.\n"
        "2. Prepare the message; do not send automatically during the trial.\n"
        "3. Record who was contacted and suppress anyone who already responded.\n"
        "If the business owner has not approved the wording, do nothing.\n"
    )
    digest = bind_digest(state.run_id, intervention.value, draft)
    return (
        state.model_copy(update={"draft": draft, "draft_digest": digest}),
        Transition.TO_VERIFY,
        {"digest": digest, "chars": len(draft)},
    )


def n_verify(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Independent check of the prepared artifact. Fails closed."""
    if not state.draft or not state.draft_digest:
        raise ValueError("verify reached without a prepared draft")

    recomputed = bind_digest(state.run_id, state.judgment.intervention.value, state.draft)
    problems: list[str] = []
    if recomputed != state.draft_digest:
        problems.append("digest mismatch")
    if "automatically" in state.draft and "do not send automatically" not in state.draft:
        problems.append("draft implies automatic sending")
    if not state.draft.endswith("\n"):
        problems.append("draft must end with a newline for stable hashing")
    if "\r" in state.draft:
        problems.append("draft contains CR; LF only")

    if problems:
        return state, Transition.ESCALATE_VALIDATION, {"problems": problems}

    return (
        state,
        Transition.TO_APPROVE,
        {"digest": recomputed, "problems": []},
    )


def n_await_approval(state: RunState, deps: Deps) -> tuple[RunState, Transition, dict]:
    """Pause point. Nothing happens unless an exact-digest approval exists."""
    if not state.draft_digest:
        raise ValueError("approval gate reached without a digest")

    approval: Approval | None = deps.approvals.find(
        run_id=state.run_id, draft_digest=state.draft_digest
    )
    if approval is None:
        # Terminal for this pass, not for the run: the approval may arrive later.
        return state, Transition.ESCALATE_REVIEW, {
            "reason": "awaiting exact-draft approval",
            "digest": state.draft_digest,
        }

    return (
        state.model_copy(update={"approval_token": approval.approved_by}),
        Transition.TO_DONE,
        {"approved_by": approval.approved_by, "approved_at": approval.approved_at},
    )


NODES: tuple[tuple[str, NodeFn], ...] = (
    ("intake", n_intake),
    ("classify", n_classify),
    ("route", n_route),
    ("prepare", n_prepare),
    ("verify", n_verify),
    ("await_approval", n_await_approval),
)

NEXT_NODE = {
    Stage.INTAKE: "intake",
    Stage.CLASSIFY: "classify",
    Stage.ROUTE: "route",
    Stage.PREPARE: "prepare",
    Stage.VERIFY: "verify",
    Stage.APPROVE: "await_approval",
}


def run_plain(state: RunState, deps: Deps, *, entry: Stage | None = None) -> RunState:
    """Explicit state machine. No framework, no hidden behaviour.

    `entry` names the STAGE to resume from, matching `graph_workflow.run_graph`,
    so the two drivers have the same call shape.
    """
    node_name = NEXT_NODE[entry or state.stage]
    seq = len(deps.log.read())

    while True:
        if state.terminal is not None:
            break
        try:
            state = state.model_copy(update={"budget": state.budget.spend()})
        except BudgetExceeded as exc:
            state = state.moved(Transition.ESCALATE_BUDGET)
            deps.log.append(
                RunEvent(
                    run_id=state.run_id,
                    seq=seq,
                    node=node_name,
                    stage=state.stage.value,
                    transition=Transition.ESCALATE_BUDGET.value,
                    terminal=state.terminal.value if state.terminal else None,
                    detail={"error": str(exc)},
                )
            )
            break

        node_fn = dict(NODES)[node_name]
        try:
            state, transition, detail = node_fn(state, deps)
        except IllegalTransition as exc:  # pragma: no cover - defensive
            deps.log.append(
                RunEvent(
                    run_id=state.run_id,
                    seq=seq,
                    node=node_name,
                    stage=state.stage.value,
                    transition=None,
                    terminal=None,
                    detail={"illegal_transition": str(exc)},
                )
            )
            raise

        before = state.stage
        state = state.moved(transition)
        deps.log.append(
            RunEvent(
                run_id=state.run_id,
                seq=seq,
                node=node_name,
                stage=before.value,
                transition=transition.value,
                terminal=state.terminal.value if state.terminal else None,
                detail=detail,
            )
        )
        seq += 1

        if state.terminal is not None:
            break
        node_name = NEXT_NODE.get(state.stage)
        if node_name is None:
            break

    return state


def artifact_digest(contact_id: int, subject: str, body: str) -> str:
    """Re-exported so lessons and apps share one binding rule."""
    return canonical_digest(contact_id, subject, body)
