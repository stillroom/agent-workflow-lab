#!/usr/bin/env python3
"""Resume the paused live run by approving its EXACT artifact digest.

    .venv/bin/python scripts/approve_demo.py

This is the human gate. It is deliberately dumb: you must supply (or confirm)
the digest that identifies the exact artifact. Approve a different digest and
the run stays paused — which is the behaviour the tests pin down.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_lab.approvals import ApprovalStore  # noqa: E402
from agent_lab.judgment import RecordedSource  # noqa: E402
from agent_lab.runlog import RunLog  # noqa: E402
from agent_lab.state import RunState, Stage  # noqa: E402
from agent_lab.workflow import Deps, run_plain  # noqa: E402


def main() -> int:
    run_id = "live-demo"
    run_dir = ROOT / "runs" / run_id
    recording = ROOT / "recordings" / "northside-garden-care.json"

    if not (run_dir / "run.jsonl").is_file():
        raise SystemExit("no recorded run; run scripts/live_jev_demo.py first")

    previous = RunLog(run_dir / "run.jsonl").read()
    paused = next(
        (e for e in reversed(previous) if e.terminal == "NEEDS_REVIEW"), None
    )
    if paused is None:
        raise SystemExit("the last run did not pause for approval")

    digest = paused.detail.get("digest")
    print(f"run_id       : {run_id}")
    print(f"draft_digest : {digest}")
    print()

    approvals = ApprovalStore(run_dir / "approvals.jsonl")

    # Rebuild the paused state: same typed state, same frozen judgment, same log.
    assessment = (ROOT / "cases" / "northside-garden-care.txt").read_text(encoding="utf-8")
    paused_state = RunState(
        run_id=run_id,
        stage=Stage.APPROVE,
        draft=run_dir.joinpath("draft.txt").read_text(encoding="utf-8")
        if (run_dir / "draft.txt").is_file()
        else None,
        draft_digest=digest,
    )
    if paused_state.draft is None:
        # Recompute deterministically from the frozen judgment (offline replay).
        deps = Deps(
            judgment=RecordedSource(recording_path=recording),
            approvals=approvals,
            log=RunLog(run_dir / "run.jsonl"),
        )
        rebuilt = run_plain(
            RunState(run_id=run_id, assessment_text=assessment), deps
        )
        paused_state = rebuilt

    print("--- without an approval ---")
    deps = Deps(
        judgment=RecordedSource(recording_path=recording),
        approvals=approvals,
        log=RunLog(run_dir / "run.jsonl"),
    )
    blocked = run_plain(paused_state.resume(), deps, entry=Stage.APPROVE)
    print(f"terminal={blocked.terminal.value}  (expected NEEDS_REVIEW)")

    print()
    print("--- recording approval of THIS digest ---")
    approvals.record(run_id=run_id, draft_digest=digest, approved_by="adam")
    resumed = run_plain(paused_state.resume(), deps, entry=Stage.APPROVE)
    print(f"terminal={resumed.terminal.value}  approved_by={resumed.approval_token}")
    print()
    print("Nothing was sent, published or deployed. The gate only unlocked a terminal state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
