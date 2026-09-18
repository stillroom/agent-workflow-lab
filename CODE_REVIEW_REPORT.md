# Code Review Report — agent-workflow-lab

## Scope

Read-only review of the current `main` worktree. The tree was clean before review. This report is the sole requested repository write.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 AGENT_LAB_JUDGMENT=stub \
  .venv/bin/python -m pytest lessons -v -p no:cacheprovider
```

Result: **46 passed, 2 skipped**. The skipped tests require a live TypeSafe/Jev call and were deliberately not run.

## Findings

### High — A rejection unlocks the approval gate

**Evidence:** `agent_lab/approvals.py:51-69`, `agent_lab/workflow.py:154-167`

`reject()` stores a normal `Approval` record with `note="REJECTED"`. `find()` returns the first matching record without examining that note, and the workflow treats any returned record as approval. A rejected draft can therefore complete successfully; a later rejection also cannot override an earlier approval.

**Recommendation:** model a decision explicitly (`approved` / `rejected`), select the latest applicable decision, and make rejection lead to a rejected terminal state.

### High — Approval is not bound to the draft actually resumed

**Evidence:** `agent_lab/workflow.py:149-167`, `scripts/approve_demo.py:48-57`

The gate trusts `state.draft_digest`; it never recomputes the digest from `state.draft`. The demo reconstructs the draft from editable `runs/live-demo/draft.txt` but takes the digest from a historical log event. An edited draft can retain the old digest and be accepted.

**Recommendation:** recompute and compare the draft digest immediately before consuming approval. Restore the complete artifact-binding inputs from durable state rather than combining a historical digest with an independently read file.

### High — The graph driver does not preserve the plain driver's budget or audit semantics

**Evidence:** `agent_lab/graph_workflow.py:56-68`; contrast `agent_lab/workflow.py:203-248`

`_advance()` executes a node and moves state but does not spend `Budget` or append a `RunEvent`; it also discards node detail. This means a graph run can execute even with `max_steps=0` and produces no transition evidence, despite its claim to have identical semantics to the plain driver.

**Recommendation:** centralize budget spending, transition application, and event logging in a shared driver helper. Extend graph/plain equivalence tests to compare terminal result, budget use, and log records.

### High — `run_graph()` can resume a terminal state and turn it into success

**Evidence:** `agent_lab/graph_workflow.py:153-162`

Unlike `run_plain()`, `run_graph()` has no terminal-state guard. Its intake node dispatches approval-stage states directly to `AwaitApproval`, so a terminal failure whose stage is `APPROVE` can be processed again and reach `SUCCESS` if a matching approval exists.

**Recommendation:** return terminal states unchanged. Require the existing explicit resume path for only resumable pauses.

### Medium — The graph driver cannot resume ordinary intermediate stages

**Evidence:** `agent_lab/graph_workflow.py:81-87, 153-161`

The graph entry always starts at `Intake` and dispatches only `Stage.APPROVE`. A state at `CLASSIFY`, `ROUTE`, `PREPARE`, or `VERIFY` attempts an intake transition from the wrong stage and raises `IllegalTransition`.

**Recommendation:** dispatch every supported stage, or make the graph entry node route to the node corresponding to the current stage. Add resumption tests for every non-terminal stage.

### Medium — NUL-delimited digest bindings have field-boundary collisions

**Evidence:** `agent_lab/encoding.py:125-145`

The code claims NUL cannot occur in UTF-8 text. Python strings can contain it, so distinct inputs such as `("a\0b", "c")` and `("a", "b\0c")` serialize identically. This affects both `bind_digest()` and `canonical_digest()` and undermines artifact binding.

**Recommendation:** reject embedded NUL in every field or adopt an unambiguous encoding (for example, length-prefixed UTF-8 fields). Add collision regression tests.

### Medium — JSONL readers break on valid Unicode line separators

**Evidence:** `agent_lab/approvals.py:55-58`, `agent_lab/runlog.py:55-57`

Writers use `ensure_ascii=False`, but readers call `splitlines()`. Python treats Unicode line separators such as U+2028 as line boundaries even when they occur inside a valid JSON string. The resulting fragments fail JSON decoding.

**Recommendation:** iterate physical file lines (newline is explicitly LF on write) or split only on `"\n"`.

### Medium — Judgment failures do not become recorded terminal outcomes

**Evidence:** `agent_lab/workflow.py:219-234`, `agent_lab/graph_workflow.py:56-68`, `agent_lab/judgment.py:10-12`

Expected judgment/validation errors propagate out of both drivers. The plain driver records only `IllegalTransition`, leaving no failure event or terminal state for a failed judgment, contrary to the validation-boundary contract.

**Recommendation:** catch expected judgment failures in both drivers, transition to a defined failure terminal, and append a sanitized failure event.

### Medium — Building documentation makes a live call and deletes prior demo evidence

**Evidence:** `scripts/build_walkthrough.py:141-143`, `scripts/live_jev_demo.py:43-50, 94-106`

`build_walkthrough.py` always executes the live demo. That operation can require credentials/network access and removes/recreates the prior live-demo directory and recording. Documentation generation should not silently invoke destructive live work.

**Recommendation:** build from existing recordings by default; make live refresh an explicit opt-in with a separate, isolated output path.

## Summary

**9 findings:** 4 high, 5 medium. The most urgent fixes are the approval/rejection model and restoring equivalent safety/audit behavior in the graph driver.
