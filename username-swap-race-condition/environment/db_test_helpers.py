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
    """Reset DB to initial state. Hidden implementation."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        _thread_local.caches.clear()
    init_premade_users()
    # Set the initial indexes to have old timestamps, so dangling pointers
    # are old and the lockout does not block. This ensures the race conditions
    # are reliably triggered.
    old_time = time.time() - 10.0
    for username, entry in username_index_table._data.items():
        entry.time_created = old_time
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


def run_concurrent_1(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """Simulate concurrent username claims to the same target."""
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


def run_hold_visibility_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate the hold visibility race:
    - User1 changes A to B, creating hold on A and deleting index A
    - User2's cache sees the index clear (index cache updated) but not the hold yet (hold cache not updated)
    - User2 tries to claim A, sees index clear and no hold, so it allows the claim (buggy version)
    - The fixed version should prevent this by creating a target hold on A, which fails because the hold already exists.
    """
    results = []
    
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob and deleting Bob index
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Manually control cache updates to simulate the race:
    # - Update the index cache so Alice sees the Bob index deleted (Bob is available)
    # - Do NOT update the hold cache, so Alice does not see the hold on Bob yet
    # - Do NOT update the user.blob cache (not needed for this race)
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        for key, cache in list(_thread_local.caches.items()):
            if "username_index" in key:
                cache._update_from_global()
            # Do not update username_hold or user_blob caches - keep them stale
            # The hold cache does not have the Bob hold, so Alice does not see it.
            # The index cache does not have the Bob index (it was deleted), so Alice sees Bob as available.
    
    # Alice tries to take Bob. Her cache sees the Bob index deleted (available) and no hold.
    # The buggy version will allow her to take Bob, even though the hold exists in the global table.
    # The fixed version should prevent her by creating a target hold on Bob, which will fail
    # because the Bob hold already exists in the global table.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    enable_auto_cache()
    
    return results


def run_rapid_change_race(change_username_fn: Callable) -> None:
    """
    Simulate the rapid change race:
    - User1 rapidly changes A->B->A
    - User2's cache sees the old A index (stale), no hold on A (stale), but the updated
      user.blob with username B (so the A index is a dangling pointer)
    - User2 thinks A is a dangling pointer (age > lockout) and tries to claim it,
      deleting the User1->A index even though User1 just changed back to A.
    - This leads to both users having username A, and the index only pointing to User2.
    """
    results = []
    
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob and deleting Bob index
    success1, _ = change_username_fn(1, "Robert")
    results.append(("Bob1", success1))
    
    if success1:
        # Manually update only the user.blob cache to see Bob's username as Robert,
        # but do NOT update the index or hold caches.
        from db.read_cache import _thread_local
        if hasattr(_thread_local, 'caches'):
            for key, cache in list(_thread_local.caches.items()):
                if "user_blob" in key:
                    cache._update_from_global()
                # Do not update username_index or username_hold caches
        
        # Bob changes back to Bob, creating hold on Robert and recreating Bob index
        success2, _ = change_username_fn(1, "Bob")
        results.append(("Bob2", success2))
        
        # Do NOT update any caches. Alice's cache still has the stale state:
        # - user.blob: Bob username = Robert (from manual update after first change)
        # - index: Bob index still points to user 1 (stale, not updated after Bob's changes)
        # - hold: No hold on Bob (stale, not updated)
        # The stale Bob index has an old time_created (from initialization, set to 10s ago),
        # so its age is greater than the lockout. Alice thinks it's a dangling pointer.
    
    # Alice tries to take Bob concurrently.
    # Her cache sees the dangling pointer (index Bob -> 1, but user 1 username = Robert),
    # age > lockout, and no hold. She tries to claim Bob.
    success, _ = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    enable_auto_cache()


def run_concurrent_reclaim_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """Simulate a user reclaiming their old username while another tries concurrently."""
    results = []
    
    success, _ = change_username_fn(1, "Robert")
    results.append(("Bob1", success))
    
    if "Robert" in username_hold_table._data:
        del username_hold_table._data["Robert"]
    
    barrier = threading.Barrier(2)
    
    def bob_reclaims():
        barrier.wait()
        success, _ = change_username_fn(1, "Bob")
        results.append(("Bob2", success))
    
    def alice_tries():
        barrier.wait()
        success, _ = change_username_fn(2, "Bob")
        results.append(("Alice", success))
    
    t1 = threading.Thread(target=bob_reclaims)
    t2 = threading.Thread(target=alice_tries)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    return results


def setup_dangling_pointer():
    """Create a dangling pointer scenario. Returns the index entry."""
    username_index_table._data["Dangling"] = UsernameIndexEntry(
        user_id=1, time_created=time.time()
    )
    force_cache_update()
    return read_username_index("Dangling")


def setup_expired_hold(username: str):
    """Create an expired hold for the given username."""
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
