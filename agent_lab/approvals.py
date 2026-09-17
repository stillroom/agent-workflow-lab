"""Human approval, stored durably and bound to an exact artifact.

The rule this module enforces: approval never transfers to a materially edited
version. The token carries the digest of the exact draft that was approved; if
the draft changes by one byte, the token no longer matches and the run refuses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Approval:
    run_id: str
    draft_digest: str
    approved_by: str
    approved_at: str
    note: str = ""


class ApprovalStore:
    """Append-only JSONL of approval decisions, outside any model's control."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        run_id: str,
        draft_digest: str,
        approved_by: str,
        note: str = "",
    ) -> Approval:
        approval = Approval(
            run_id=run_id,
            draft_digest=draft_digest,
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            note=note,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(approval.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
        return approval

    def find(self, *, run_id: str, draft_digest: str) -> Approval | None:
        """Only a decision matching THIS run and THIS exact digest counts."""
        if not self.path.is_file():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["run_id"] == run_id and row["draft_digest"] == draft_digest:
                return Approval(**row)
        return None

    def reject(self, *, run_id: str, draft_digest: str, by: str) -> None:
        self.record(
            run_id=run_id,
            draft_digest=draft_digest,
            approved_by=by,
            note="REJECTED",
        )
