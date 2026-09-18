#!/usr/bin/env python3
"""Build a single self-contained HTML walkthrough from the real source files.

    .venv/bin/python scripts/build_walkthrough.py

Why generate it instead of hand-writing it: the code samples, the file list and
the test output are read from disk, so the walkthrough cannot drift from the
lab. Run it again after editing the lab.
"""

from __future__ import annotations

import html
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LESSONS = [
    {
        "n": "01",
        "title": "Encoding problems",
        "file": "agent_lab/encoding.py",
        "tests": "lessons/lesson_01_encoding/test_encoding.py",
        "why": (
            "Every hash, comparison and approval in this lab depends on bytes. "
            "Four failure modes are worth knowing by name."
        ),
        "points": [
            "<b>Wrong codec = mojibake.</b> UTF-8 bytes read as cp1252 give the "
            "printable <code>â€™</code> you see in reports. The same bytes read as "
            "latin-1 give invisible C1 control characters instead.",
            "<b>Some corruption is unrecoverable.</b> cp1252 leaves 0x81 0x8D 0x8F "
            "0x90 0x9D undefined, and <code>”</code> is <code>E2 80 9D</code> in "
            "UTF-8. A lossy decode destroys it for good.",
            "<b>Line endings change digests.</b> The same body sent as CRLF and LF "
            "hashes differently, so an approval recorded on Windows stops matching.",
            "<b>NFC vs NFD.</b> Two names that look identical can be different byte "
            "sequences, so they compare unequal and hash differently. Normalise "
            "before hashing.",
            "<b>Never join fields by concatenation.</b> <code>('ab','c')</code> and "
            "<code>('a','bc')</code> concatenate to the same string. Joining with a "
            "NUL byte cannot collide, because NUL cannot appear in the fields.",
        ],
    },
    {
        "n": "02",
        "title": "A workflow you can read",
        "file": "agent_lab/workflow.py",
        "tests": "lessons/lesson_02_workflow/test_workflow.py",
        "why": (
            "The control plane is a loop, a router and an allow-list. The model "
            "never decides where the workflow goes."
        ),
        "points": [
            "<b>Typed state.</b> <code>RunState</code> carries the stage, the "
            "judgment, the artifact digest and a step budget. It is frozen, so a "
            "change is a new value, never a silent mutation.",
            "<b>Illegal transitions raise.</b> <code>state.moved(...)</code> refuses "
            "a move the allow-list does not contain. That turns a router bug into a "
            "loud failure instead of a plausible run.",
            "<b>Terminals mean something.</b> <code>NEEDS_REVIEW</code> is a pause and "
            "can resume. <code>REJECTED</code>, <code>FAILED_VALIDATION</code> and "
            "<code>FAILED_BUDGET</code> are final.",
            "<b>The write-up of the artifact is deterministic.</b> No model writes "
            "the operations checklist; code composes it, so the same inputs give the "
            "same bytes.",
            "<b>Append-only evidence.</b> Every step writes one JSON line. The log is "
            "never rewritten and can be digested as evidence.",
        ],
    },
    {
        "n": "03",
        "title": "Jev as one node",
        "file": "agent_lab/judgment.py",
        "tests": "lessons/lesson_03_jev/test_jev.py",
        "why": (
            "TypeSafe supplies one narrow typed judgment. Swapping the source — live, "
            "recorded, or stub — does not touch the workflow."
        ),
        "points": [
            "<b>Validate at the boundary.</b> <code>_build()</code> rejects any option "
            "outside the three we defined, before the router sees it.",
            "<b>One coherent judgment per question.</b> A <code>Choice</code> for the "
            "intervention plus a <code>Noul</code> for the review-gap signal, batched "
            "in a single call.",
            "<b>Replay is offline and stable.</b> A recording is bound to the "
            "assessment it was made against, so a replayed run reproduces identical "
            "semantics with zero network.",
            "<b>Thresholds live in code.</b> Confidence below 0.60 escalates to review "
            "before any artifact is prepared. That number is visible and testable.",
            "<b>Source selection is explicit.</b> <code>AGENT_LAB_JUDGMENT=jev</code>, "
            "<code>recorded</code> or <code>stub</code>. It is never guessed.",
        ],
    },
    {
        "n": "04",
        "title": "Same workflow, framework driver",
        "file": "agent_lab/graph_workflow.py",
        "tests": "lessons/lesson_02_workflow/test_workflow.py",
        "why": (
            "The workflow is written twice on purpose: once as plain Python, once "
            "with pydantic-graph. Both must produce identical results."
        ),
        "points": [
            "<b>The nodes are one-line delegations.</b> If a framework made you "
            "rewrite your logic, that is a signal, not a feature.",
            "<b>Constraint 1:</b> <code>from __future__ import annotations</code> is "
            "required, because <code>run()</code>'s return annotation names nodes "
            "defined later in the file.",
            "<b>Constraint 2:</b> the graph is handed a node <em>instance</em>. A "
            "graph executes a node object; it does not call a function to start.",
            "<b>Constraint 3:</b> graph state must be mutable and mutated in place — "
            "pydantic-graph builds a fresh context per node, so rebinding "
            "<code>ctx.state</code> is discarded. Our frozen state is wrapped in a "
            "small mutable holder.",
            "<b>What the framework adds:</b> structural validation and a rendered "
            "diagram. What it does not add: your semantics.",
        ],
    },
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def code_block(text: str, *, lang: str = "python") -> str:
    return (
        f'<pre class="code" data-lang="{lang}">'
        f"<code>{html.escape(text)}</code></pre>"
    )


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    return (result.stdout + result.stderr).strip()


def build() -> Path:
    tests = run([".venv/bin/python", "-m", "pytest", "lessons", "-q"])
    live = run([".venv/bin/python", "scripts/live_jev_demo.py"])
    graph = run(
        [
            ".venv/bin/python",
            "-c",
            "import sys; sys.path.insert(0,'.');"
            "from agent_lab.graph_workflow import build_graph;"
            "print(build_graph().render())",
        ]
    )

    lesson_html = []
    for lesson in LESSONS:
        points = "\n".join(f"<li>{p}</li>" for p in lesson["points"])
        lesson_html.append(
            f"""
    <section class="lesson" id="lesson-{lesson['n']}">
      <header class="lesson-head">
        <span class="badge">Lesson {lesson['n']}</span>
        <h2>{html.escape(lesson['title'])}</h2>
      </header>
      <p class="why">{lesson['why']}</p>
      <ul class="points">{points}</ul>
      <details>
        <summary>Show the source: <code>{lesson['file']}</code></summary>
        {code_block(read(lesson['file']))}
      </details>
      <details>
        <summary>Show the tests that pin the behaviour</summary>
        {code_block(read(lesson['tests']))}
      </details>
    </section>"""
        )

    state_src = read("agent_lab/state.py")
    runlog_src = read("agent_lab/runlog.py")
    approvals_src = read("agent_lab/approvals.py")

    quickstart = """# one-time setup
cd ~/Projects/agent-workflow-lab
python3 -m venv .venv
.venv/bin/python -m pip install pydantic pydantic-graph typesafe-sdk pytest

# run the whole lab offline (no API key, no spend)
.venv/bin/python -m pytest lessons -v

# see a live Jev judgment drive the workflow, then freeze it for replay
.venv/bin/python scripts/live_jev_demo.py

# approve the exact artifact and watch the paused run finish
.venv/bin/python scripts/approve_demo.py"""

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Workflow Lab — a walkthrough</title>
<style>
  :root {{
    --bg: #14161a; --panel: #1b1e24; --panel-2: #21252c;
    --ink: #e8e6e1; --ink-dim: #a8a49c; --line: #2e333b;
    --accent: #c9a227; --green: #6fae7f; --red: #c86b5e; --blue: #7aa2c8;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-text-size-adjust: 100%;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 24px 18px 96px; }}
  header.hero {{ border-bottom: 1px solid var(--line); padding-bottom: 22px; margin-bottom: 30px; }}
  h1 {{ font-size: clamp(1.5rem, 5vw, 2.1rem); margin: 0 0 10px; letter-spacing: -0.02em; }}
  .sub {{ color: var(--ink-dim); margin: 0; }}
  .toc {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
          padding: 16px 18px; margin: 24px 0 34px; }}
  .toc h2 {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .13em;
             color: var(--ink-dim); margin: 0 0 10px; font-weight: 600; }}
  .toc ol {{ margin: 0; padding-left: 20px; }}
  .toc a {{ color: var(--ink); text-decoration: none; border-bottom: 1px dotted var(--line); }}
  .toc a:hover {{ color: var(--accent); }}
  section.lesson {{ margin: 0 0 46px; }}
  .lesson-head {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .badge {{ font: 600 .7rem/1 var(--mono); letter-spacing: .1em; text-transform: uppercase;
            color: var(--bg); background: var(--accent); padding: 6px 9px; border-radius: 5px; }}
  h2 {{ font-size: clamp(1.15rem, 4vw, 1.45rem); margin: 0; letter-spacing: -0.01em; }}
  h3 {{ font-size: 1rem; margin: 26px 0 8px; color: var(--ink); }}
  .why {{ color: var(--ink-dim); margin: 12px 0 14px; }}
  ul.points {{ margin: 0 0 18px; padding-left: 20px; }}
  ul.points li {{ margin: 0 0 9px; }}
  code {{ font-family: var(--mono); font-size: .87em; background: var(--panel-2);
          padding: 2px 6px; border-radius: 4px; overflow-wrap: anywhere; }}
  pre.code {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
              padding: 14px 16px; overflow-x: auto; font-size: .78rem; line-height: 1.55;
              -webkit-overflow-scrolling: touch; }}
  pre.code code {{ background: none; padding: 0; font-size: inherit; }}
  .out {{ background: #101216; border: 1px solid var(--line); border-left: 3px solid var(--green);
          border-radius: 10px; padding: 14px 16px; overflow-x: auto;
          font: .76rem/1.55 var(--mono); white-space: pre; color: #cfd3d8; }}
  details {{ border: 1px solid var(--line); border-radius: 10px; margin: 0 0 12px;
             background: var(--panel); }}
  details[open] {{ background: var(--panel); }}
  summary {{ cursor: pointer; padding: 13px 16px; font-weight: 600; font-size: .9rem;
             list-style: none; }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: "▸ "; color: var(--accent); }}
  details[open] summary::before {{ content: "▾ "; }}
  details > pre.code, details > p {{ margin: 0 16px 16px; }}
  .callout {{ background: var(--panel-2); border-left: 3px solid var(--accent);
              border-radius: 8px; padding: 13px 16px; margin: 18px 0; font-size: .93rem; }}
  .callout.warn {{ border-left-color: var(--red); }}
  .callout.ok {{ border-left-color: var(--green); }}
  footer {{ border-top: 1px solid var(--line); margin-top: 46px; padding-top: 18px;
            color: var(--ink-dim); font-size: .85rem; }}
  @media (max-width: 520px) {{
    .wrap {{ padding: 18px 13px 80px; }}
    pre.code {{ font-size: .72rem; padding: 12px; }}
    .out {{ font-size: .68rem; }}
  }}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>Agent Workflow Lab</h1>
  <p class="sub">A small, readable agent workflow — typed state, explicit
  transitions, one Jev judgment, and a human approval gate you cannot bypass
  by accident.</p>
</header>

<div class="toc">
  <h2>What this walks you back through</h2>
  <ol>
    <li><a href="#quickstart">Run it in two minutes</a></li>
    <li><a href="#shape">The shape of the thing</a></li>
    <li><a href="#lesson-01">Lesson 01 — Encoding problems</a></li>
    <li><a href="#lesson-02">Lesson 02 — A workflow you can read</a></li>
    <li><a href="#lesson-03">Lesson 03 — Jev as one node</a></li>
    <li><a href="#lesson-04">Lesson 04 — Same workflow, framework driver</a></li>
    <li><a href="#evidence">Live evidence from this machine</a></li>
    <li><a href="#limits">Honest limits</a></li>
  </ol>
</div>

<section class="lesson" id="quickstart">
  <div class="lesson-head"><span class="badge">Start</span><h2>Run it in two minutes</h2></div>
  <p class="why">Nothing here needs an API key except the optional live Jev call.
  The default judgment source is a stub, so the tests never spend money.</p>
  {code_block(quickstart, lang="bash")}
</section>

<section class="lesson" id="shape">
  <div class="lesson-head"><span class="badge">Shape</span><h2>The shape of the thing</h2></div>
  <p class="why">Nine small files. Read them in this order; each one is short
  enough to hold in your head at once.</p>
  {code_block('''agent_lab/state.py          typed state, legal transitions, terminals, budgets
agent_lab/encoding.py       the encoding rules that stop silent corruption
agent_lab/judgment.py       where a typed model judgment comes from
agent_lab/credentials.py    where the Jev key comes from (pointer, no baked paths)
agent_lab/runlog.py         append-only run evidence
agent_lab/approvals.py      human approval bound to an exact digest
agent_lab/workflow.py       the workflow as a PLAIN Python state machine
agent_lab/graph_workflow.py the same workflow driven by pydantic-graph
scripts/live_jev_demo.py    live Jev -> workflow -> approval gate''', lang="text")}

  <div class="callout">
    <b>The rule this lab is built on:</b> build the semantics, adopt the
    infrastructure. The workflow definition and the evaluation contract are
    yours. Checkpoint storage, tracing and durable execution are replaceable
    later, and you should not build them on day one.
  </div>

  <h3>The path a run takes</h3>
  {code_block('''intake  ->  classify  ->  route  ->  prepare  ->  verify  ->  await_approval
                 |             |                                        |
                 |             +-- not a fit -----------------> REJECTED (final)
                 |             +-- confidence < 0.60 --------> NEEDS_REVIEW (pause)
                 +-- bad input ------------------------------> raises before any call

await_approval:  exact digest approved  ->  SUCCESS
                 anything else          ->  NEEDS_REVIEW (still paused)''', lang="text")}
</section>

{''.join(lesson_html)}

<section class="lesson" id="evidence">
  <div class="lesson-head"><span class="badge">Evidence</span><h2>Live evidence from this machine</h2></div>
  <p class="why">Real output, captured by <code>scripts/build_walkthrough.py</code>
  when this page was generated.</p>

  <h3>Tests</h3>
  <div class="out">{html.escape(tests)}</div>

  <h3>Live Jev run, frozen for replay, then replayed</h3>
  <div class="out">{html.escape(live)}</div>

  <h3>The graph the framework builds</h3>
  <div class="out">{html.escape(graph)}</div>
</section>

<section class="lesson" id="limits">
  <div class="lesson-head"><span class="badge">Limits</span><h2>Honest limits</h2></div>
  <ul class="points">
    <li>This is a teaching lab, not production infrastructure. There is no
    checkpoint store, no distributed workers, no crash recovery across
    machines, and no tracing backend.</li>
    <li>The approval store is an append-only local file. It records who was
    named as approver; it does not authenticate them.</li>
    <li><code>.env</code> and the pointer it can hold are conveniences, not a
    secrets manager: a key written there is plaintext on disk. Point
    <code>TYPESAFE_ENV_FILE</code> at a real secret store for anything more.</li>
    <li>The stub and recorded sources make behaviour deterministic; a live Jev
    call may differ run to run. Only <code>recorded</code> is replay-stable.</li>
    <li>Thresholds (0.60 confidence) are illustrative. Real thresholds need
    labelled examples from your own traffic.</li>
    <li>The synthetic case is invented test data. No real business, prospect or
    client information is in this lab.</li>
  </ul>
  <div class="callout warn">
    <b>Never delegated to the model in this design:</b> permission checks,
    arithmetic, source truth, approval, sending, and every irreversible action.
  </div>
</section>

<footer>
  MIT licensed — see <code>LICENSE</code>. Use it, modify it, sell it; keep the
  copyright notice. Regenerate this page with
  <code>.venv/bin/python scripts/build_walkthrough.py</code>. The code samples,
  test output and graph diagram are read from the lab at build time, so the page
  cannot drift from it.
</footer>

</div>
</body>
</html>
"""

    out = ROOT / "walkthrough.html"
    out.write_text(doc, encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    path = build()
    size = path.stat().st_size
    print(f"wrote {path} ({size:,} bytes)")
    sys.exit(0)
