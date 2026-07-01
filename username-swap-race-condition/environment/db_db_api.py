"""
Public DB API exposed to the username service.

All read operations go through the per-client per-table read cache (may be stale).
All write operations go to the global instance with per-pkey locking (strong consistency).
"""

from typing import Optional, List, Dict, Any

# Configurable intervals
CACHE_UPDATE_INTERVAL = 0.5
DANGLING_POINTER_LOCKOUT = 2 * CACHE_UPDATE_INTERVAL
HOLD_TIME_SECONDS = 3

# Read Operations (from read cache, per-client per-table instances)


def read_user_by_id(user_id: int) -> Optional[Any]:
    """
    Read user blob by primary key from cache.

    Returns User with id, username, email. May be stale.
    """
    pass


def read_username_index(username: str) -> Optional[Any]:
    """
    Read username index by username from cache.

    Returns entry with user_id, time_created. May be stale.
    """
    pass


def read_username_hold(username: str) -> Optional[Any]:
    """
    Read username hold by username from cache.

    Returns entry with user_id, time_created, time_expired. May be stale.
    """
    pass


# Write Operations (to global instance with per-pkey locking)


def create_username_index(username: str, user_id: int) -> bool:
    """
    Create a new username index entry.

    Fails if pkey (username) already exists.
    Automatically adds time_created.
    """
    pass


def delete_username_index(username: str, user_id: int) -> bool:
    """
    Delete a username index entry by username and user_id.

    Succeeds even if no matching entry (idempotent).
    If username points to a different user_id, the delete is a no-op and still succeeds.
    """
    pass


def create_username_hold(username: str, user_id: int, hold_expire_time: int) -> bool:
    """
    Create a new username hold.

    hold_expire_time is the absolute timestamp when the hold expires.
    Fails if pkey (username) already has a hold.
    Automatically adds time_created.
    """
    pass


def delete_username_hold(username: str, user_id: int, hold_expire_time: int) -> bool:
    """
    Delete a username hold by username, user_id, and hold_expire_time.

    Succeeds even if no matching entry (idempotent).
    The hold_expire_time must match the hold's time_expired within 0.1 seconds.
    """
    pass


def update_user_username(user_id: int, new_username: str) -> bool:
    """
    Update a user's username in the user blob.

    Fails if user_id does not exist.
    """
    pass


def atomic_changeset(operations: List[Dict[str, Any]]) -> bool:
    """
    Execute multiple operations atomically.

    Acquires global locks for all pkeys involved before starting.
    Backs up current state of all pkeys.
    If any operation fails, reverts to initial state and returns False.
    If all succeed, returns True.

    Operations can be:
    - create_username_index: {op, username, user_id}
    - delete_username_index: {op, username, user_id}
    - create_username_hold: {op, username, user_id, hold_expire_time}
    - delete_username_hold: {op, username, user_id, hold_expire_time}
    - update_user_username: {op, user_id, new_username}

    BLOCKS: Deleting and writing the same data in the same changeset is not
    allowed. For example, delete_username_index("bob", 123) followed by
    create_username_index("bob", 123) in the same changeset will be blocked.
    Similarly, create then delete of the same username is also blocked.
    This is tracked by the username for index/hold operations (regardless of
    user_id), and by user_id for user blob operations. This prevents using
    delete/create or create/delete as a way to bypass the "create should fail
    if pkey exists" rule or as a master read stand-in to check if a key exists.
    """
    pass


def get_cache_update_interval() -> float:
    """Get the cache update interval in seconds."""
    pass


def get_dangling_pointer_lockout() -> float:
    """Get the dangling pointer lockout period in seconds."""
    pass


def get_hold_time_seconds() -> int:
    """Get the default hold time in seconds."""
    pass


def init_premade_users():
    """Initialize the premade users: Bob (1), Alice (2), Tom (3)."""
    pass
