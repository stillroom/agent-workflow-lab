"""Human decisions, stored durably and bound to an exact artifact.

Two rules this module enforces:

1. Approval never transfers to a materially edited version. The record carries
   the digest of the exact draft that was decided on; if the draft changes by one
   byte the record no longer matches, and the run refuses.

2. A decision is a typed value, not a note. "Approved or rejected?" is answered
   by a field, so no reader has to interpret prose. A free-text note is welcome,
   but nothing may branch on it.

The store is append-only, so `latest_decision` means "the last row for this run
and digest wins". That ordering is what lets a later rejection override an
earlier approval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Decision(str, Enum):
    """What a human decided about one exact draft."""

    APPROVED = "approved"
    REJECTED = "rejected"


class UndecidedRecord(RuntimeError):
    """A stored row predates the explicit-decision model.

    Refusing is the point. Inferring "approved" from a missing field — or from a
    note that happens to contain a word — is the exact failure this module exists
    to prevent, so a legacy row is an error with a fix, not a guess.
    """


@dataclass(frozen=True)
class DecisionRecord:
    """One durable human decision, bound to a run and a draft digest."""

    run_id: str
    draft_digest: str
    decided_by: str
    decided_at: str
    decision: Decision = Decision.APPROVED
    note: str = ""

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("a decision must name a run")
        if not self.draft_digest:
            raise ValueError("a decision must name the draft digest it applies to")
        if not self.decided_by:
            raise ValueError("a decision must name who made it")


class ApprovalStore:
    """Append-only JSONL of human decisions, outside any model's control."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _rows(self) -> list[dict]:
        """Every stored row, in file order.

        Split on LF only. The writer pins newline to "\\n", while `str.splitlines`
        also treats characters such as U+2028 as line breaks — and those are legal
        inside a JSON string, so splitting on them would tear valid rows apart.
        """
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").split("\n"), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if "decision" not in row:
                raise UndecidedRecord(
                    f"{self.path}:{number} has no `decision` field, so this row "
                    f"does not say whether the draft was approved or rejected. "
                    f"Re-record the decision, or delete the run directory and "
                    f"start a new run."
                )
            rows.append(row)
        return rows

    def record(
        self,
        *,
        run_id: str,
        draft_digest: str,
        decided_by: str,
        decision: Decision = Decision.APPROVED,
        note: str = "",
    ) -> DecisionRecord:
        record = DecisionRecord(
            run_id=run_id,
            draft_digest=draft_digest,
            decided_by=decided_by,
            decided_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            decision=decision,
            note=note,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record.__dict__, ensure_ascii=False, sort_keys=True) + "\n"
            )
        return record

    def approve(
        self, *, run_id: str, draft_digest: str, by: str, note: str = ""
    ) -> DecisionRecord:
        return self.record(
            run_id=run_id,
            draft_digest=draft_digest,
            decided_by=by,
            decision=Decision.APPROVED,
            note=note,
        )

    def reject(
        self, *, run_id: str, draft_digest: str, by: str, note: str = ""
    ) -> DecisionRecord:
        return self.record(
            run_id=run_id,
            draft_digest=draft_digest,
            decided_by=by,
            decision=Decision.REJECTED,
            note=note,
        )

    def latest_decision(self, *, run_id: str, draft_digest: str) -> DecisionRecord | None:
        """The LAST decision matching THIS run and THIS exact digest, if any.

        Last, not first: the store only appends, so a later decision is a human
        changing their mind, and it must be able to.
        """
        match: DecisionRecord | None = None
        for row in self._rows():
            if row["run_id"] == run_id and row["draft_digest"] == draft_digest:
                # JSON gives back "approved"; the record must carry the enum, so
                # that every reader compares a typed value rather than a string.
                match = DecisionRecord(**{**row, "decision": Decision(row["decision"])})
        return match
