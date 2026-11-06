"""A second process must fail promptly; killing a holder must release the kernel lease."""
import os
import signal
import subprocess
import sys

import pytest

from lectureflow import filelock


def test_cross_process_lease_survives_contention_and_process_exit(tmp_path):
    path = tmp_path / ".structure.lock"
    holder = """
import os
import sys
from lectureflow import filelock
with open(sys.argv[1], "a+") as lock:
    filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
    print(os.getpid(), flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", holder, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as process:
        # A Windows venv launcher can have a different PID from the lock owner.
        owner = int(process.stdout.readline())
        try:
            with path.open("a+") as lock, pytest.raises(BlockingIOError):
                filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        finally:
            os.kill(owner, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            process.wait(timeout=10)
    with path.open("a+") as lock:
        filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
