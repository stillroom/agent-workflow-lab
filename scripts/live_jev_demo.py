#!/usr/bin/env python3
"""Live proof: one Jev call drives the workflow, then gets frozen for replay.

    .venv/bin/python scripts/live_jev_demo.py

Prints the judgment, the route taken, the artifact digest, the run log, and
writes `recordings/<case>.json` so the identical run can be replayed offline.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_lab.approvals import ApprovalStore  # noqa: E402
from agent_lab.judgment import JevSource, RecordedSource  # noqa: E402
from agent_lab.runlog import RunLog, write_recording  # noqa: E402
from agent_lab.state import RunState, Terminal  # noqa: E402
from agent_lab.workflow import Deps, run_plain  # noqa: E402


def load_key() -> None:
    """Read TYPESAFE_API_KEY from the active Hermes profile .env if unset."""
    if os.getenv("TYPESAFE_API_KEY"):
        return
    env_file = Path.home() / ".hermes/profiles/hermes_engineer/.env"
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("TYPESAFE_API_KEY="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                os.environ["TYPESAFE_API_KEY"] = value
                return
    raise SystemExit("TYPESAFE_API_KEY is not set")


def main() -> int:
    load_key()

    case_path = ROOT / "cases" / "northside-garden-care.txt"
    assessment = case_path.read_text(encoding="utf-8")
    run_id = "live-demo"

    work = ROOT / "runs" / run_id
    work.mkdir(parents=True, exist_ok=True)

    deps = Deps(
        judgment=JevSource(),
        approvals=ApprovalStore(work / "approvals.jsonl"),
        log=RunLog(work / "run.jsonl"),
    )

    state = run_plain(RunState(run_id=run_id, assessment_text=assessment), deps)
    judgment = state.judgment
    assert judgment is not None

    if state.draft is not None:
        (work / "draft.txt").write_text(state.draft, encoding="utf-8", newline="\n")

    events = deps.log.read()
    classify_detail = next(e.detail for e in events if e.node == "classify")

    print("=" * 68)
    print("LIVE JEV JUDGMENT")
    print("=" * 68)
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

    recording = write_recording(
        ROOT / "recordings" / "northside-garden-care.json",
        assessment=assessment,
        intervention=judgment.intervention.value,
        confidence=judgment.confidence,
        review_gap=judgment.review_gap_evidenced,
        usage=classify_detail["usage"],
    )
    print(f"\n  frozen to  : {recording.relative_to(ROOT)}")

    # Replay the frozen judgment and confirm identical semantics, offline.
    replay_dir = ROOT / "runs" / f"{run_id}-replay"
    replay_deps = Deps(
        judgment=RecordedSource(recording_path=recording),
        approvals=ApprovalStore(replay_dir / "approvals.jsonl"),
        log=RunLog(replay_dir / "run.jsonl"),
    )
    replay = run_plain(RunState(run_id=run_id, assessment_text=assessment), replay_deps)
    same = (
        replay.terminal is state.terminal
        and replay.draft == state.draft
        and replay.draft_digest == state.draft_digest
        and replay.events == state.events
    )
    print(f"  replay identical to live run: {same}")

    if state.terminal is Terminal.NEEDS_REVIEW and state.draft_digest:
        print()
        print("=" * 68)
        print("APPROVAL GATE")
        print("=" * 68)
        print("  The run stopped here. Nothing was sent or published.")
        print("  To continue, an approval must name this exact digest:")
        print(f"      run_id       = {run_id}")
        print(f"      draft_digest = {state.draft_digest}")
        print()
        print("  Try it: .venv/bin/python scripts/approve_demo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
