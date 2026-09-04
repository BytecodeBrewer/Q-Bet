from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

application = import_module("qbet.web.wsgi").application

__all__ = ["application"]
