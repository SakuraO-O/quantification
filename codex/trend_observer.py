#!/usr/bin/env python3
"""Compatibility wrapper for the modular trend observer.

Prefer:
    python -m codex.trend_observer.cli
"""

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codex.trend_observer.cli import main  # noqa: E402


if __name__ == "__main__":
    main()

