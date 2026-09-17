"""Lesson 02 — the workflow.

These tests are the contract for the plain state machine AND prove that the
pydantic-graph driver produces identical outcomes.

    .venv/bin/python -m pytest lessons/lesson_02_workflow/test_workflow.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_lab.approvals import ApprovalStore
from agent_lab.judgment import Intervention, JudgmentError, StubSource
from agent_lab.runlog import RunLog
from agent_lab.state import (
    Budget,
    IllegalTransition,
    NotResumable,
    RunState,
    Stage,
    Terminal,
    Transition,
)
from agent_lab.workflow import Deps, artifact_digest, run_plain


@pytest.fixture
def deps(tmp_path: Path):
    def build(
        *,
        tag: str = "run",
        intervention: Intervention = Intervention.REVIEW_FOLLOW_UP,
        confidence: float = 0.99,
    ) -> Deps:
        return Deps(
            judgment=StubSource(intervention=intervention, confidence=confidence),
            approvals=ApprovalStore(tmp_path / f"{tag}-approvals.jsonl"),
            log=RunLog(tmp_path / f"{tag}.jsonl"),
        )

    return build


def start(run_id: str = "run", **kwargs) -> RunState:
    return RunState(run_id=run_id, assessment_text="synthetic assessment", **kwargs)


# --------------------------------------------------------------------------
# Terminals are reachable, and each means something specific.
# --------------------------------------------------------------------------


def test_low_confidence_pauses_for_review(deps) -> None:
    out = run_plain(start(), deps(confidence=0.42))
    assert out.terminal is Terminal.NEEDS_REVIEW
    assert out.stage is Stage.ROUTE


def test_no_evidenced_fit_is_rejected(deps) -> None:
    out = run_plain(start(), deps(intervention=Intervention.NOT_A_FIT))
    assert out.terminal is Terminal.REJECTED


def test_budget_cap_fires_instead_of_looping_forever(deps) -> None:
    out = run_plain(start(budget=Budget(max_steps=3)), deps())
    assert out.terminal is Terminal.FAILED_BUDGET
    assert out.budget.used_steps == 3


def test_missing_input_fails_before_any_model_call(deps, tmp_path) -> None:
    with pytest.raises(ValueError):
        run_plain(RunState(run_id="empty", assessment_text="   "), deps())


# --------------------------------------------------------------------------
# The approval gate is the whole point: exact artifact, exact digest.
# --------------------------------------------------------------------------


def test_run_pauses_without_approval_and_records_the_digest(deps) -> None:
    d = deps(tag="pause")
    first = run_plain(start("pause"), d)
    assert first.terminal is Terminal.NEEDS_REVIEW
    assert first.stage is Stage.APPROVE
    assert first.draft_digest, "a digest must exist so a human can approve THIS version"


def test_approval_of_the_exact_digest_completes_the_run(deps) -> None:
    d = deps(tag="approve")
    first = run_plain(start("approve"), d)
    d.approvals.record(run_id="approve", draft_digest=first.draft_digest, approved_by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.SUCCESS
    assert second.approval_token == "adam"


def test_approval_of_a_different_digest_does_not_unlock(deps) -> None:
    """The failure this guards: approval attached to 'a draft', not THIS draft."""
    d = deps(tag="tamper")
    first = run_plain(start("tamper"), d)
    other = artifact_digest(1, "invoice follow-up", "a completely different body")
    assert other != first.draft_digest
    d.approvals.record(run_id="tamper", draft_digest=other, approved_by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.NEEDS_REVIEW
    assert second.approval_token is None


def test_approval_from_a_different_run_does_not_unlock(deps) -> None:
    """Same draft text, different run: approval is bound to the run too."""
    d = deps(tag="crossrun")
    first = run_plain(start("crossrun"), d)
    d.approvals.record(run_id="SOME-OTHER-RUN", draft_digest=first.draft_digest, approved_by="adam")

    assert run_plain(first.resume(), d, entry=Stage.APPROVE).terminal is Terminal.NEEDS_REVIEW


# --------------------------------------------------------------------------
# Pause vs final: only a pause may be resumed.
# --------------------------------------------------------------------------


def test_only_pausable_terminals_resume() -> None:
    assert RunState(run_id="x", terminal=Terminal.NEEDS_REVIEW).resume().terminal is None
    for final in (Terminal.REJECTED, Terminal.FAILED_VALIDATION, Terminal.FAILED_BUDGET):
        with pytest.raises(NotResumable):
            RunState(run_id="x", terminal=final).resume()


def test_illegal_transitions_are_refused() -> None:
    """A router bug must raise, not silently produce a plausible-looking run."""
    state = RunState(run_id="x")  # stage=INTAKE
    with pytest.raises(IllegalTransition):
        state.moved(Transition.TO_PREPARE)


# --------------------------------------------------------------------------
# Evidence: the run log records what actually happened.
# --------------------------------------------------------------------------


def test_run_log_records_every_transition_in_order(deps) -> None:
    d = deps(tag="log")
    run_plain(start("log"), d)
    events = d.log.read()

    assert [e.seq for e in events] == list(range(len(events)))
    assert [e.node for e in events] == [
        "intake",
        "classify",
        "route",
        "prepare",
        "verify",
        "await_approval",
    ]
    assert events[-1].terminal == Terminal.NEEDS_REVIEW.value
    assert d.log.digest(), "the log must be digestable as evidence"


def test_rejecting_leaves_no_draft(deps) -> None:
    d = deps(tag="reject")
    out = run_plain(start("reject"), deps(intervention=Intervention.NOT_A_FIT))
    assert out.draft is None and out.draft_digest is None


# --------------------------------------------------------------------------
# Framework equivalence: the reason both implementations exist.
# --------------------------------------------------------------------------


def test_graph_driver_matches_plain_driver(deps) -> None:
    from agent_lab.graph_workflow import run_graph

    plain = run_plain(start("cmp"), deps(tag="cmp-plain"))
    graph = run_graph(start("cmp"), deps(tag="cmp-graph"))

    assert plain.terminal == graph.terminal
    assert plain.stage == graph.stage
    assert plain.draft == graph.draft
    assert plain.draft_digest == graph.draft_digest
    assert plain.events == graph.events


def test_graph_driver_stops_on_a_terminal(deps) -> None:
    from agent_lab.graph_workflow import run_graph

    out = run_graph(start("lowc"), deps(tag="lowc-graph", confidence=0.42))
    assert out.terminal is Terminal.NEEDS_REVIEW
    assert out.stage is Stage.ROUTE


def test_graph_and_plain_agree_after_approval(deps) -> None:
    from agent_lab.graph_workflow import run_graph

    d_plain, d_graph = deps(tag="app-plain"), deps(tag="app-graph")
    p1 = run_plain(start("app"), d_plain)
    g1 = run_graph(start("app"), d_graph)
    assert p1.draft_digest == g1.draft_digest, "identical drafts must hash identically"

    for store_deps, state in ((d_plain, p1), (d_graph, g1)):
        store_deps.approvals.record(
            run_id="app", draft_digest=state.draft_digest, approved_by="adam"
        )

    assert (
        run_plain(p1.resume(), d_plain, entry=Stage.APPROVE).terminal
        is run_graph(g1.resume(), d_graph, entry=Stage.APPROVE).terminal
        is Terminal.SUCCESS
    )
