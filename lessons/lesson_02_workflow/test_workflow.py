"""Lesson 02 — the workflow.

These tests are the contract for the plain state machine AND prove that the
pydantic-graph driver produces identical outcomes.

    .venv/bin/python -m pytest lessons/lesson_02_workflow/test_workflow.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_lab.approvals import ApprovalStore, Decision, UndecidedRecord
from agent_lab.encoding import bind_digest
from agent_lab.judgment import Intervention, JudgmentError, StubSource
from agent_lab.runlog import RunLog
from agent_lab.state import (
    ALLOWED,
    TERMINATES,
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


class _Unvalidatable:
    """A judgment source whose answer does not survive validation.

    Stands in for a model that returns an option we never defined. The lab's rule
    is that this becomes a terminal failure state rather than a route, so it must
    be testable in both drivers.
    """

    name = "unvalidatable"
    last_usage: dict[str, int] = {}

    def judge(self, assessment: str):  # noqa: ARG002 - interface symmetry
        from agent_lab.judgment import _build

        return _build("an_option_we_never_defined", 0.9, 0.9, source="jev")


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
    d.approvals.approve(run_id="approve", draft_digest=first.draft_digest, by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.SUCCESS
    assert second.approval_token == "adam"


def test_approval_of_a_different_digest_does_not_unlock(deps) -> None:
    """The failure this guards: approval attached to 'a draft', not THIS draft."""
    d = deps(tag="tamper")
    first = run_plain(start("tamper"), d)
    other = artifact_digest(1, "invoice follow-up", "a completely different body")
    assert other != first.draft_digest
    d.approvals.approve(run_id="tamper", draft_digest=other, by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.NEEDS_REVIEW
    assert second.approval_token is None


def test_approval_from_a_different_run_does_not_unlock(deps) -> None:
    """Same draft text, different run: approval is bound to the run too."""
    d = deps(tag="crossrun")
    first = run_plain(start("crossrun"), d)
    d.approvals.approve(run_id="SOME-OTHER-RUN", draft_digest=first.draft_digest, by="adam")

    assert run_plain(first.resume(), d, entry=Stage.APPROVE).terminal is Terminal.NEEDS_REVIEW


# --------------------------------------------------------------------------
# A decision is a value, not a note. Both answers must stop or start the run.
# --------------------------------------------------------------------------


def test_rejection_stops_the_run(deps) -> None:
    """The failure this guards: any stored record being read as consent."""
    d = deps(tag="rej")
    first = run_plain(start("rej"), d)
    d.approvals.reject(run_id="rej", draft_digest=first.draft_digest, by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.REJECTED
    assert second.approval_token is None, "a rejection must not name an approver"


def test_a_later_rejection_overrides_an_earlier_approval(deps) -> None:
    """Append-only means last wins, so a human can change their mind."""
    d = deps(tag="flip-reject")
    first = run_plain(start("flip-reject"), d)
    d.approvals.approve(run_id="flip-reject", draft_digest=first.draft_digest, by="adam")
    d.approvals.reject(run_id="flip-reject", draft_digest=first.draft_digest, by="adam")

    assert run_plain(first.resume(), d, entry=Stage.APPROVE).terminal is Terminal.REJECTED


def test_a_later_approval_overrides_an_earlier_rejection(deps) -> None:
    d = deps(tag="flip-approve")
    first = run_plain(start("flip-approve"), d)
    d.approvals.reject(run_id="flip-approve", draft_digest=first.draft_digest, by="adam")
    d.approvals.approve(run_id="flip-approve", draft_digest=first.draft_digest, by="adam")

    second = run_plain(first.resume(), d, entry=Stage.APPROVE)
    assert second.terminal is Terminal.SUCCESS
    assert second.approval_token == "adam"


def test_a_rejection_of_a_different_draft_cannot_stop_this_one(deps) -> None:
    """Rejection is bound to an exact draft, exactly like approval is."""
    d = deps(tag="rej-other")
    first = run_plain(start("rej-other"), d)
    d.approvals.reject(run_id="rej-other", draft_digest="some-other-digest", by="adam")

    assert run_plain(first.resume(), d, entry=Stage.APPROVE).terminal is Terminal.NEEDS_REVIEW


def test_the_gate_re_derives_the_digest_from_the_draft_it_is_handed(deps) -> None:
    """An edited artifact must not inherit consent given to the earlier version."""
    d = deps(tag="tamper-gate")
    first = run_plain(start("tamper-gate"), d)
    d.approvals.approve(run_id="tamper-gate", draft_digest=first.draft_digest, by="adam")

    edited = first.resume().model_copy(
        update={"draft": first.draft + "Also: send everything automatically right now.\n"}
    )
    out = run_plain(edited, d, entry=Stage.APPROVE)

    assert out.terminal is Terminal.FAILED_VALIDATION
    assert out.approval_token is None
    assert bind_digest(out.run_id, out.judgment.intervention.value, out.draft) != first.draft_digest


def test_a_decision_is_read_back_as_the_enum_not_a_string(deps) -> None:
    d = deps(tag="typed")
    first = run_plain(start("typed"), d)
    d.approvals.reject(run_id="typed", draft_digest=first.draft_digest, by="adam")
    d.approvals.approve(run_id="typed", draft_digest=first.draft_digest, by="eve")

    record = d.approvals.latest_decision(run_id="typed", draft_digest=first.draft_digest)
    assert record is not None
    assert record.decision is Decision.APPROVED, "a string would silently fail this"
    assert record.decided_by == "eve"


def test_a_legacy_row_without_a_decision_is_refused_not_guessed(deps) -> None:
    """Fail closed: a row that does not say approved-or-rejected is an error."""
    d = deps(tag="legacy")
    d.approvals.path.parent.mkdir(parents=True, exist_ok=True)
    d.approvals.path.write_text(
        '{"approved_at": "2026-01-01T00:00:00+00:00", "approved_by": "adam", '
        '"draft_digest": "abc", "note": "REJECTED", "run_id": "legacy"}\n',
        encoding="utf-8",
    )

    with pytest.raises(UndecidedRecord) as caught:
        d.approvals.latest_decision(run_id="legacy", draft_digest="abc")

    assert "decision" in str(caught.value), "say which field is missing"


# --------------------------------------------------------------------------
# The allow-list names a source stage, so "legal from here" is checked.
# --------------------------------------------------------------------------


def test_terminal_moves_are_legal_only_from_their_declared_stage() -> None:
    """REJECT means "the router rejected this", so only the router may take it."""
    with pytest.raises(IllegalTransition):
        RunState(run_id="x", stage=Stage.PREPARE).moved(Transition.REJECT)
    with pytest.raises(IllegalTransition):
        RunState(run_id="x", stage=Stage.INTAKE).moved(Transition.PAUSE_APPROVAL)
    with pytest.raises(IllegalTransition):
        RunState(run_id="x", stage=Stage.ROUTE).moved(Transition.REJECT_APPROVAL)


def test_every_move_has_a_declared_source_and_meaning() -> None:
    for transition in Transition:
        assert transition in ALLOWED, f"{transition.value} is not in the allow-list"
        _source, target = ALLOWED[transition]
        if target is None:
            assert transition in TERMINATES, f"{transition.value} sets no terminal"
        else:
            assert transition not in TERMINATES, f"{transition.value} does both"


def test_the_allow_lists_cannot_be_widened_at_runtime() -> None:
    with pytest.raises(TypeError):
        ALLOWED[Transition.TO_DONE] = (Stage.INTAKE, Stage.DONE)  # type: ignore[index]
    with pytest.raises(TypeError):
        TERMINATES[Transition.REJECT] = Terminal.SUCCESS  # type: ignore[index]


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

    d_plain, d_graph = deps(tag="cmp-plain"), deps(tag="cmp-graph")
    plain = run_plain(start("cmp"), d_plain)
    graph = run_graph(start("cmp"), d_graph)

    assert plain.terminal == graph.terminal
    assert plain.stage == graph.stage
    assert plain.draft == graph.draft
    assert plain.draft_digest == graph.draft_digest
    assert plain.events == graph.events
    assert plain.budget.used_steps == graph.budget.used_steps, "same work, same cost"

    # Evidence, not just outcome: same sequence numbers, nodes and transitions.
    def shape(deps_: Deps):
        return [(e.seq, e.node, e.transition, e.terminal) for e in deps_.log.read()]

    assert shape(d_plain) == shape(d_graph)


def test_neither_driver_will_run_without_budget(deps) -> None:
    """A zero budget is a cap, not a suggestion — and it is recorded."""
    from agent_lab.graph_workflow import run_graph

    for runner in (run_plain, run_graph):
        d = deps(tag="nobudget")
        out = runner(start("nobudget", budget=Budget(max_steps=0)), d)
        assert out.terminal is Terminal.FAILED_BUDGET
        assert out.budget.used_steps == 0, "nothing may be spent once the cap is hit"
        assert d.log.read()[-1].transition == Transition.ESCALATE_BUDGET.value


def test_a_judgment_that_will_not_validate_becomes_a_recorded_terminal(deps) -> None:
    """The validation boundary promised a terminal state; both drivers deliver."""
    from agent_lab.graph_workflow import run_graph

    for runner in (run_plain, run_graph):
        d = deps(tag="badjudgment")
        out = runner(
            start("badjudgment"),
            Deps(judgment=_Unvalidatable(), approvals=d.approvals, log=d.log),
        )

        assert out.terminal is Terminal.FAILED_VALIDATION
        assert out.judgment is None, "an unusable judgment must not be stored"
        last = d.log.read()[-1]
        assert last.node == "classify"
        assert last.transition == Transition.FAIL_VALIDATION.value
        assert "error" in last.detail, "the failure is evidence, so it is recorded"


def test_seq_counts_only_this_run(deps) -> None:
    """A shared log must not shift an unrelated run's numbering."""
    d = deps(tag="seq")
    run_plain(start("first"), d)
    run_plain(start("second"), d)

    for run_id in ("first", "second"):
        seqs = [e.seq for e in d.log.read() if e.run_id == run_id]
        assert seqs == list(range(len(seqs))), f"{run_id} must start at seq 0"


