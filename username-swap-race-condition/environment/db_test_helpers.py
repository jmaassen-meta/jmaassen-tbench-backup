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


def reset_db_hidden():
    """Reset DB to initial state. Hidden implementation."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    init_premade_users()
    time.sleep(get_cache_update_interval() * 2 + 0.1)


def run_concurrent_claim_race(change_username_fn: Callable) -> List[Tuple[str, bool]]:
    """
    Simulate Race Condition 1: Two users try to claim the same available username
    concurrently. Only one should succeed.
    
    Hidden implementation: Creates two threads that try to claim "Charlie"
    at the same time. Returns the results.
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
    
    Hidden implementation: Bob changes to Robert while Alice simultaneously
    tries to take Bob, without waiting for cache updates.
    """
    results = []
    
    def bob_changes():
        success, msg = change_username_fn(1, "Robert")
        results.append(("Bob", success))
    
    def alice_tries():
        success, msg = change_username_fn(2, "Bob")
        results.append(("Alice", success))
    
    t1 = threading.Thread(target=bob_changes)
    t2 = threading.Thread(target=alice_tries)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    return results


def run_rapid_change_race(change_username_fn: Callable) -> None:
    """
    Simulate Race Condition 3: User1 rapidly changes A->B->A while User2
    tries to take A concurrently.
    
    Hidden implementation: Bob rapidly changes Bob->Robert->Bob while Alice
    tries to take Bob concurrently.
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
    
    Hidden implementation: Creates a username index entry pointing to user 1,
    but user 1 has a different username. Returns the time when created.
    """
    username_index_table._data["Dangling"] = UsernameIndexEntry(
        user_id=1, time_created=time.time()
    )
    time.sleep(get_cache_update_interval() + 0.1)
    return read_username_index("Dangling")


def cleanup_test_holds():
    """
    Clean up holds created during failed attempts.
    
    Hidden implementation: Removes all holds except the Bob hold, and deletes
    the Bob index if it exists, to ensure test isolation.
    """
    holds_to_remove = [u for u, h in username_hold_table._data.items() if u != "Bob"]
    for u in holds_to_remove:
        del username_hold_table._data[u]
    if "Bob" in username_index_table._data:
        del username_index_table._data["Bob"]
    time.sleep(get_cache_update_interval() + 0.1)
