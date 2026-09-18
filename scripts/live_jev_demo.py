#!/usr/bin/env python3
"""One Jev judgment drives the workflow, then gets frozen for replay.

    .venv/bin/python scripts/live_jev_demo.py            # replay the recording
    .venv/bin/python scripts/live_jev_demo.py --live      # call Jev, refresh it

The default is offline and needs no credentials: it replays the committed
recording in `recordings/`, so a fresh clone can reproduce the whole run.

`--live` makes a real call, spends real tokens, and overwrites the recording.
That is the only mode that needs `TYPESAFE_API_KEY`, and the only mode that
writes to `recordings/`. Nothing here ever deletes it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_lab.approvals import ApprovalStore  # noqa: E402
from agent_lab.credentials import CredentialError, load_typesafe_credentials  # noqa: E402
from agent_lab.judgment import JevSource, RecordedSource  # noqa: E402
from agent_lab.runlog import RunLog, write_recording  # noqa: E402
from agent_lab.state import RunState, Terminal  # noqa: E402
from agent_lab.workflow import Deps, run_plain  # noqa: E402

CASE = "northside-garden-care"
RUN_ID = "live-demo"


def load_key() -> None:
    """Resolve TYPESAFE_API_KEY from the environment or a configured env file.

    No path is hardcoded here: see `agent_lab/credentials.py` and `.env.example`.
    """
    try:
        load_typesafe_credentials()
    except CredentialError as exc:
        raise SystemExit(str(exc)) from exc


def _scratch(name: str) -> Path:
    """A clean scratch directory for one pass.

    `runs/` is disposable, and `seq` counts this run's existing events, so a
    stale directory would make two passes of the same run disagree for no real
    reason. Only this demo's own scratch is ever cleared.
    """
    path = ROOT / "runs" / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run(source, work: Path, assessment: str):
    deps = Deps(
        judgment=source,
        approvals=ApprovalStore(work / "approvals.jsonl"),
        log=RunLog(work / "run.jsonl"),
    )
    state = run_plain(RunState(run_id=RUN_ID, assessment_text=assessment), deps)
    return state, deps


def main(argv: list[str]) -> int:
    live = "--live" in argv
    recording = ROOT / "recordings" / f"{CASE}.json"

    if live:
        load_key()
        source = JevSource()
        origin = "LIVE JEV CALL (a real call, real tokens)"
    else:
        if not recording.is_file():
            raise SystemExit(
                f"no recording at {recording.relative_to(ROOT)} to replay. "
                f"Run with --live once to create it."
            )
        source = RecordedSource(recording_path=recording)
        origin = f"REPLAYED FROM {recording.relative_to(ROOT)} (no network, no key)"

    assessment = (ROOT / "cases" / f"{CASE}.txt").read_text(encoding="utf-8")

    work = _scratch(RUN_ID)
    state, deps = _run(source, work, assessment)
    judgment = state.judgment
    assert judgment is not None

    if state.draft is not None:
        (work / "draft.txt").write_text(state.draft, encoding="utf-8", newline="\n")

    events = deps.log.read()
    classify_detail = next(e.detail for e in events if e.node == "classify")

    print("=" * 68)
    print("JEV JUDGMENT")
    print("=" * 68)
    print(f"  source            : {origin}")
    print(f"  intervention      : {judgment.intervention.value}")
    print(f"  confidence        : {judgment.confidence:.3f}")
    print(f"  review_gap signal : {judgment.review_gap_evidenced:.3f}")
    print(f"  usage             : {classify_detail['usage']}")
    print()
    print("=" * 68)
    print("RUN LOG (what actually happened, in order)")
    print("=" * 68)
    for event in events:
        print(
            f"  seq{event.seq}  {event.node:<15} {event.transition:<28} "
            f"terminal={event.terminal}"
        )
    print()
    print("=" * 68)
    print("OUTCOME")
    print("=" * 68)
    print(f"  terminal   : {state.terminal.value}")
    print(f"  stage      : {state.stage.value}")
    print(f"  steps used : {state.budget.used_steps}/{state.budget.max_steps}")
    print(f"  digest     : {state.draft_digest}")
    print(f"  log digest : {deps.log.digest()[:32]}...")

    if live:
        written = write_recording(
            recording,
            assessment=assessment,
            intervention=judgment.intervention.value,
            confidence=judgment.confidence,
            review_gap=judgment.review_gap_evidenced,
            usage=classify_detail["usage"],
        )
        print(f"\n  frozen to  : {written.relative_to(ROOT)} (refreshed)")

    # Second pass from the recording, and compare. Offline either way.
    replay_deps_dir = _scratch(f"{RUN_ID}-replay")
    replay, replay_deps = _run(
        RecordedSource(recording_path=recording), replay_deps_dir, assessment
    )
    same = (
        replay.terminal is state.terminal
        and replay.draft == state.draft
        and replay.draft_digest == state.draft_digest
        and replay.events == state.events
    )
    if live:
        print(f"  replay of the fresh recording is identical: {same}")
    else:
        print(f"  a second pass over the same recording is identical: {same}")

    if state.terminal is Terminal.NEEDS_REVIEW and state.draft_digest:
        print()
        print("=" * 68)
        print("APPROVAL GATE")
        print("=" * 68)
        print("  The run stopped here. Nothing was sent or published.")
        print("  To continue, a decision must name this exact digest:")
        print(f"      run_id       = {RUN_ID}")
        print(f"      draft_digest = {state.draft_digest}")
        print()
        print("  Try it: .venv/bin/python scripts/approve_demo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