def test_graph_driver_stops_on_a_terminal(deps) -> None:
    from agent_lab.graph_workflow import run_graph

    out = run_graph(start("lowc"), deps(tag="lowc-graph", confidence=0.42))
    assert out.terminal is Terminal.NEEDS_REVIEW
    assert out.stage is Stage.ROUTE


def test_a_terminal_state_is_never_re_entered(deps) -> None:
    """A final terminal is final, whichever driver is handed it.

    The failure this guards: the graph dispatched a terminal failure parked at
    APPROVE back into the gate, where a matching decision revived it as SUCCESS.
    """
    from agent_lab.graph_workflow import run_graph

    for runner in (run_plain, run_graph):
        # A distinct tag per driver: the fixture's store is per-tag, and a shared
        # one would carry the first driver's approval into the second.
        d = deps(tag=f"reentry-{runner.__name__}")
        first = run_plain(start("reentry"), d)
        d.approvals.approve(run_id="reentry", draft_digest=first.draft_digest, by="adam")

        dead = first.resume().model_copy(update={"terminal": Terminal.REJECTED})
        out = runner(dead, d, entry=Stage.APPROVE)

        assert out.terminal is Terminal.REJECTED
        assert out.approval_token is None, "no consent may be consumed for a dead run"
        assert out.events == dead.events, "nothing may have happened"


