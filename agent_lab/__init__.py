"""A tiny deterministic agent-workflow lab.

Design rule borrowed from the research synthesis this lab implements:
**build the semantics, adopt the infrastructure.**

Everything here is small enough to read in one sitting:

    state.py       typed state, legal transitions, terminal states, budgets
    encoding.py    the encoding rules that stop silent corruption
    judgment.py    where a typed model judgment comes from (Jev / recorded / stub)
    runlog.py      append-only run evidence
    approvals.py   human approval bound to an exact artifact digest
    workflow.py    the workflow as a PLAIN Python state machine
    graph_workflow.py   the same workflow driven by pydantic-graph
"""

from .approvals import Approval, ApprovalStore
from .encoding import bind_digest, canonical_digest, decode_strict, normalise
from .judgment import JudgmentSource, JevSource, JudgmentError, RecordedSource, StubSource
from .runlog import RunEvent, RunLog, write_recording
from .state import (
    Budget,
    BudgetExceeded,
    IllegalTransition,
    Intervention,
    Judgment,
    NotResumable,
    RunState,
    Stage,
    Terminal,
    Transition,
)
from .workflow import Deps, artifact_digest, run_plain

__all__ = [
    "Approval",
    "ApprovalStore",
    "Budget",
    "BudgetExceeded",
    "Deps",
    "IllegalTransition",
    "Intervention",
    "JevSource",
    "Judgment",
    "JudgmentError",
    "JudgmentSource",
    "NotResumable",
    "RecordedSource",
    "RunEvent",
    "RunLog",
    "RunState",
    "Stage",
    "StubSource",
    "Terminal",
    "Transition",
    "artifact_digest",
    "bind_digest",
    "canonical_digest",
    "decode_strict",
    "normalise",
    "run_plain",
    "write_recording",
]
