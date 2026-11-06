"""A second process must fail promptly; killing a holder must release the kernel lease."""
import subprocess
import sys

from lectureflow import filelock


def test_cross_process_lease_survives_contention_and_process_exit(tmp_path):
    path = tmp_path / ".structure.lock"
    holder = """
import sys
from lectureflow import filelock
with open(sys.argv[1], "a+") as lock:
    filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
    print("held", flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", holder, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as process:
        try:
            assert process.stdout.readline().strip() == "held"
            import pytest

            with path.open("a+") as lock, pytest.raises(BlockingIOError):
                filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        finally:
            process.kill()
            process.wait(timeout=10)
    with path.open("a+") as lock:
        filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
