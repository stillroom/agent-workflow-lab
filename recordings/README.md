# Recordings

Frozen judgments from real Jev calls, committed on purpose.

`northside-garden-care.json` was produced by an actual live `--live` run against
`cases/northside-garden-care.txt`. It is in the repository so that a fresh clone
can reproduce the full workflow offline: no API key, no network, no spend, and
the same judgment every time.

Each recording is bound to the assessment it was made against (by SHA-256), so a
recording is refused rather than reused if the case file changes.

Refresh it with a real call:

```bash
.venv/bin/python scripts/live_jev_demo.py --live
```

That overwrites the recording and needs `TYPESAFE_API_KEY`; see `.env.example`.
Nothing in this repo deletes a recording. A live call may return different values
between runs, so refreshing is a deliberate act, not a side effect of building
the docs.
