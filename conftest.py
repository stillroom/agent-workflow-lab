"""Repo-root conftest: guarantees `import agent_lab` from any working directory.

`pyproject.toml` sets `pythonpath = ["."]` for the pytest path. This file is the
belt-and-braces version so the lab also works when pytest is pointed at an
absolute path, or when the suite is run by a harness that does not read the
pytest config. Both mechanisms exist because "works only from the repo root" is
exactly the kind of trap that wastes a fresh agent's first ten minutes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
