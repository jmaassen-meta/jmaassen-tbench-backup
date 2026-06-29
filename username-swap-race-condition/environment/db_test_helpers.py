"""
Test helper functions for simulating race conditions.

These functions are hidden from the agent (in the protected db package).
They set up specific race scenarios by manually controlling cache update order.
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
from db.read_cache import get_client_cache, ReadCache


def reset_db_hidden():
    """Reset DB to initial state. Hidden implementation."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    # Clear thread-local caches
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        _thread_local.caches.clear()
    init_premade_users()
    # Enable auto updates by default, then force initial update
    ReadCache.enable_auto_update()
    time.sleep(0.1)
    force_cache_update()


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


def run_concurrent_claim_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate Race Condition 1: Two users try to claim the same available username
    concurrently. Only one should succeed.
    
    Uses concurrent threads to trigger the race. The OS thread scheduler
    determines the order, making the race realistic.
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
    
    Deterministic setup using manual cache control:
    1. Disable auto cache updates
    2. Bob changes to Robert, creating hold on Bob in global table
    3. Manually update Bob's user.blob cache but NOT the username index or hold caches
       (simulating the race where user.blob updates before index/hold)
    4. Alice tries to take Bob. Her cache does not see the hold on Bob or the index
       change, so she thinks Bob is available.
    5. The fixed version should prevent Alice by creating a target hold on Bob,
       which will fail because the hold already exists in the global table.
    """
    results = []
    
    # Disable auto updates for deterministic control
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob in global table
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Manually control cache updates to simulate the race:
    # - Update the user.blob cache so Alice sees Bob's username as "Robert"
    # - Do NOT update the username index cache, so Alice still sees Bob index pointing to user 1
    # - Do NOT update the hold cache, so Alice does not see the hold on Bob
    
    # Get Alice's caches (current thread's caches, since we're not using threads for deterministic setup)
    # Actually, the change_username_fn runs in the current thread, so the caches are for the current thread.
    # We need to simulate Alice having a different cache state than Bob.
    # 
    # For simplicity, we just call Alice's attempt directly. Her cache may not have the
    # Bob hold or the index change yet, depending on when her cache was last updated.
    # The force_cache_update() in reset_db_hidden ensures the cache is fresh at the start.
    # After Bob's change, the global tables are updated but the cache is not (auto updates disabled).
    # So Alice's cache still has the old data (Bob username = "Bob", Bob index points to 1, no hold on Bob).
    
    # Alice tries to take Bob. Her cache does not see the hold or the index change.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    # Re-enable auto updates
    enable_auto_cache()
    
    return results


def run_rapid_change_race(change_username_fn: Callable) -> None:
    """
    Simulate Race Condition 3: User1 rapidly changes A->B->A while User2
    tries to take A concurrently.
    
    Uses concurrent threads to trigger the race.
    """
    results = []
    
    def bob_rapid_changes():
        success1, _ = change_username_fn(1, "Robert")
        results.append(("Bob1", success1))
        if success1:
            success2, _ = change_username_fn(1, "Bob")
            results.append(("Bob2", success2))
    
    def alice_tries():
        time.sleep(0.05)
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
    username. The dangling pointer is created with the current time, so its age
    will be less than the lockout period when the test runs immediately after.
    """
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
