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
from types import MappingProxyType
from typing import Callable, Mapping

from .approvals import ApprovalStore, Decision, DecisionRecord
from .encoding import bind_digest, canonical_digest
from .judgment import JudgmentError, JudgmentSource
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
    """Pause point. Nothing happens unless an exact-digest APPROVAL exists.

    The gate re-derives the digest from the draft it was handed, and never trusts
    `state.draft_digest`. Trusting it is how an edited draft inherits an approval
    issued for the version before the edit: the field is a convenience for humans
    reading a log, not evidence.
    """
    if not state.draft or state.judgment is None:
        raise ValueError("approval gate reached without a prepared draft")

    recomputed = bind_digest(
        state.run_id, state.judgment.intervention.value, state.draft
    )
    if recomputed != state.draft_digest:
        return state, Transition.FAIL_APPROVAL_DIGEST, {
            "problems": [
                "draft digest does not match the digest presented for approval"
            ]
        }

    record: DecisionRecord | None = deps.approvals.latest_decision(
        run_id=state.run_id, draft_digest=recomputed
    )
    if record is None:
        # Terminal for this pass, not for the run: a decision may arrive later.
        return state, Transition.PAUSE_APPROVAL, {
            "reason": "awaiting exact-draft approval",
            "digest": recomputed,
        }

    outcome = {
        "decision": record.decision.value,
        "decided_by": record.decided_by,
        "decided_at": record.decided_at,
        "digest": recomputed,
    }

    if record.decision is Decision.REJECTED:
        # A rejection is a decision too, and it must stop the run. Treating any
        # stored record as consent is what let a rejection unlock the gate.
        return state, Transition.REJECT_APPROVAL, outcome

    return (
        state.model_copy(update={"approval_token": record.decided_by}),
        Transition.TO_DONE,
        outcome,
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

NODE_BY_NAME: Mapping[str, NodeFn] = MappingProxyType(dict(NODES))


# --------------------------------------------------------------------------
# The step, shared by both drivers so their behaviour cannot drift.
# --------------------------------------------------------------------------


def next_seq(state: RunState, deps: Deps) -> int:
    """One past the highest `seq` already recorded for THIS run in THIS log.

    Counting only this run's events means a resume continues the same run's
    numbering, and unrelated runs sharing a log file cannot shift it.
    """
    recorded = [event.seq for event in deps.log.read() if event.run_id == state.run_id]
    return max(recorded) + 1 if recorded else 0


def _append_event(
    deps: Deps,
    *,
    state: RunState,
    node: str,
    seq: int,
    stage: Stage,
    transition: str | None,
    detail: dict,
) -> None:
    deps.log.append(
        RunEvent(
            run_id=state.run_id,
            seq=seq,
            node=node,
            stage=stage.value,
            transition=transition,
            terminal=state.terminal.value if state.terminal else None,
            detail=detail,
        )
    )


def step(state: RunState, deps: Deps, node_name: str, seq: int) -> RunState:
    """One transition: spend budget, run the node, apply the move, record it.

    This is the ONLY place budget is spent, a move is applied, or evidence is
    written. Both drivers call it, so a run's accounting and audit trail cannot
    depend on which driver executed it.
    """
    try:
        state = state.model_copy(update={"budget": state.budget.spend()})
    except BudgetExceeded as exc:
        spent = state.moved(Transition.ESCALATE_BUDGET)
        _append_event(
            deps,
            state=spent,
            node=node_name,
            seq=seq,
            stage=state.stage,
            transition=Transition.ESCALATE_BUDGET.value,
            detail={"error": str(exc)},
        )
        return spent

    node_fn = NODE_BY_NAME[node_name]
    stage_before = state.stage

    try:
        proposed, transition, detail = node_fn(state, deps)
    except JudgmentError as exc:
        # A judgment that will not validate is an EXPECTED failure at the
        # validation boundary, so it becomes a recorded terminal outcome instead
        # of an exception escaping the workflow.
        failed = state.moved(Transition.FAIL_VALIDATION)
        _append_event(
            deps,
            state=failed,
            node=node_name,
            seq=seq,
            stage=stage_before,
            transition=Transition.FAIL_VALIDATION.value,
            detail={"error": str(exc)},
        )
        return failed

    try:
        moved = proposed.moved(transition)
    except IllegalTransition as exc:
        # A node asked for a move its stage forbids. That is a router bug, so it
        # is recorded before it propagates.
        _append_event(
            deps,
            state=proposed,
            node=node_name,
            seq=seq,
            stage=stage_before,
            transition=transition.value,
            detail={"illegal_transition": str(exc)},
        )
        raise

    _append_event(
        deps,
        state=moved,
        node=node_name,
        seq=seq,
        stage=stage_before,
        transition=transition.value,
        detail=detail,
    )
    return moved


def run_plain(state: RunState, deps: Deps, *, entry: Stage | None = None) -> RunState:
    """Explicit state machine. No framework, no hidden behaviour.

    `entry` names the STAGE to resume from, matching `graph_workflow.run_graph`,
    so the two drivers have the same call shape.
    """
    node_name = NEXT_NODE[entry or state.stage]
    seq = next_seq(state, deps)

    while state.terminal is None:
        state = step(state, deps, node_name, seq)
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
