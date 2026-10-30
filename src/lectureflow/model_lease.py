from __future__ import annotations

import fcntl
from pathlib import Path


def live_requested(root: Path) -> bool:
    with (root / ".live-priority.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
