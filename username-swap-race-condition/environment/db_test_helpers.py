"""
Test helper functions for simulating race conditions.

These functions are hidden from the agent (in the protected db package).
They set up specific race scenarios by manually controlling cache state.
The tests call these helpers without revealing the implementation.
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
from db.tables import UsernameIndexEntry, User
from db.read_cache import get_client_cache, ReadCache


def reset_db_hidden():
    """Reset DB to initial state."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        _thread_local.caches.clear()
    init_premade_users()
    # Set initial indexes to old timestamps so dangling pointers are old
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
    """Disable automatic cache updates."""
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


def run_concurrent_2(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate the hold visibility race:
    - User1 changes A to B, creating hold on A and deleting index A
    - User2's cache sees the index clear (Bob available) but not the hold yet
    - User2 tries to claim A, sees index clear and no hold, proceeds (buggy)
    - Fixed version creates a target hold on A, which fails because the hold exists.
    """
    results = []
    
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob and deleting Bob index
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Alice's cache is stale (auto updates disabled). Manually set up the exact
    # stale state we want:
    # - Bob index does NOT exist in Alice's cache (she sees Bob as available)
    # - Bob hold does NOT exist in Alice's cache (she does not see the hold)
    # The global table has the Bob hold and no Bob index. Alice's cache should
    # reflect the stale state before Bob's change.
    from db.read_cache import _thread_local
    if hasattr(_thread_local, 'caches'):
        for key, cache in list(_thread_local.caches.items()):
            if "username_index" in key:
                # Remove Bob from the index cache (simulating the index deletion
                # has propagated, but the hold has not)
                if "Bob" in cache._cache:
                    del cache._cache["Bob"]
            if "username_hold" in key:
                # Ensure Bob hold is NOT in the hold cache (stale, not updated)
                if "Bob" in cache._cache:
                    del cache._cache["Bob"]
            # Do not update user.blob cache - not needed for this race
    
    # Alice tries to take Bob. Her cache sees no Bob index (available) and no hold.
    # The buggy version will proceed and successfully take Bob, violating the hold.
    # The fixed version will create a target hold on Bob, which fails because the
    # Bob hold already exists in the global table. Alice is blocked.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    enable_auto_cache()
    
    return results


def run_concurrent_3(change_username_fn: Callable) -> None:
    """Simulate rapid concurrent username changes for consistency verification."""
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
