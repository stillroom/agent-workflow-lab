# Agent Workflow Lab

A small, readable agent workflow you can rebuild by hand: **typed state, explicit
transitions, one narrow model judgment, and a human approval gate**.

Built as the practical half of the research synthesis in
`life-os/Business/Stillroom/agent-team/research/reports/deterministic-agent-workflows-research.md`.
That report's recommendation was *build the semantics, adopt the infrastructure*.
This lab is the semantics, small enough to read in one sitting.

Open **[walkthrough.html](walkthrough.html)** in a browser for the guided version —
code samples, test output and the graph diagram are generated from the real files,
so the page cannot drift from the lab.

## Quick start

```bash
cd ~/Projects/agent-workflow-lab
python3 -m venv .venv
.venv/bin/python -m pip install pydantic pydantic-graph typesafe-sdk pytest

.venv/bin/python -m pytest lessons -v          # 46 tests, no API key, no spend
.venv/bin/python scripts/live_jev_demo.py      # live Jev -> workflow -> pause
.venv/bin/python scripts/approve_demo.py       # approve the exact artifact
.venv/bin/python scripts/build_walkthrough.py  # regenerate the HTML page
```

The live demo is the only script that needs a key, and the repo never hardcodes
where yours lives — see [Where the Jev key comes from](#where-the-jev-key-comes-from).

## What is here

| File | What it teaches |
|---|---|
| `agent_lab/state.py` | Typed state, an allow-list of transitions, terminal states, step budgets |
| `agent_lab/encoding.py` | The encoding rules that stop silent data corruption |
| `agent_lab/judgment.py` | One typed judgment, from Jev / a recording / a stub |
| `agent_lab/credentials.py` | Where the Jev key comes from: environment, `.env`, or a `.env` pointer |
| `agent_lab/runlog.py` | Append-only JSONL run evidence |
| `agent_lab/approvals.py` | Human approval bound to an exact artifact digest |
| `agent_lab/workflow.py` | The workflow as a **plain Python state machine** |
| `agent_lab/graph_workflow.py` | The same workflow driven by **pydantic-graph** |
| `lessons/lesson_01_encoding/` | Tests that demonstrate each encoding failure mode |
| `lessons/lesson_02_workflow/` | Tests for the control plane, gates and terminals |
| `lessons/lesson_03_jev/` | Tests for the model boundary and deterministic replay |
| `scripts/live_jev_demo.py` | Real Jev call driving a real run |
| `scripts/approve_demo.py` | Resuming a paused run at the approval gate |
| `scripts/build_walkthrough.py` | Generates `walkthrough.html` from the real source |
| `.env.example` | Template for the gitignored `.env`; contains no secret |

## The path a run takes

```text
intake -> classify -> route -> prepare -> verify -> await_approval
             |           |                              |
             |           +-- not a fit ----------> REJECTED          (final)
             |           +-- confidence < 0.60 --> NEEDS_REVIEW      (pause)
             +-- bad input ---------------------> raises before any model call

await_approval:  exact digest approved  ->  SUCCESS
                 anything else          ->  NEEDS_REVIEW (still paused)
```

## Why Jev sits here

TypeSafe's System One model returns **typed judgments with probabilities**, not
prose. That is exactly what a deterministic control plane wants: a value it can
validate, threshold and route on.

- The question is narrow — one `Choice` for the intervention, one `Noul` for the
  review-gap signal — and both ride in **one batched call**.
- The answer is validated into a frozen `Judgment` **before** the router sees it.
  An option outside the three we defined raises at the boundary.
- Thresholds live in code (confidence < 0.60 escalates), so they are visible,
  testable, and evaluated on your own data.
- The judgment is **frozen to a recording** after a live run, so the same run
  replays offline with identical semantics.

Swapping the source never touches the workflow:

```bash
AGENT_LAB_JUDGMENT=stub      # default; tests never spend money
AGENT_LAB_JUDGMENT=recorded  # deterministic offline replay
AGENT_LAB_JUDGMENT=jev       # live TypeSafe call
```

## Where the Jev key comes from

The key is the SDK's `TYPESAFE_API_KEY`, and **no path to it is committed**. The
repo resolves it in this order, first hit wins:

1. **the environment** — already exported, or supplied by CI
2. **the repo `.env`** — gitignored, created from `.env.example`
3. **the file `.env` points at** — a pointer, so a checkout can say "the key lives
   over there" without this repository knowing where "there" is

```bash
cp .env.example .env
```

Then either put the key in `.env` directly:

```dotenv
TYPESAFE_API_KEY=...
```

or keep secrets out of the repo entirely and point at a file that already has one:

```dotenv
TYPESAFE_ENV_FILE=~/.config/my-secrets/typesafe.env
```

Relative pointers resolve against the directory of the `.env` that declared them,
so the checkout stays portable if you move it. `$VAR` values are never expanded
and never executed: a value is read literally. Nothing in the lab authenticates —
the key is exported and the SDK reads it from there. Run a live test with no key
and the failure names the three options above instead of raising a traceback.

## What is deliberately not here

- No durable execution engine, no distributed workers, no crash recovery across
  machines. If those become necessary, that is the Temporal threshold — add it
  later, behind an adapter, once a real need is demonstrated.
- No tracing backend. The run log is the minimum useful evidence.
- No automatic anything. The run ends at a terminal state; nothing is sent,
  published or deployed.

## What the model is never trusted with

Permission checks, arithmetic, source truth, approvals, sending, and every
irreversible action. Those live in code. The model supplies one semantic signal,
and code decides what to do with it.

## Honest limits

- Teaching lab, not production infrastructure.
- The approval store is an append-only local file. It records **who was named**
  as approver; it does not authenticate them.
- `.env` and the pointer it can hold are conveniences, not a secrets manager. A
  key written there is plaintext on disk with whatever file permissions you gave
  it; for anything more, point `TYPESAFE_ENV_FILE` at your real secret store.
- A live Jev call may differ between runs. Only `recorded` replay is
  byte-stable.
- The 0.60 confidence threshold is illustrative, not tuned on real data.
- `cases/northside-garden-care.txt` is invented synthetic data. No real
  business, prospect or client information is in this lab.

## Licence

MIT. Use it, modify it, sell it — see [LICENSE](LICENSE). The only obligation is
keeping the copyright notice and licence text with any copy or substantial
portion. There is no warranty.

That covers the lab. The Jev endpoint itself is TypeSafe's service and is subject
to their terms, not this licence; nothing here grants you access to it.
