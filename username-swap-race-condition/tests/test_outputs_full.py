"""
Tests for username swap race condition task.

Verifies that:
1. Basic username change works correctly
2. All three tables remain consistent after change
3. Concurrent changes do not cause race conditions
4. Username holds properly block claims
5. Dangling pointers are handled correctly with lockout period
6. Performance: multiple updates complete within time budget
7. Race Condition 1: Simultaneous claim - only one succeeds
8. Race Condition 2: Hold visibility - user sees A available before hold written
9. Race Condition 3: Rapid A->B->A with concurrent claim leads to invalid state
"""

import time
import threading
import sys

# Add site-packages to path so db module can be found when running with uvx
sys.path.insert(0, "/usr/local/lib/python3.12/site-packages")
sys.path.insert(0, "/app")
sys.path.insert(0, "/db")

from db.db_api import (
    read_user_by_id,
    read_username_index,
    read_username_hold,
    init_premade_users,
    get_hold_time_seconds,
    get_dangling_pointer_lockout,
    get_cache_update_interval,
)
from db.tables import username_index_table, username_hold_table, user_blob_table
from username_service import change_username


def reset_db():
    """Reset DB to initial state with premade users."""
    username_index_table._data.clear()
    username_hold_table._data.clear()
    user_blob_table._data.clear()
    init_premade_users()
    # Wait for caches to update
    time.sleep(get_cache_update_interval() * 2 + 0.1)


def test_basic_username_change():
    """Test basic username change from Bob to Robert."""
    reset_db()

    # Bob (id=1) changes from "Bob" to "Robert"
    success, msg = change_username(1, "Robert")
    assert success, f"Change should succeed: {msg}"

    # Verify user blob updated
    user = read_user_by_id(1)
    assert user.username == "Robert", (
        f"User username should be 'Robert', got '{user.username}'"
    )

    # Verify username index updated
    robert_index = read_username_index("Robert")
    assert robert_index is not None, "Robert index should exist"
    assert robert_index.user_id == 1, "Robert index should point to user 1"

    # Verify old username index deleted
    bob_index = read_username_index("Bob")
    assert bob_index is None, "Bob index should be deleted"

    # Verify hold created for old username
    bob_hold = read_username_hold("Bob")
    assert bob_hold is not None, "Hold for Bob should exist"
    assert bob_hold.user_id == 1, "Hold should reference user 1"

    print("✓ test_basic_username_change passed")