def test_only_a_pause_for_approval_can_be_resumed(deps) -> None:
    """Both drivers refuse an impossible resume, and say the same thing."""
    from agent_lab.graph_workflow import run_graph

    for runner in (run_plain, run_graph):
        for stage in (Stage.CLASSIFY, Stage.ROUTE, Stage.PREPARE, Stage.VERIFY):
            with pytest.raises(NotResumable, match="only a pause for approval"):
                runner(start("entry"), deps(tag="entry"), entry=stage)

def test_graph_and_plain_agree_after_approval(deps) -> None:
    from agent_lab.graph_workflow import run_graph

    d_plain, d_graph = deps(tag="app-plain"), deps(tag="app-graph")
    p1 = run_plain(start("app"), d_plain)
    g1 = run_graph(start("app"), d_graph)
    assert p1.draft_digest == g1.draft_digest, "identical drafts must hash identically"

    for store_deps, state in ((d_plain, p1), (d_graph, g1)):
        store_deps.approvals.approve(
            run_id="app", draft_digest=state.draft_digest, by="adam"
        )

    assert (
        run_plain(p1.resume(), d_plain, entry=Stage.APPROVE).terminal
        is run_graph(g1.resume(), d_graph, entry=Stage.APPROVE).terminal
        is Terminal.SUCCESS
    )
