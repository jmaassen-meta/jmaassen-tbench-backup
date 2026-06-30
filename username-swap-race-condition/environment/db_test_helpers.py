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

    Also sets the initial indexes to have old timestamps, so that when a
    dangling pointer scenario is set up, the stale index in the cache will
    have an old time_created, ensuring the dangling pointer age is greater
    than the lockout period. This makes the race conditions reliably trigger
    without the lockout blocking prematurely.
    """
    from db.read_cache import _thread_local

    # Go to manual to prevent auto updates during reset
    ReadCache.disable_auto_update()

    # Clear all state
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()

    # Clear thread-local caches to ensure fresh caches for this test
    if hasattr(_thread_local, "caches"):
        _thread_local.caches.clear()

    # Re-initialize the premade users
    init_premade_users()

    # Set the initial indexes to have old timestamps, so dangling pointers
    # are old and the lockout does not block. This ensures the race conditions
    # are reliably triggered.
    old_time = time.time() - 10.0  # 10 seconds ago, older than the 1.0s lockout
    for username, entry in username_index_table._data.items():
        entry.time_created = old_time

    # Force a cache refresh with the fresh DB state (while still in manual mode)
    force_cache_update()

    # Set back to auto for normal test execution (unless a specific test disables it)
    ReadCache.enable_auto_update()
    time.sleep(0.1)
    force_cache_update()


def force_cache_update():
    """Force all per-client caches to update from global state."""
    from db.read_cache import _thread_local

    if hasattr(_thread_local, "caches"):
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


def run_concurrent_2(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate concurrent changes where one user changes while another tries
    to claim the old username. The second user's cache may not see the hold yet.
    """
    results = []

    disable_auto_cache()

    # Bob changes to Robert, creating hold on Bob in global table
    # and deleting the Bob index from the global table.
    success, msg = change_username_fn(1, "Robert")
    results.append(("Bob", success))

    # Manually update only the user.blob cache (so we see Bob's username as Robert),
    # but do NOT update the hold cache or index cache.
    # This simulates the race where user.blob updates before index/hold in distributed caches.
    from db.read_cache import _thread_local

    if hasattr(_thread_local, "caches"):
        for key, cache in list(_thread_local.caches.items()):
            if "user_blob" in key:
                cache._update_from_global()
            # Do not update username_index or username_hold caches - keep them stale
            # The index cache still has the old Bob index (stale), and the hold cache
            # does not have the Bob hold (stale). The stale Bob index has an old
            # time_created (from initialization), so its age is greater than the lockout.

    # Alice's cache now sees:
    # - Bob's user.blob username = "Robert" (updated)
    # - Bob index still points to user 1 (stale, not updated) with age > lockout
    # - No hold on Bob (stale, not updated)
    # So Alice thinks Bob is a dangling pointer (age > lockout) and tries to claim it.
    # The buggy version will delete the dangling pointer and successfully take Bob.
    # The fixed version should prevent Alice by creating a target hold on Bob,
    # which will fail because the Bob hold already exists in the global table.

    # Alice tries to take Bob. Her cache does not see the hold.
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
    """
    Simulate a user reclaiming their old username while another user
    tries to claim it concurrently. The original owner should succeed,
    the other user should fail.
    """
    results = []

    # Bob changes to Robert, creating hold on Bob
    success, _ = change_username_fn(1, "Robert")
    results.append(("Bob1", success))

    # Clean up the hold on Robert (target hold from Bob's change) so Bob's
    # reclaim attempt won't be blocked by the existing hold on his old username.
    # In a real scenario, the user can reclaim their own username even if the
    # hold is still active. The test verifies the reclaim logic.
    if "Robert" in username_hold_table._data:
        del username_hold_table._data["Robert"]

    # Now Bob tries to reclaim Bob, while Alice simultaneously tries to take Bob
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


def setup_multiple_dangling_pointers():
    """
    Create multiple dangling pointers with different ages.

    Creates three dangling pointers:
    - "Dangling1": Recent, age < lockout, should NOT be claimable
    - "Dangling2": Old, age > lockout, should be claimable
    - "Dangling3": Very old, age >> lockout, should be claimable
    """
    now = time.time()
    lockout = get_dangling_pointer_lockout()

    # Recent dangling pointer - should NOT be claimable (age < lockout)
    username_index_table._data["Dangling1"] = UsernameIndexEntry(
        user_id=1,
        time_created=now - 0.1,  # Very recent
    )
    # Old dangling pointer - should be claimable (age > lockout)
    username_index_table._data["Dangling2"] = UsernameIndexEntry(
        user_id=2,
        time_created=now - lockout - 1.0,  # Older than lockout
    )
    # Very old dangling pointer - should be claimable (age >> lockout)
    username_index_table._data["Dangling3"] = UsernameIndexEntry(
        user_id=3,
        time_created=now - lockout - 10.0,  # Much older than lockout
    )
    force_cache_update()


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
