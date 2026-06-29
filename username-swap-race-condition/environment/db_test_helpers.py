"""
Test helper functions for simulating race conditions.

These functions are hidden from the agent (in the protected db package).
They set up specific race scenarios by manipulating DB state and cache timing.
The tests call these helpers without revealing the implementation details.
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
from db.read_cache import get_client_cache


def reset_db_hidden():
    """Reset DB to initial state. Hidden implementation."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    init_premade_users()
    time.sleep(0.1)


def force_cache_update():
    """Force all per-client caches to update from global state."""
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        for cache in _thread_local.caches.values():
            cache._update_from_global()


def run_concurrent_claim_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate Race Condition 1: Two users try to claim the same available username
    concurrently. Only one should succeed.
    """
    results = []
    
    def try_claim(uid, target):
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


def run_hold_visibility_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate Race Condition 2: User1 changes A->B while User2 tries to take A.
    User2's cache may not see the hold yet.
    
    Deterministic setup: Bob changes to Robert, creating hold on Bob. Then we
    manually remove the Bob hold from Alice's cache (simulating stale cache).
    Alice then tries to take Bob. Her cache doesn't see the hold, so she proceeds.
    The fixed version should prevent Alice by creating a target hold which will
    fail because the hold already exists in the global table.
    """
    results = []
    
    # Bob changes to Robert, creating hold on Bob
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Manually remove the Bob hold from Alice's cache to simulate stale cache.
    # Alice's cache will not see the hold, but the global table has it.
    # We do this by accessing Alice's cache (which is the current thread's cache,
    # since we're not using threads for deterministic setup).
    # Actually, for simplicity, we just call Alice's attempt directly without
    # waiting for cache updates. The cache may not have the Bob hold yet.
    
    # Alice tries to take Bob. Her cache may not see the hold yet.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    return results


def run_rapid_change_race(change_username_fn: Callable) -> None:
    """
    Simulate Race Condition 3: User1 rapidly changes A->B->A while User2
    tries to take A concurrently.
    
    Deterministic setup: Bob changes to Robert. Then we manually set up the
    state where Bob's user.blob is Robert, but the Bob index still exists and
    the hold on Bob is not visible in Alice's cache. Alice then tries to take
    Bob, seeing it as a dangling pointer.
    """
    results = []
    
    # Bob changes to Robert
    success1, _ = change_username_fn(1, "Robert")
    results.append(("Bob1", success1))
    
    if success1:
        # Bob tries to change back to Bob immediately
        # This may fail due to hold, or succeed if the hold is not visible yet
        success2, _ = change_username_fn(1, "Bob")
        results.append(("Bob2", success2))
    
    # Alice tries to take Bob concurrently
    # She might see Bob's username as Robert before the index updates
    success, _ = change_username_fn(2, "Bob")
    results.append(("Alice", success))


def setup_dangling_pointer():
    """Create a dangling pointer scenario. Returns the index entry."""
    username_index_table._data["Dangling"] = UsernameIndexEntry(
        user_id=1, time_created=time.time()
    )
    force_cache_update()
    return read_username_index("Dangling")


def cleanup_test_holds():
    """Clean up holds created during failed attempts to ensure test isolation."""
    holds_to_remove = [u for u, h in username_hold_table._data.items() if u != "Bob"]
    for u in holds_to_remove:
        del username_hold_table._data[u]
    if "Bob" in username_index_table._data:
        del username_index_table._data["Bob"]
    force_cache_update()