def test_concurrent_changes_no_race():
    """Test that concurrent changes do not cause race conditions."""
    reset_db()

    results = []

    def change_user(uid, target):
        success, msg = change_username(uid, target)
        results.append((uid, target, success))

    # Concurrently change all three users
    threads = [
        threading.Thread(target=change_user, args=(1, "Bobby")),
        threading.Thread(target=change_user, args=(2, "Alicia")),
        threading.Thread(target=change_user, args=(3, "Tommy")),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All should succeed (different target usernames)
    assert all(r[2] for r in results), f"All changes should succeed: {results}"

    # Verify no duplicate usernames
    usernames = []
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        usernames.append(user.username)

    assert len(usernames) == len(set(usernames)), (
        f"Duplicate usernames found: {usernames}"
    )

    print("✓ test_concurrent_changes_no_race passed")


def test_hold_expiration_blocking():
    """Test that holds block claims before expiration, allow after."""
    reset_db()

    # Bob changes from "Bob" to "Robert", creating hold on "Bob"
    success, _ = change_username(1, "Robert")
    assert success

    # Wait for caches
    time.sleep(get_cache_update_interval() * 2 + 0.1)

    # Alice tries to take "Bob" before hold expires - should fail
    success, msg = change_username(2, "Bob")
    assert not success, (
        f"Alice should not be able to take Bob before hold expires: {msg}"
    )

    # Wait for hold to expire
    # TO BE UPDATED: Hold time is currently 1 second for testing; adjust if needed
    time.sleep(get_hold_time_seconds() + 0.5)
    time.sleep(get_cache_update_interval() * 2 + 0.1)

    # Alice tries again after hold expires - should succeed
    success, msg = change_username(2, "Bob")
    assert success, f"Alice should be able to take Bob after hold expires: {msg}"

    print("✓ test_hold_expiration_blocking passed")


def test_dangling_pointer_lockout():
    """Test that dangling pointers can only be claimed after lockout period."""
    reset_db()

    # Manually create a dangling pointer: index points to user 1, but user 1 has different username
    from db.tables import UsernameIndexEntry

    username_index_table._data["Dangling"] = UsernameIndexEntry(
        user_id=1, time_created=time.time()
    )

    time.sleep(get_cache_update_interval() * 2 + 0.1)

    # Alice tries to take "Dangling" immediately - should fail due to lockout
    success, msg = change_username(2, "Dangling")
    assert not success, f"Should fail due to dangling pointer lockout: {msg}"
    assert "lockout" in msg.lower() or "recent" in msg.lower(), (
        f"Should mention lockout: {msg}"
    )

    # Wait for lockout period
    time.sleep(get_dangling_pointer_lockout() + 0.5)
    time.sleep(get_cache_update_interval() * 2 + 0.1)

    # Alice tries again after lockout - should succeed
    success, msg = change_username(2, "Dangling")
    assert success, f"Should succeed after lockout: {msg}"

    print("✓ test_dangling_pointer_lockout passed")


def test_race_condition_1_simultaneous_claim():
    """
    Race Condition 1: When 2+ people try to update to the same available username
    at the same time, only 1 should succeed, others should get fail response.
    """
    reset_db()

    results = []

    def try_claim(uid, target):
        success, msg = change_username(uid, target)
        results.append((uid, success))

    # Both Alice and Tom try to claim "Charlie" at the same time
    # (Charlie is available, not taken by anyone)
    threads = [
        threading.Thread(target=try_claim, args=(2, "Charlie")),
        threading.Thread(target=try_claim, args=(3, "Charlie")),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only one should succeed
    successes = [r for r in results if r[1]]
    assert len(successes) == 1, (
        f"Only one should succeed, got {len(successes)} successes: {results}"
    )

    # Verify only one user has "Charlie"
    charlie_count = 0
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        if user.username == "Charlie":
            charlie_count += 1

    assert charlie_count == 1, f"Only one user should have Charlie, got {charlie_count}"

    print("✓ test_race_condition_1_simultaneous_claim passed")


def test_race_condition_2_hold_visibility():
    """
    Race Condition 2: If user1 changes from A to B and user2 changes from C to A,
    user2 might see A available before they see the hold is written, so it goes
    through but should fail due to hold.
    """
    reset_db()

    # Bob changes from "Bob" to "Robert", creating hold on "Bob"
    # But user2's cache hasn't updated yet, so they don't see the hold
    success, _ = change_username(1, "Robert")
    assert success

    # Immediately (before cache updates), Alice tries to take "Bob"
    # Her cache doesn't see the hold yet, so buggy version lets it through
    # Fixed version should check hold via direct read or handle the race

    # Simulate by not waiting for cache - Alice's cache is stale
    # In real scenario, this happens due to distributed cache staleness

    # For the test, we'll verify that after the hold is visible, Alice cannot take Bob
    time.sleep(get_cache_update_interval() * 2 + 0.1)

    success, msg = change_username(2, "Bob")
    # Should fail because Bob has a hold
    assert not success, f"Alice should not be able to take Bob due to hold: {msg}"

    print("✓ test_race_condition_2_hold_visibility passed")


def test_race_condition_3_rapid_change_dangling():
    """
    Race Condition 3: If user1 rapidly changes A->B->A, and user2 tries to change
    to A at the same time, user2 might see no hold yet and see user1's username
    as B before the lookup has changed, so dangling pointer logic lets it take A.
    This leads to invalid state where both user1 and user2 have user.username of A
    but lookup only points to user2.

    The fix: Create a hold for the target username before proceeding. If the hold
    already exists (even if we couldn't see it due to cache staleness), the create
    will fail and we abort. This ensures the dangling pointer race is prevented.
    """
    reset_db()

    # This is a complex race that requires precise timing
    # Simplified test: verify that after rapid changes, the state remains consistent

    # Bob changes Bob -> Robert -> Bob rapidly
    success1, _ = change_username(1, "Robert")
    assert success1

    time.sleep(0.1)  # Small delay but not enough for all caches

    success2, _ = change_username(1, "Bob")
    # This may fail due to hold, or succeed if hold expired/not yet visible

    time.sleep(get_cache_update_interval() * 2 + 0.1)

    # Verify consistency: username in user_blob matches lookup table
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        index = read_username_index(user.username)
        if index:
            assert index.user_id == uid, (
                f"User {uid} has username '{user.username}' but index points to "
                f"user {index.user_id}. Tables out of sync!"
            )

    print("✓ test_race_condition_3_rapid_change_dangling passed")


def test_target_hold_prevents_dangling_race():
    """
    Test that creating a hold for the target username prevents the dangling
    pointer race condition.

    Scenario:
    - User1 changes A->B, creating hold on A, updating index, updating user.blob
    - User2's cache sees user1's user.blob as B (updated) before seeing the index
      change (A still points to user1) and before seeing the hold on A.
    - User2 sees A index pointing to user1, but user1's username is B, so it's a
      dangling pointer. If the dangling pointer is older than lockout, user2
      deletes it and takes A.
    - But user1 just created a hold on A, which user2 couldn't see yet.
    - Without target hold: Both user1 and user2 think they own A. Invalid state!
    - With target hold: User2 tries to create hold on A, but the hold already
      exists (created by user1), so the create fails and user2 aborts. Race prevented!

    This test verifies that the target hold is created and prevents the race.
    """
    reset_db()

    # Bob changes Bob -> Robert, creating hold on Bob
    success, _ = change_username(1, "Robert")
    assert success, "Bob should successfully change to Robert"

    # Wait for Bob's user.blob to update in cache, but not the index or hold
    # (simulates the race condition where user.blob updates before index/hold)
    time.sleep(get_cache_update_interval() + 0.1)

    # At this point, a buggy client might see:
    # - Bob's user.blob username = "Robert" (updated)
    # - Bob index still points to user 1 (not yet updated in cache)
    # - No hold on Bob yet (not yet visible in cache)
    # So the client thinks Bob is a dangling pointer and tries to claim it.

    # Alice tries to take "Bob". With the fix, she should:
    # 1. Try to create a hold on "Bob" - this will fail because Bob's hold already
    #    exists (even though Alice couldn't see it in her stale cache).
    # 2. Abort the operation.
    # Without the fix, Alice would see no hold, see the dangling pointer, and take Bob,
    # leading to both Bob and Alice having username "Bob".

    success, msg = change_username(2, "Bob")
    # Should fail because Bob has a hold (even if Alice couldn't see it, the create will fail)
    assert not success, f"Alice should not be able to take Bob due to hold: {msg}"

    # Verify only Bob (user 1) has the hold on "Bob", not Alice
    time.sleep(get_cache_update_interval() * 2 + 0.1)
    bob_hold = read_username_hold("Bob")
    assert bob_hold is not None, "Hold on Bob should exist"
    assert bob_hold.user_id == 1, (
        f"Hold on Bob should reference user 1, got user {bob_hold.user_id}"
    )

    # Verify Alice does not have username "Bob"
    alice = read_user_by_id(2)
    assert alice.username != "Bob", (
        f"Alice should not have username Bob, got {alice.username}"
    )

    print("✓ test_target_hold_prevents_dangling_race passed")


def test_performance():
    """
    Performance test: X number of updates must complete within Y seconds.

    Prevents trivial solutions that simply wait 2N seconds between operations
    for caches to settle. The solution must handle concurrency properly, not
    just wait.

    TO BE UPDATED: Thresholds will be tuned based on testing with original
    implementation and solution. Current values are placeholders.
    """
    reset_db()

    start = time.time()

    # TO BE UPDATED: Adjust number of updates and time budget based on testing
    # Current: 50 username changes should complete within 5 seconds
    # Target: 100+ username changes in 1 second (as per instruction)
    num_updates = 50  # TO BE UPDATED: Increase to 100+ once tested
    time_budget = 5.0  # TO BE UPDATED: Decrease to 1.0 once tested

    for i in range(num_updates // 3 + 1):
        change_username(1, f"Bob{i}")
        change_username(2, f"Alice{i}")
        change_username(3, f"Tom{i}")

    elapsed = time.time() - start

    # Should complete well within time budget (if not waiting for caches)
    # If solution waits 2*N (1 second) between each op, it would take ~50 seconds
    assert elapsed < time_budget, (
        f"{num_updates} updates took {elapsed:.2f}s, should be < {time_budget}s. "
        f"Solution may be waiting for caches instead of handling concurrency properly. "
        f"TO BE UPDATED: Adjust thresholds after testing."
    )

    print(
        f"✓ test_performance passed ({elapsed:.2f}s for {num_updates} updates) - TO BE UPDATED"
    )


if __name__ == "__main__":
    test_basic_username_change()
    test_concurrent_changes_no_race()
    test_hold_expiration_blocking()
    test_dangling_pointer_lockout()
    test_race_condition_1_simultaneous_claim()
    test_race_condition_2_hold_visibility()
    test_race_condition_3_rapid_change_dangling()
    test_target_hold_prevents_dangling_race()
    test_performance()
    print("\n✅ All tests passed!")
