"""
Test helper functions for simulating race conditions.

These functions are hidden from the agent (in the protected db package).
They set up specific race scenarios by calling the agent's code to set up the
global state, then manually controlling the cache to simulate stale caches.
The tests call these helpers without revealing the implementation.
"""

import time
import threading
from typing import List, Tuple, Callable, Dict, Any

from db.db_api import (
    read_user_by_id,
    read_username_index,
    read_username_hold,
    init_premade_users,
    get_cache_update_interval,
    get_dangling_pointer_lockout,
)
from db.tables import username_index_table, username_hold_table, user_blob_table
from db.tables import UsernameIndexEntry, UsernameHoldEntry, User
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


def run_concurrent_2(change_username_fn: Callable) -> Tuple[bool, Dict[str, Any]]:
    """
    Simulate the hold visibility race:
    - Bob changes Bob->Robert, creating hold on Bob and deleting Bob index
    - Alice's cache sees the Bob index deleted (available) but NOT the Bob hold (stale)
    - Alice tries to take Bob. Buggy version sees no index and no hold, proceeds and
      succeeds, violating the hold (Bob has a hold on Bob but Alice now owns Bob).
    - Fixed version creates a target hold on Bob as part of the atomic changeset,
      which fails because the Bob hold already exists in the global table.
    
    Uses the agent's code to set up the global state (Bob's change), then manually
    controls the cache to ensure Alice's cache is stale. This proves the buggy
    version can produce the state that leads to the race.
    
    Returns:
        (violation_occurred, state) - violation_occurred is True if Alice succeeded
        in taking Bob even though Bob has a hold (buggy version), False if Alice was
        blocked (fixed version).
    """
    from db.read_cache import _thread_local
    results = []
    
    # Ensure caches exist before Bob's change, so they are populated with the
    # initial state (no holds). After Bob's change, the caches will be stale
    # because auto updates are disabled.
    get_client_cache("username_index", 0.5)
    get_client_cache("username_hold", 0.5)
    get_client_cache("user_blob", 0.5)
    force_cache_update()
    
    disable_auto_cache()
    
    # Bob changes to Robert, creating hold on Bob and deleting Bob index in global table.
    # The cache is NOT updated (auto updates disabled), so it still has the old state.
    # This proves the buggy version can produce the state that leads to the race.
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))
    
    # Alice's cache is stale (it was populated before Bob's change, with the initial
    # state where Bob hold does not exist and Bob index exists). Manually set it up
    # to see:
    # - No Bob index (Bob available) - index cache does NOT have Bob key. The Bob index
    #   was deleted from the global table by Bob's change. The index cache still has the
    #   old Bob index (stale). We manually remove it from the cache to simulate the
    #   index deletion having propagated, but the hold not yet.
    # - No Bob hold (stale) - hold cache does NOT have Bob key. The Bob hold was created
    #   in the global table by Bob's change, but the hold cache was not updated. The hold
    #   cache does not have the Bob key. We ensure it stays that way.
    if hasattr(_thread_local, 'caches'):
        for key, cache in list(_thread_local.caches.items()):
            if "username_index" in key:
                if "Bob" in cache._cache:
                    del cache._cache["Bob"]
            if "username_hold" in key:
                if "Bob" in cache._cache:
                    del cache._cache["Bob"]
            # Set last_update to now to prevent the cache from updating based on time.
            cache._last_update = time.time()
    
    # Alice tries to take Bob. Her cache sees no Bob index (available) and no hold.
    success, msg = change_username_fn(2, "Bob")
    results.append(("Alice", success))
    
    enable_auto_cache()
    force_cache_update()
    
    # Check if a violation occurred
    violation = False
    state = {}
    
    bob = read_user_by_id(1)
    alice = read_user_by_id(2)
    bob_index = read_username_index("Bob")
    bob_hold = read_username_hold("Bob")
    
    state["bob_username"] = bob.username if bob else None
    state["alice_username"] = alice.username if alice else None
    state["bob_index_user"] = bob_index.user_id if bob_index else None
    state["bob_hold_user"] = bob_hold.user_id if bob_hold else None
    state["alice_success"] = success
    
    # Violation: Alice succeeded in taking Bob even though Bob has a hold on Bob
    if success and bob_hold is not None and bob_hold.user_id == 1:
        violation = True
        state["violation_reason"] = "Alice took Bob even though Bob has a hold"
    
    return violation, state


def run_concurrent_3(change_username_fn: Callable) -> bool:
    """Simulate the rapid change race and return whether a violation occurred."""
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
    
    force_cache_update()
    
    violation = False
    bob = read_user_by_id(1)
    alice = read_user_by_id(2)
    
    if bob and alice and bob.username == alice.username == "Bob":
        violation = True
    
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        if user:
            index = read_username_index(user.username)
            if index:
                if index.user_id != uid:
                    violation = True
            else:
                violation = True
    
    return violation


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
