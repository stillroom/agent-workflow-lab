# Code Review Report — agent-workflow-lab

## Scope

Read-only review of the current `main` worktree. The tree was clean before review. This report is the sole requested repository write.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 AGENT_LAB_JUDGMENT=stub \
  .venv/bin/python -m pytest lessons -v -p no:cacheprovider
```

Result: **46 passed, 2 skipped**. The skipped tests require a live TypeSafe/Jev call and were deliberately not run.

## Status

All nine findings were reproduced before being fixed, and each is now pinned by
regression tests. Fixed on `fix/approval-contract` (PR #1), merged as five
commits:

| # | Severity | Finding | Fixed in |
|---|---|---|---|
| 1 | High | A rejection unlocks the approval gate | `d6d4ac8` |
| 2 | High | Approval is not bound to the draft actually resumed | `d6d4ac8` |
| 3 | High | The graph driver drops the plain driver's budget and audit semantics | `6571a5e` |
| 4 | High | `run_graph()` can resume a terminal state and turn it into success | `f7e57f1` |
| 5 | Medium | The graph driver cannot resume ordinary intermediate stages | `f7e57f1` (re-scoped, see below) |
| 6 | Medium | NUL-delimited digest bindings have field-boundary collisions | `ff67275` |
| 7 | Medium | JSONL readers break on valid Unicode line separators | `ff67275` |
| 8 | Medium | Judgment failures do not become recorded terminal outcomes | `6571a5e` |
| 9 | Medium | Building documentation makes a live call and deletes prior evidence | `348d35a` |

Three further problems were found while acting on this report and are fixed in
the same branch; they are listed under "Beyond this report" at the end.

Suite after the fixes: **65 passed, 2 skipped**, verified from a fresh clone with
no `.env` and no key.

## Findings

### High — A rejection unlocks the approval gate

**Evidence:** `agent_lab/approvals.py:51-69`, `agent_lab/workflow.py:154-167`

`reject()` stores a normal `Approval` record with `note="REJECTED"`. `find()` returns the first matching record without examining that note, and the workflow treats any returned record as approval. A rejected draft can therefore complete successfully; a later rejection also cannot override an earlier approval.

**Recommendation:** model a decision explicitly (`approved` / `rejected`), select the latest applicable decision, and make rejection lead to a rejected terminal state.

**Status:** Fixed in `d6d4ac8`. `Decision` is now a field on `DecisionRecord`
rather than a note, `latest_decision()` returns the last matching row so
append-only order means last-wins, and rejection reaches `Terminal.REJECTED` via
a new `awaiting_approval->rejected` move. Legacy rows without a `decision` field
raise instead of being guessed at.

### High — Approval is not bound to the draft actually resumed

**Evidence:** `agent_lab/workflow.py:149-167`, `scripts/approve_demo.py:48-57`

The gate trusts `state.draft_digest`; it never recomputes the digest from `state.draft`. The demo reconstructs the draft from editable `runs/live-demo/draft.txt` but takes the digest from a historical log event. An edited draft can retain the old digest and be accepted.

**Recommendation:** recompute and compare the draft digest immediately before consuming approval. Restore the complete artifact-binding inputs from durable state rather than combining a historical digest with an independently read file.

**Status:** Fixed in `d6d4ac8`. The gate re-derives the digest from the draft in
hand and fails closed (`FAILED_VALIDATION`) on mismatch, so `draft_digest` is now
documentation for humans rather than authority. `approve_demo.py` rebuilds state
from the recording and compares `draft.txt` instead of trusting it.

### High — The graph driver does not preserve the plain driver's budget or audit semantics

**Evidence:** `agent_lab/graph_workflow.py:56-68`; contrast `agent_lab/workflow.py:203-248`

`_advance()` executes a node and moves state but does not spend `Budget` or append a `RunEvent`; it also discards node detail. This means a graph run can execute even with `max_steps=0` and produces no transition evidence, despite its claim to have identical semantics to the plain driver.

**Recommendation:** centralize budget spending, transition application, and event logging in a shared driver helper. Extend graph/plain equivalence tests to compare terminal result, budget use, and log records.

**Status:** Fixed in `6571a5e`. `workflow.step()` is now the only place budget is
spent, a move is applied, or evidence written, and both drivers call it.
Equivalence tests compare budget use and the full
`(seq, node, transition, terminal)` log shape, plus zero-budget behaviour.

### High — `run_graph()` can resume a terminal state and turn it into success

**Evidence:** `agent_lab/graph_workflow.py:153-162`

Unlike `run_plain()`, `run_graph()` has no terminal-state guard. Its intake node dispatches approval-stage states directly to `AwaitApproval`, so a terminal failure whose stage is `APPROVE` can be processed again and reach `SUCCESS` if a matching approval exists.

**Recommendation:** return terminal states unchanged. Require the existing explicit resume path for only resumable pauses.

**Status:** Fixed in `f7e57f1`. Both drivers return a terminal state untouched, so
resuming remains an explicit `RunState.resume()` decision and `NotResumable`
means something in either driver.

### Medium — The graph driver cannot resume ordinary intermediate stages

**Evidence:** `agent_lab/graph_workflow.py:81-87, 153-161`

The graph entry always starts at `Intake` and dispatches only `Stage.APPROVE`. A state at `CLASSIFY`, `ROUTE`, `PREPARE`, or `VERIFY` attempts an intake transition from the wrong stage and raises `IllegalTransition`.

**Recommendation:** dispatch every supported stage, or make the graph entry node route to the node corresponding to the current stage. Add resumption tests for every non-terminal stage.

**Status:** Fixed in `f7e57f1`, re-scoped deliberately. Dispatching every stage
was NOT implemented, because resuming at `ROUTE` and `PREPARE` cannot work: the
typed state carries no judgment and no draft, so those nodes would have to invent
inputs. The cause was that `entry` accepted stages it could never honour, so both
drivers now share `resume_entry()`, which accepts only a pause for approval and
refuses the rest with one message. Previously the same input failed differently
in each driver (`IllegalTransition` from the graph, `AssertionError` from the
plain runner), and that divergence is what the tests now pin.

### Medium — NUL-delimited digest bindings have field-boundary collisions

**Evidence:** `agent_lab/encoding.py:125-145`

The code claims NUL cannot occur in UTF-8 text. Python strings can contain it, so distinct inputs such as `("a\0b", "c")` and `("a", "b\0c")` serialize identically. This affects both `bind_digest()` and `canonical_digest()` and undermines artifact binding.

**Recommendation:** reject embedded NUL in every field or adopt an unambiguous encoding (for example, length-prefixed UTF-8 fields). Add collision regression tests.

**Status:** Fixed in `ff67275`, using both halves of the recommendation where each
fits. `bind_digest()` now length-prefixes each field, so no field content can
forge a boundary. `canonical_digest()` keeps its NUL layout because that layout is
a wire contract with the Stillroom acquisition app and changing it would
invalidate every digest that app has stored, so it rejects NUL-containing fields
instead of colliding silently.

### Medium — JSONL readers break on valid Unicode line separators

**Evidence:** `agent_lab/approvals.py:55-58`, `agent_lab/runlog.py:55-57`

Writers use `ensure_ascii=False`, but readers call `splitlines()`. Python treats Unicode line separators such as U+2028 as line boundaries even when they occur inside a valid JSON string. The resulting fragments fail JSON decoding.

**Recommendation:** iterate physical file lines (newline is explicitly LF on write) or split only on `"\n"`.

**Status:** Fixed in `ff67275`. Both readers split on LF only, matching what the
writers pin, with U+2028/U+2029 round-trip tests for each.

### Medium — Judgment failures do not become recorded terminal outcomes

**Evidence:** `agent_lab/workflow.py:219-234`, `agent_lab/graph_workflow.py:56-68`, `agent_lab/judgment.py:10-12`

Expected judgment/validation errors propagate out of both drivers. The plain driver records only `IllegalTransition`, leaving no failure event or terminal state for a failed judgment, contrary to the validation-boundary contract.

**Recommendation:** catch expected judgment failures in both drivers, transition to a defined failure terminal, and append a sanitized failure event.

**Status:** Fixed in `6571a5e`. `JudgmentError` becomes a recorded
`FAILED_VALIDATION` terminal with a sanitized event, in both drivers.

One correction to the framing, which made the real bug worse: `workflow.py`
caught `IllegalTransition` around the NODE call, but nodes never call `moved()` —
the driver does. That handler was unreachable while `JudgmentError`, which really
does escape, went unhandled. The `try` wrapped the wrong call; it now wraps the
move, where it can fire. Note that `n_intake` raising on bad input was NOT
changed: the README documents "bad input raises before any model call", so that
difference is intentional.

### Medium — Building documentation makes a live call and deletes prior demo evidence

**Evidence:** `scripts/build_walkthrough.py:141-143`, `scripts/live_jev_demo.py:43-50, 94-106`

`build_walkthrough.py` always executes the live demo. That operation can require credentials/network access and removes/recreates the prior live-demo directory and recording. Documentation generation should not silently invoke destructive live work.

**Recommendation:** build from existing recordings by default; make live refresh an explicit opt-in with a separate, isolated output path.

**Status:** Fixed in `348d35a`. `recordings/` is no longer gitignored, so the
frozen judgment is committed and replay works in a fresh clone. The demo and the
docs build are offline by default; `--live` is the only mode that calls Jev or
writes a recording, and nothing deletes one. Scratch stays in `runs/`.

Related cause worth recording: `recordings/` being gitignored is also why
`AGENT_LAB_JUDGMENT=recorded` could not work from a fresh clone. Separately, `seq`
being derived from the whole log file meant the demo's run directory accumulated
across invocations, and a published `walkthrough.html` reported
`replay identical to live run: False` for a run that was in fact identical — see
"Beyond this report".

## Summary

**9 findings:** 4 high, 5 medium. The most urgent fixes are the approval/rejection model and restoring equivalent safety/audit behavior in the graph driver.

## Beyond this report

Found while acting on the findings above. Fixed in the same branch.

- **The transition allow-list did not enforce source stages.** Every terminal
  move was declared `(None, None)`, so `ESCALATE_REVIEW` and `REJECT` were legal
  from ANY stage despite being named `route->needs_review` and `route->rejected`.
  Each move now declares its source, `TERMINATES` names the terminal it sets, and
  both tables are read-only mappings. Fixed in `d6d4ac8`.
- **`seq` was derived from the whole log file** (`len(deps.log.read())`), so an
  unrelated run sharing a log shifted this run's numbering, and the file was
  re-parsed on every run. It now counts only this run's events. Fixed in
  `6571a5e`.
- **Dead code with a stale reference.** `NODE_BY_STAGE` was defined and never
  used, and `transition_for` / `TRANSITIONS_BY_NODE` were never called while
  referring to a "lesson 06" that does not exist. The table is now wired and the
  unused pair is deleted. Fixed in `6571a5e`.
- **A regression introduced by fixing finding 8 and caught in testing.** With
  failures becoming terminal states, the graph walked past `FAILED_VALIDATION` at
  `classify` and hit an assertion further down, because only `Route` and `Verify`
  checked for terminals. Nodes now stop wherever a run terminates. Fixed in
  `6571a5e`.

The two misleading-test observations below are recorded rather than fixed, since
neither is a defect in behaviour:

- `test_graph_driver_stops_on_a_terminal` did not test a terminal handed IN, only
  a terminal produced during a graph run. A real terminal-re-entry test now
  exists alongside it.
- `test_concatenation_collides_and_nul_joining_does_not` asserted that NUL joining
  does not collide, which finding 6 shows is false for inputs containing NUL. It
  is renamed and now asserts the collision.
