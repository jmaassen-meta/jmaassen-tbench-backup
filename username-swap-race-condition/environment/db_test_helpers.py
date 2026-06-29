"""
Test helper functions for simulating race conditions.

These functions are hidden from the agent (in the protected db package).
They set up specific race scenarios by manually controlling cache update order
and DB state. The tests call these helpers without revealing the implementation.
"""

import time
import threading
from typing import List, Tuple, Callable

from db.db_api import (
    read_user_by_id,
    read_username_index,
    read_username_hold,
    init_premade_users,
    get_cache_update_interval,
    get_dangling_pointer_lockout,
)
from db.tables import username_index_table, username_hold_table, user_blob_table
from db.tables import UsernameIndexEntry
from db.read_cache import get_client_cache, ReadCache


def reset_db_hidden():
    """
    Reset DB to initial state. Hidden implementation.
    
    Goes to manual cache control, forces a refresh with the fresh DB state,
    then sets back to auto. This ensures the cache has the correct initial
    state without relying on timing.
    """
    from db.read_cache import _thread_local
    
    # Go to manual to prevent auto updates during reset
    ReadCache.disable_auto_update()
    
    # Clear all state
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    
    # Clear thread-local caches to ensure fresh caches for this test
    if hasattr(_thread_local, 'caches'):
        _thread_local.caches.clear()
    
    # Re-initialize the premade users
    init_premade_users()
    
    # Force a cache refresh with the fresh DB state (while still in manual mode)
    force_cache_update()
    
    # Set back to auto for normal test execution (unless a specific test disables it)
    ReadCache.enable_auto_update()


def force_cache_update():
    """Force all per-client caches to update from global state."""
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        for cache in _thread_local.caches.values():
            cache._update_from_global()


def disable_auto_cache():
    """Disable automatic cache updates for deterministic testing."""
    ReadCache.disable_auto_update()


def enable_auto_cache():
    """Enable automatic cache updates."""
    ReadCache.enable_auto_update()


def run_concurrent_1(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate concurrent username claims to the same target.
    
    Uses a barrier to ensure both threads start the critical section together,
    deterministically triggering the race condition.
    """
    results = []
    barrier = threading.Barrier(2)
    
    def try_claim(uid, target):
        barrier.wait()
        success, msg = change_username_fn(uid, target)
        results.append((uid, success))
    
    threads = [
        threading.Thread(target=try_claim, args=(2, "Charlie")),
        threading.Thread(target=try_claim, args=(3, "Charlie")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    return results


def run_concurrent_2(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate concurrent changes where one user changes while another tries
    to claim the old username. The second user's cache may not see the hold yet.
    """
    results = []
    
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob in global table
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Alice's cache was not updated (auto updates disabled), so she does not see
    # the hold on Bob or the index change. Her cache still has the old data.
    # Alice tries to take Bob.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    enable_auto_cache()
    
    return results


def run_concurrent_3(change_username_fn: Callable) -> None:
    """
    Simulate rapid concurrent username changes for consistency verification.
    
    Uses a barrier to synchronize the threads at the critical point.
    """
    results = []
    barrier = threading.Barrier(2)
    
    def bob_rapid_changes():
        success1, _ = change_username_fn(1, "Robert")
        results.append(("Bob1", success1))
        if success1:
            barrier.wait()
            success2, _ = change_username_fn(1, "Bob")
            results.append(("Bob2", success2))
    
    def alice_tries():
        barrier.wait()
        success, _ = change_username_fn(2, "Bob")
        results.append(("Alice", success))
    
    t1 = threading.Thread(target=bob_rapid_changes)
    t2 = threading.Thread(target=alice_tries)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def setup_dangling_pointer():
    """
    Create a dangling pointer scenario.
    
    Creates a username index entry pointing to user 1, but user 1 has a different
    username. The dangling pointer is created with the current time.
    """
    username_index_table._data["Dangling"] = UsernameIndexEntry(
        user_id=1, time_created=time.time()
    )
    force_cache_update()
    return read_username_index("Dangling")


def setup_expired_hold(username: str):
    """
    Create an expired hold for the given username.
    
    The hold is created with an expire time in the past, so it is already expired.
    """
    from db.tables import UsernameHoldEntry
    now = time.time()
    username_hold_table._data[username] = UsernameHoldEntry(
        user_id=1,
        time_created=now - 10,
        time_expired=now - 5,
    )
    force_cache_update()


def cleanup_test_holds():
    """Clean up holds created during failed attempts to ensure test isolation."""
    holds_to_remove = [u for u, h in username_hold_table._data.items() if u != "Bob"]
    for u in holds_to_remove:
        del username_hold_table._data[u]
    if "Bob" in username_index_table._data:
        del username_index_table._data["Bob"]
    force_cache_update()
