"""Root conftest — make the src-layout package importable in pytest.

`taskq_api` lives under `03-development/src/`, so a bare `pytest` from the
project root cannot import it without the src directory on `sys.path`. We
insert it here so test modules can simply `from taskq_api.app import app`,
and pytest's own rootdir discovery picks up this file as the project's
canonical root marker.

This file is intentionally tiny — every line has a job.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "03-development" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))