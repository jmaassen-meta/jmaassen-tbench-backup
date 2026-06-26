"""
Three DB tables with global lock protection for writes.

Tables:
1. UsernameIndex: username str -> user_id, time_created
2. UsernameHold: username str -> user_id, time_created, time_expired
3. UserBlob: user_id -> User with username field
"""

import time
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field

from db.lock_manager import lock_manager


@dataclass
class UsernameIndexEntry:
    user_id: int
    time_created: float = field(default_factory=time.time)


@dataclass
class User:
    id: int
    username: str
    email: str = ""


@dataclass
class UsernameHoldEntry:
    user_id: int
    time_created: float = field(default_factory=time.time)
    time_expired: float = 0.0


class UsernameIndexTable:
    """Username to user_id lookup table. Only create/delete allowed."""

    def __init__(self):
        self._data: Dict[str, UsernameIndexEntry] = {}
        self._lock = threading.Lock()

    def create(self, username: str, user_id: int) -> bool:
        """
        Create a new username index entry.

        Fails if pkey (username) already exists.
        Acquires global lock for the username pkey.

        Returns:
            True if created successfully, False if username already exists
        """
        pkey = f"username_index:{username}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                if username in self._data:
                    return False
                self._data[username] = UsernameIndexEntry(user_id=user_id)
                return True

    def delete(self, username: str, user_id: int) -> bool:
        """
        Delete a username index entry by username and user_id.

        Succeeds even if no matching entry exists (idempotent).
        If current entry is bob:123 and we delete bob:456, it succeeds and bob:123 remains.
        Acquires global lock.

        Returns:
            True always (idempotent success)
        """
        pkey = f"username_index:{username}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                if username in self._data:
                    if self._data[username].user_id == user_id:
                        del self._data[username]
                # Always succeed, even if no matching entry
                return True

    def get(self, username: str) -> Optional[UsernameIndexEntry]:
        """Get entry by username. No lock needed for reads (cache handles this)."""
        with self._lock:
            return self._data.get(username)

    def get_all(self) -> Dict[str, UsernameIndexEntry]:
        """Get all entries (for cache updates)."""
        with self._lock:
            return dict(self._data)


class UsernameHoldTable:
    """Username hold table. Only create/delete allowed."""

    def __init__(self):
        self._data: Dict[str, UsernameHoldEntry] = {}
        self._lock = threading.Lock()

    def create(self, username: str, user_id: int, hold_expire_time: float) -> bool:
        """
        Create a new username hold entry.

        Fails if pkey (username) already exists.
        Acquires global lock for the username pkey.
        Automatically adds time_created and uses the provided hold_expire_time for time_expired.

        Returns:
            True if created successfully, False if username already has a hold
        """
        pkey = f"username_hold:{username}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                if username in self._data:
                    return False
                now = time.time()
                self._data[username] = UsernameHoldEntry(
                    user_id=user_id,
                    time_created=now,
                    time_expired=hold_expire_time,
                )
                return True

    def delete(self, username: str, user_id: int, hold_expire_time: float) -> bool:
        """
        Delete a username hold by username, user_id, and hold_expire_time.
        Acquires global lock.

        Returns:
            True if deleted or if no matching entry existed
        """
        pkey = f"username_hold:{username}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                if username in self._data:
                    entry = self._data[username]
                    if entry.user_id == user_id:
                        # Check hold_expire_time matches (within tolerance)
                        expected_expired = hold_expire_time
                        if abs(entry.time_expired - expected_expired) < 0.1:
                            del self._data[username]
                return True

    def get(self, username: str) -> Optional[UsernameHoldEntry]:
        """Get entry by username."""
        with self._lock:
            return self._data.get(username)

    def get_all(self) -> Dict[str, UsernameHoldEntry]:
        """Get all entries (for cache updates)."""
        with self._lock:
            return dict(self._data)


class UserBlobTable:
    """User blob table with user_id as key and User object with username field."""

    def __init__(self):
        self._data: Dict[int, User] = {}
        self._lock = threading.Lock()

    def create(self, user_id: int, username: str, email: str = "") -> User:
        """Create a new user."""
        pkey = f"user_blob:{user_id}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                user = User(id=user_id, username=username, email=email)
                self._data[user_id] = user
                return user

    def update_username(self, user_id: int, new_username: str) -> bool:
        """
        Update user blob's username field.
        Acquires global lock for the user_id pkey.

        Returns:
            True if updated, False if user not found
        """
        pkey = f"user_blob:{user_id}"
        with lock_manager.acquire([pkey]):
            with self._lock:
                if user_id not in self._data:
                    return False
                self._data[user_id].username = new_username
                return True

    def get(self, user_id: int) -> Optional[User]:
        """Get user by user_id."""
        with self._lock:
            return self._data.get(user_id)

    def get_all(self) -> Dict[int, User]:
        """Get all users (for cache updates)."""
        with self._lock:
            return dict(self._data)


# Global table instances
username_index_table = UsernameIndexTable()
username_hold_table = UsernameHoldTable()
user_blob_table = UserBlobTable()
