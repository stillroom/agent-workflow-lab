"""Append-only run log — the smallest thing that makes a run inspectable.

One JSON object per line. Never rewritten, only appended. Two consequences
that matter:

* A run can be read back exactly as it happened, including failures.
* A recorded judgment can be replayed later without calling the model again.

This deliberately does NOT try to be Temporal. It records what happened; it
does not drive resumption. That distinction is the whole lesson in report §1.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunEvent:
    run_id: str
    seq: int
    node: str
    stage: str
    transition: str | None
    terminal: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


class RunLog:
    """Append-only JSONL writer/reader.

    `ensure_ascii=False` keeps human-readable text readable; the file is
    always written as UTF-8 with LF endings, so a digest over its bytes is
    stable across platforms.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, event: RunEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(event.to_json() + "\n")

    def read(self) -> list[RunEvent]:
        """Every recorded event, in file order.

        Split on LF only. The writer pins newline to "\n", while
        `str.splitlines` also breaks on characters such as U+2028 — which are
        legal inside a JSON string, so splitting on them tears valid rows apart.
        """
        if not self.path.is_file():
            return []
        events: list[RunEvent] = []
        for line in self.path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                events.append(RunEvent(**json.loads(line)))
        return events

    def digest(self) -> str:
        """Digest of the exact recorded bytes — evidence, not interpretation."""
        import hashlib

        if not self.path.is_file():
            return ""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def write_recording(
    path: Path,
    *,
    assessment: str,
    intervention: str,
    confidence: float,
    review_gap: float,
    usage: dict[str, int] | None = None,
) -> Path:
    """Freeze one live judgment to disk so it can be replayed deterministically."""
    import hashlib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "assessment_sha": hashlib.sha256(assessment.encode("utf-8")).hexdigest(),
        "intervention": intervention,
        "confidence": confidence,
        "review_gap": review_gap,
        "usage": usage or {},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path
