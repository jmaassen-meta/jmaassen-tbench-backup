"""
Global Lock Manager for per-pkey locking.

Ensures that write operations on the same primary key are serialized,
while allowing concurrency across different keys.
"""

import threading
from contextlib import contextmanager
from typing import List


class GlobalLockManager:
    """Manages per-pkey locks for global write serialization."""

    def __init__(self):
        self._locks = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, pkey: str) -> threading.Lock:
        """Get or create a lock for the given pkey."""
        with self._locks_lock:
            if pkey not in self._locks:
                self._locks[pkey] = threading.Lock()
            return self._locks[pkey]

    @contextmanager
    def acquire(self, pkeys: List[str]):
        """
        Acquire locks for all given pkeys in a consistent order to prevent deadlock.

        Args:
            pkeys: List of primary key strings to lock

        Yields:
            None when all locks are acquired
        """
        # Sort pkeys to ensure consistent lock ordering and prevent deadlock
        sorted_pkeys = sorted(set(pkeys))
        acquired_locks = []

        try:
            for pkey in sorted_pkeys:
                lock = self._get_lock(pkey)
                lock.acquire()
                acquired_locks.append(lock)
            yield
        finally:
            # Release in reverse order
            for lock in reversed(acquired_locks):
                lock.release()


# Global lock manager instance
lock_manager = GlobalLockManager()
