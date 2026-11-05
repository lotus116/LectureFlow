"""Exclusive advisory file locks shared by Unix and Windows processes."""

import os

if os.name != "nt":
    from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
else:
    import errno
    import msvcrt

    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    def flock(handle, operation):
        # Lock byte zero even for empty files. Closing the handle releases the lock.
        position = handle.tell()
        handle.seek(0)
        try:
            mode = msvcrt.LK_UNLCK if operation == LOCK_UN else (
                msvcrt.LK_NBLCK if operation & LOCK_NB else msvcrt.LK_LOCK
            )
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as error:
                if operation != LOCK_UN and error.errno in {errno.EACCES, errno.EDEADLK}:
                    raise BlockingIOError(errno.EAGAIN, "File is locked") from error
                raise
        finally:
            handle.seek(position)

__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_UN", "flock"]
