"""
Public DB API exposed to the username service.

All read operations go through the per-client per-table read cache (may be stale).
All write operations go to the global instance with per-pkey locking (strong consistency).
"""

from typing import Optional, List, Dict, Any

from db.tables import User, UsernameIndexEntry, UsernameHoldEntry
from db.read_cache import get_client_cache
from db.changeset import atomic_changeset as _atomic_changeset

# Configurable intervals
CACHE_UPDATE_INTERVAL = 0.5
DANGLING_POINTER_LOCKOUT = 2 * CACHE_UPDATE_INTERVAL
HOLD_TIME_SECONDS = 3  # Increased for testing  # Default hold duration in seconds  # Turned down from 15 for reasonable test execution


# Read Operations (from read cache, per-client per-table instances)


def read_user_by_id(user_id: int) -> Optional[User]:
    """
    Read user blob by primary key from cache.

    Returns User with id, username, email. May be stale.
    """
    cache = get_client_cache("user_blob", CACHE_UPDATE_INTERVAL)
    return cache.get(user_id)


def read_username_index(username: str) -> Optional[UsernameIndexEntry]:
    """
    Read username index by username from cache.

    Returns entry with user_id, time_created. May be stale.
    """
    cache = get_client_cache("username_index", CACHE_UPDATE_INTERVAL)
    return cache.get(username)


def read_username_hold(username: str) -> Optional[UsernameHoldEntry]:
    """
    Read username hold by username from cache.

    Returns entry with user_id, time_created, time_expired. May be stale.
    """
    cache = get_client_cache("username_hold", CACHE_UPDATE_INTERVAL)
    return cache.get(username)


# Write Operations (to global instance with per-pkey locking)


def create_username_index(username: str, user_id: int) -> bool:
    """
    Create a new username index entry.

    Fails if pkey (username) already exists.
    Acquires global lock for the username pkey.
    Automatically adds time_created.

    Returns:
        True if created successfully, False if username already exists
    """
    from db.tables import username_index_table
    return username_index_table.create(username, user_id)


def delete_username_index(username: str, user_id: int) -> bool:
    """
    Delete a username index entry by username and user_id.

    Succeeds even if no matching entry exists (idempotent).
    If current entry is bob:123 and we delete bob:456, it succeeds and bob:123 remains.
    Acquires global lock.

    Returns:
        True always (idempotent success)
    """
    from db.tables import username_index_table
    return username_index_table.delete(username, user_id)


def create_username_hold(
    username: str, user_id: int, hold_expire_time: float = HOLD_TIME_SECONDS
) -> bool:
    """
    Create a new username hold entry.

    Fails if pkey (username) already exists.
    Acquires global lock for the username pkey.
    Automatically adds time_created and uses the provided hold_expire_time.

    Returns:
        True if created successfully, False if username already has a hold
    """
    from db.tables import username_hold_table
    return username_hold_table.create(username, user_id, hold_expire_time)


def delete_username_hold(
    username: str, user_id: int, hold_expire_time: float = HOLD_TIME_SECONDS
) -> bool:
    """
    Delete a username hold by username, user_id, and hold_expire_time.
    Acquires global lock.

    Returns:
        True if deleted or if no matching entry existed
    """
    from db.tables import username_hold_table
    return username_hold_table.delete(username, user_id, hold_expire_time)


def update_user_username(user_id: int, new_username: str) -> bool:
    """
    Update user blob's username field.
    Acquires global lock for the user_id pkey.

    Returns:
        True if updated, False if user not found
    """
    from db.tables import user_blob_table
    return user_blob_table.update_username(user_id, new_username)


# Atomic Changeset


def atomic_changeset(operations: List[Dict[str, Any]]) -> bool:
    """
    Accepts arbitrary number of create/delete/update operations.

    Acquires global locks for all pkeys involved before starting.
    Backs up current state of all pkeys.
    If any operation fails, reverts to initial state and returns False.
    If all succeed, returns True.
    Operations are executed in order given.

    Operation types:
    - {'op': 'create_username_index', 'username': str, 'user_id': int}
    - {'op': 'delete_username_index', 'username': str, 'user_id': int}
    - {'op': 'create_username_hold', 'username': str, 'user_id': int, 'hold_expire_time': int}
    - {'op': 'delete_username_hold', 'username': str, 'user_id': int, 'hold_expire_time': int}
    - {'op': 'update_user_username', 'user_id': int, 'new_username': str}

    Returns:
        True if all operations succeeded, False if any failed (and reverted)
    """
    return _atomic_changeset(operations)


# Utility


def get_cache_update_interval() -> float:
    """Returns current CACHE_UPDATE_INTERVAL in seconds."""
    return CACHE_UPDATE_INTERVAL


def get_dangling_pointer_lockout() -> float:
    """Returns DANGLING_POINTER_LOCKOUT period (2x cache interval)."""
    return DANGLING_POINTER_LOCKOUT


def get_hold_time_seconds() -> int:
    """Returns HOLD_TIME_SECONDS."""
    return HOLD_TIME_SECONDS


# Initialization


def init_premade_users():
    """Create the three premade users: Bob (1), Alice (2), Tom (3)."""
    from db.tables import username_index_table, username_hold_table, user_blob_table
    user_blob_table.create(1, "Bob", "bob@example.com")
    user_blob_table.create(2, "Alice", "alice@example.com")
    user_blob_table.create(3, "Tom", "tom@example.com")

    username_index_table.create("Bob", 1)
    username_index_table.create("Alice", 2)
    username_index_table.create("Tom", 3)


# Initialize premade users on module load
init_premade_users()
