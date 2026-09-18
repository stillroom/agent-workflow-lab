"""Lesson 03 — Jev (TypeSafe) as one node in the workflow.

Three things this lesson proves:

1. A model judgment can be validated into a typed object BEFORE control flow
   sees it (`Judgment`), so a surprising answer cannot silently become a route.
2. The same workflow runs with a live Jev call, a recorded replay, or a stub —
   the node code does not change.
3. Thresholds live in code, and the graph's behaviour changes predictably when
   confidence falls below them.

Live calls are opt-in:

    AGENT_LAB_JUDGMENT=jev      .venv/bin/python -m pytest lessons/lesson_03_jev -v
    AGENT_LAB_JUDGMENT=stub     .venv/bin/python -m pytest lessons/lesson_03_jev -v

Default is `stub`, so the suite never spends money or needs a key. When you do run
the live tests, the key is resolved by `agent_lab/credentials.py` — from the
environment, from a gitignored `.env`, or from the file a `.env` points at. The
repo never hardcodes where a secret lives; section 6 pins that behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_lab.approvals import ApprovalStore
from agent_lab.credentials import (
    CREDENTIAL_ENV,
    DOTENV_ENV,
    POINTER_ENV,
    CredentialError,
    load_typesafe_credentials,
    parse_env_file,
    read_env_file,
)
from agent_lab.judgment import (
    CRITERIA,
    GAP_INSTRUCTIONS,
    INSTRUCTIONS,
    Intervention,
    JevSource,
    JudgmentError,
    RecordedSource,
    StubSource,
    source_from_env,
)
from agent_lab.runlog import RunLog, write_recording
from agent_lab.state import Intervention as StateIntervention
from agent_lab.state import Judgment, RunState, Terminal
from agent_lab.workflow import Deps, run_plain

ASSESSMENT = (
    "Northside Garden Care (synthetic). Owner plus four field staff in Melbourne. "
    "Staff mark jobs complete in a field-service app and the owner invoices each "
    "afternoon; most customers pay within seven days and the owner chases the few "
    "stragglers every Friday, which is manageable. The real gap is review requests: "
    "a staff member sometimes texts a customer but there is no trigger, no record of "
    "who was contacted, and no suppression for customers who already reviewed. The "
    "owner estimates about three quarters of completed jobs get no review request. "
    "They want a small pilot this month, want to approve the wording and eligibility "
    "rules first, and do not want messages sent automatically during the trial."
)


def _deps(tmp_path: Path, source, tag: str = "jev") -> Deps:
    return Deps(
        judgment=source,
        approvals=ApprovalStore(tmp_path / f"{tag}-approvals.jsonl"),
        log=RunLog(tmp_path / f"{tag}.jsonl"),
    )


# --------------------------------------------------------------------------
# 1. The question is narrow, typed and reviewable — as plain data.
# --------------------------------------------------------------------------


def test_the_question_is_data_not_prose_buried_in_code() -> None:
    assert set(CRITERIA) == {
        Intervention.REVIEW_FOLLOW_UP.value,
        Intervention.PAYMENT_FOLLOW_UP.value,
        Intervention.NOT_A_FIT.value,
    }
    assert "explicit evidence" in INSTRUCTIONS
    assert "explicitly evidence" in GAP_INSTRUCTIONS


# --------------------------------------------------------------------------
# 2. Validation happens at the boundary.
# --------------------------------------------------------------------------


def test_invalid_judgment_is_rejected_at_the_boundary() -> None:
    """A model returning an option we did not define must not reach the router."""
    with pytest.raises(JudgmentError):
        from agent_lab.judgment import _build

        _build("something_else", 0.9, 0.9, source="jev")


def test_judgment_is_frozen() -> None:
    judgment = Judgment(
        intervention=StateIntervention.REVIEW_FOLLOW_UP,
        confidence=0.9,
        review_gap_evidenced=0.9,
        source="stub",
    )
    with pytest.raises(Exception):
        judgment.confidence = 0.1  # type: ignore[misc]


# --------------------------------------------------------------------------
# 3. Replay is deterministic and offline.
# --------------------------------------------------------------------------


def test_recorded_source_replays_a_frozen_judgment(tmp_path: Path) -> None:
    path = write_recording(
        tmp_path / "rec.json",
        assessment=ASSESSMENT,
        intervention=Intervention.REVIEW_FOLLOW_UP.value,
        confidence=0.97,
        review_gap=0.95,
    )
    source = RecordedSource(recording_path=path)
    first = source.judge(ASSESSMENT)
    second = source.judge(ASSESSMENT)

    assert first == second  # replay is stable
    assert first.source == "recorded"
    assert first.intervention is StateIntervention.REVIEW_FOLLOW_UP


def test_recording_refuses_a_different_assessment(tmp_path: Path) -> None:
    path = write_recording(
        tmp_path / "rec.json",
        assessment="a different business",
        intervention=Intervention.REVIEW_FOLLOW_UP.value,
        confidence=0.9,
        review_gap=0.9,
    )
    with pytest.raises(JudgmentError, match="different assessment"):
        RecordedSource(recording_path=path).judge(ASSESSMENT)


def test_replayed_run_is_identical_across_two_executions(tmp_path: Path) -> None:
    """The payoff of recording: same inputs, same evidence, byte-identical digest."""
    path = write_recording(
        tmp_path / "rec.json",
        assessment=ASSESSMENT,
        intervention=Intervention.REVIEW_FOLLOW_UP.value,
        confidence=0.97,
        review_gap=0.95,
    )

    digests = []
    for tag in ("replay-a", "replay-b"):
        deps = _deps(tmp_path, RecordedSource(recording_path=path), tag=tag)
        out = run_plain(RunState(run_id="replay", assessment_text=ASSESSMENT), deps)
        digests.append((out.draft_digest, out.events))

    assert digests[0] == digests[1]


# --------------------------------------------------------------------------
# 4. Source selection is explicit, never guessed.
# --------------------------------------------------------------------------


def test_source_from_env_rejects_an_unknown_selection(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LAB_JUDGMENT", "whatever")
    with pytest.raises(JudgmentError):
        source_from_env()


def test_source_from_env_defaults_to_stub(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LAB_JUDGMENT", raising=False)
    assert source_from_env().name == "stub"


def test_stub_source_drives_every_branch(tmp_path: Path) -> None:
    cases = {
        Intervention.REVIEW_FOLLOW_UP: Terminal.NEEDS_REVIEW,  # pauses at approval
        Intervention.PAYMENT_FOLLOW_UP: Terminal.NEEDS_REVIEW,
        Intervention.NOT_A_FIT: Terminal.REJECTED,
    }
    for intervention, expected in cases.items():
        deps = _deps(
            tmp_path,
            StubSource(intervention=intervention, confidence=0.9),
            tag=intervention.value,
        )
        out = run_plain(
            RunState(run_id=intervention.value, assessment_text=ASSESSMENT), deps
        )
        assert out.terminal is expected, intervention


def test_low_confidence_escalates_before_preparing(tmp_path: Path) -> None:
    deps = _deps(tmp_path, StubSource(confidence=0.55), tag="lowc")
    out = run_plain(RunState(run_id="lowc", assessment_text=ASSESSMENT), deps)
    assert out.terminal is Terminal.NEEDS_REVIEW
    assert out.draft is None, "a low-confidence run must not prepare an artifact"


# --------------------------------------------------------------------------
# 5. Live Jev — opt-in only.
# --------------------------------------------------------------------------


def _live_key_or_skip() -> None:
    """Live tests need a key. Say where to put one instead of failing obscurely."""
    try:
        load_typesafe_credentials()
    except CredentialError as exc:
        pytest.skip(str(exc).splitlines()[0])


@pytest.mark.skipif(
    os.getenv("AGENT_LAB_JUDGMENT") != "jev",
    reason="set AGENT_LAB_JUDGMENT=jev to run the live TypeSafe call",
)
def test_live_jev_returns_a_valid_typed_judgment(tmp_path: Path) -> None:
    _live_key_or_skip()
    source = JevSource()
    judgment = source.judge(ASSESSMENT)

    assert judgment.source == "jev"
    assert 0.0 <= judgment.confidence <= 1.0
    assert 0.0 <= judgment.review_gap_evidenced <= 1.0
    assert judgment.intervention in set(Intervention)
    assert source.last_usage.get("input_tokens", 0) > 0, "usage must be recorded"


@pytest.mark.skipif(
    os.getenv("AGENT_LAB_JUDGMENT") != "jev",
    reason="set AGENT_LAB_JUDGMENT=jev to run the live workflow",
)
def test_live_jev_drives_the_workflow_end_to_end(tmp_path: Path) -> None:
    _live_key_or_skip()
    deps = _deps(tmp_path, JevSource(), tag="live")
    out = run_plain(
        RunState(run_id="live", assessment_text=ASSESSMENT),
        deps,
    )
    events = deps.log.read()

    assert out.judgment is not None and out.judgment.source == "jev"
    assert events[1].node == "classify"
    assert events[1].detail["usage"]["input_tokens"] > 0
    assert out.terminal in set(Terminal)

    # Freeze it so the same run is reproducible forever afterwards.
    if out.judgment is not None:
        write_recording(
            tmp_path / "live-recording.json",
            assessment=ASSESSMENT,
            intervention=out.judgment.intervention.value,
            confidence=out.judgment.confidence,
            review_gap=out.judgment.review_gap_evidenced,
            usage=events[1].detail["usage"],
        )


# --------------------------------------------------------------------------
# 6. Where the key comes from — no personal path baked into the repo.
# --------------------------------------------------------------------------


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the loader at a temp `.env` and clear anything already exported."""
    monkeypatch.delenv(CREDENTIAL_ENV, raising=False)
    monkeypatch.delenv(POINTER_ENV, raising=False)
    dotenv = tmp_path / ".env"
    monkeypatch.setenv(DOTENV_ENV, str(dotenv))
    return dotenv


