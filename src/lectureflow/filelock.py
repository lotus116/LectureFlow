"""Exclusive, process-scoped nonblocking leases. Closing the handle releases the lease."""

import os

if os.name == "nt":
    import errno
    import msvcrt

    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    def flock(handle, flags):
        fd = handle.fileno()
        handle.seek(0)
        mode = msvcrt.LK_UNLCK if flags == LOCK_UN else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLK):
                raise BlockingIOError("Lease is held by another operation") from error
            raise
else:
    from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock

__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]


def recording_path(root, directory):
    # Windows cannot rename a parent folder with open descendant handles.
    return root / ".windows-recording.lock" if os.name == "nt" else directory / ".recording.lock"