def test_env_file_parser_handles_the_documented_subset() -> None:
    values = parse_env_file(
        "\n".join(
            [
                "# a comment",
                "",
                "export EXPORTED=1",
                "QUOTED=\"two words\"",
                "SINGLE='one word'",
                "SPACED   =   value   ",
                "NOT_A_PAIR",
                "URL=https://example.test/?a=1",
                "DUP=first",
                "DUP=second",
            ]
        )
    )

    assert values["EXPORTED"] == "1", "an `export ` prefix is tolerated"
    assert values["QUOTED"] == "two words"
    assert values["SINGLE"] == "one word"
    assert values["SPACED"] == "value"
    assert "NOT_A_PAIR" not in values
    assert values["URL"] == "https://example.test/?a=1", "only the first = splits"
    assert values["DUP"] == "second", "last assignment wins"


def test_values_are_literal_and_never_interpolated() -> None:
    """Reading a .env must not expand anything, and must not execute anything."""
    values = parse_env_file("A=$HOME/x\nB=`whoami`\nC=${A}")

    assert values == {"A": "$HOME/x", "B": "`whoami`", "C": "${A}"}


def test_the_environment_wins_over_any_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = _isolate(monkeypatch, tmp_path)
    dotenv.write_text(f"{CREDENTIAL_ENV}=from-file\n", encoding="utf-8")
    monkeypatch.setenv(CREDENTIAL_ENV, "from-environment")

    assert load_typesafe_credentials() == "from-environment"
    assert os.environ[CREDENTIAL_ENV] == "from-environment"


def test_a_key_can_live_in_the_gitignored_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dotenv = _isolate(monkeypatch, tmp_path)
    dotenv.write_text(f"# local\n{CREDENTIAL_ENV}=direct-key\n", encoding="utf-8")

    assert load_typesafe_credentials() == "direct-key"
    assert os.environ[CREDENTIAL_ENV] == "direct-key", "the SDK reads the environment"


def test_a_pointer_variable_can_name_the_file_holding_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets = tmp_path / "elsewhere" / "typesafe.env"
    secrets.parent.mkdir()
    secrets.write_text(f"{CREDENTIAL_ENV}=pointed-key\n", encoding="utf-8")
    dotenv = _isolate(monkeypatch, tmp_path)
    dotenv.write_text(f"{POINTER_ENV}={secrets}\n", encoding="utf-8")

    assert load_typesafe_credentials() == "pointed-key"


def test_a_relative_pointer_resolves_beside_the_file_that_declared_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """So a checkout stays portable: move the folder, the pointer still works."""
    nested = tmp_path / "private"
    nested.mkdir()
    (nested / "typesafe.env").write_text(
        f"{CREDENTIAL_ENV}=relative-key\n", encoding="utf-8"
    )
    dotenv = _isolate(monkeypatch, tmp_path)
    dotenv.write_text(f"{POINTER_ENV}=private/typesafe.env\n", encoding="utf-8")

    assert load_typesafe_credentials() == "relative-key"


def test_a_pointer_at_a_file_without_the_key_names_the_problem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "empty.env"
    target.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    dotenv = _isolate(monkeypatch, tmp_path)
    dotenv.write_text(f"{POINTER_ENV}={target}\n", encoding="utf-8")

    with pytest.raises(CredentialError) as caught:
        load_typesafe_credentials()

    message = str(caught.value)
    assert str(target) in message, "name the file that was actually read"
    assert CREDENTIAL_ENV in message


def test_no_key_anywhere_is_an_actionable_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate(monkeypatch, tmp_path)  # a .env that does not exist is the normal case

    with pytest.raises(CredentialError) as caught:
        load_typesafe_credentials()

    message = str(caught.value)
    assert ".env.example" in message, "point at the template to copy"
    assert POINTER_ENV in message, "document the pointer option"
    assert CREDENTIAL_ENV not in os.environ


def test_a_missing_env_file_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope.env") == {}
