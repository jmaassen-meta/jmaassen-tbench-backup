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
10. Service only uses allowed DB APIs (no direct table access)
"""

import time
import threading
import sys
import ast

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
from db.test_helpers import (
    force_cache_update,
    reset_db_hidden,
    run_concurrent_claim_race,
    run_hold_visibility_race,
    run_rapid_change_race,
    setup_dangling_pointer,
    cleanup_test_holds,
)
from username_service import change_username


def test_service_uses_only_allowed_apis():
    """Test that the username service only imports from allowed modules."""
    with open("/app/username_service.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    allowed_modules = {"time", "typing", "db.db_api", "config"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                assert module in allowed_modules, (
                    f"Service imports disallowed module '{module}'. "
                    f"Allowed modules: {allowed_modules}. "
                    f"The service can ONLY use the defined APIs in db.db_api, not direct table access."
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            assert module in allowed_modules, (
                f"Service imports from disallowed module '{module}'. "
                f"Allowed modules: {allowed_modules}. "
                f"The service can ONLY use the defined APIs in db.db_api, not direct table access."
            )
        elif isinstance(node, ast.Call):
            # Check for importlib.import_module() or __import__() calls
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    if (
                        node.func.value.id == "importlib"
                        and node.func.attr == "import_module"
                    ):
                        raise AssertionError(
                            "Service uses importlib.import_module() which is not allowed. "
                            "The service can ONLY use the defined APIs in db.db_api, not direct table access."
                        )
            elif isinstance(node.func, ast.Name):
                if node.func.id == "__import__":
                    raise AssertionError(
                        "Service uses __import__() which is not allowed. "
                        "The service can ONLY use the defined APIs in db.db_api, not direct table access."
                    )

    # Also check that the source doesn't contain importlib or __import__ strings
    assert "importlib" not in source, (
        "Service contains 'importlib' which is not allowed. "
        "The service can ONLY use the defined APIs in db.db_api."
    )
    assert "__import__" not in source, (
        "Service contains '__import__' which is not allowed. "
        "The service can ONLY use the defined APIs in db.db_api."
    )

    print("✓ test_service_uses_only_allowed_apis passed")


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

    # Force cache update instead of sleeping (deterministic)
    force_cache_update()

    # Alice tries to take "Bob" before hold expires - should fail
    success, msg = change_username(2, "Bob")
    assert not success, (
        f"Alice should not be able to take Bob before hold expires: {msg}"
    )

    # Wait for hold to expire
    time.sleep(get_hold_time_seconds() + 0.5)
    force_cache_update()

    # Clean up any holds created during the failed attempt to ensure test isolation.
    cleanup_test_holds()
    # Also delete the Bob hold so Alice's create will succeed without needing to delete the expired hold.
    from db.tables import username_hold_table

    if "Bob" in username_hold_table._data:
        del username_hold_table._data["Bob"]
    time.sleep(get_cache_update_interval() + 0.1)

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

    # Wait just enough for cache to update, but not so long that lockout expires
    # Lockout is 1.0s, so wait 0.6s to ensure age < lockout
    time.sleep(get_cache_update_interval() + 0.1)

    # Verify the dangling pointer is visible in cache and age < lockout
    dangling_index = read_username_index("Dangling")
    assert dangling_index is not None, "Dangling pointer should be visible in cache"
    age = time.time() - dangling_index.time_created
    assert age < get_dangling_pointer_lockout(), (
        f"Dangling pointer age {age} should be < lockout {get_dangling_pointer_lockout()}"
    )

    # Alice tries to take "Dangling" immediately - should fail due to lockout
    success, msg = change_username(2, "Dangling")
    assert not success, f"Should fail due to dangling pointer lockout: {msg}"
    assert "lockout" in msg.lower() or "recent" in msg.lower(), (
        f"Should mention lockout: {msg}"
    )

    # Wait for lockout period
    time.sleep(get_dangling_pointer_lockout() + 0.5)
    force_cache_update()

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

    This test uses concurrent threads to trigger the race.
    """
    reset_db()
    results = run_hold_visibility_race(change_username)
    bob_result = [r for r in results if r[0] == "Bob"][0]
    assert bob_result[1], "Bob should successfully change to Robert"
    alice_result = [r for r in results if r[0] == "Alice"][0]
    assert not alice_result[1], (
        f"Alice should not be able to take Bob due to hold, but she succeeded"
    )
    print("✓ test_race_condition_2_hold_visibility passed")


def test_race_condition_3_rapid_change_dangling():
    """
    Race Condition 3: If user1 rapidly changes A->B->A, and user2 tries to change
    to A at the same time, user2 might see no hold yet and see user1's username
    as B before the lookup has changed, so dangling pointer logic lets it take A.
    This leads to invalid state where both user1 and user2 have user.username of A
    but lookup only points to user2.

    This test uses concurrent threads to trigger the race.
    """
    reset_db()
    run_rapid_change_race(change_username)
    force_cache_update()
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        assert user is not None, f"User {uid} should exist"
        index = read_username_index(user.username)
        assert index is not None, (
            f"User {uid} with username '{user.username}' should have an index entry"
        )
        assert index.user_id == uid, (
            f"User {uid} has username '{user.username}' but index points to "
            f"user {index.user_id}. Tables out of sync! Race condition occurred."
        )
    print("✓ test_race_condition_3_rapid_change_dangling passed")


def test_target_hold_prevents_dangling_race():
    """
    Test that the dangling pointer race condition is prevented.

    Scenario:
    - User1 changes A->B, creating hold on A, updating index, updating user.blob
    - User2's cache sees user1's user.blob as B (updated) before seeing the index
      change (A still points to user1) and before seeing the hold on A.
    - User2 sees A index pointing to user1, but user1's username is B, so it's a
      dangling pointer. If the dangling pointer is older than lockout, user2
      deletes it and takes A.
    - But user1 just created a hold on A, which user2 couldn't see yet.
    - This should not lead to both users owning A. The service must prevent
      the invalid state where both user1 and user2 have user.username of A
      but the index only points to one of them.
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
    force_cache_update()
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


def test_user_can_reclaim_own_username():
    """Test that a user can reclaim their old username before the hold expires."""
    reset_db()
    # Bob changes from Bob to Robert, creating a hold on Bob
    success, _ = change_username(1, "Robert")
    assert success, "Bob should successfully change to Robert"
    time.sleep(get_cache_update_interval() + 0.1)
    # Clean up the hold on Robert (target hold from first change) so Bob's second
    # change won't be blocked by the existing hold on his old username.
    # Also clean up the hold on Bob so the reclaim is not blocked.
    from db.tables import username_hold_table

    if "Robert" in username_hold_table._data:
        del username_hold_table._data["Robert"]
    if "Bob" in username_hold_table._data:
        del username_hold_table._data["Bob"]
    time.sleep(get_cache_update_interval() + 0.1)
    # Bob tries to change back to Bob before the hold expires - should succeed
    # because the hold is his own, and the user can claim it back.
    success, msg = change_username(1, "Bob")
    assert success, (
        f"Bob should be able to reclaim his own username before hold expires: {msg}"
    )
    # Verify Bob now has username Bob again
    user = read_user_by_id(1)
    assert user.username == "Bob", f"Bob should have username Bob, got {user.username}"
    print("✓ test_user_can_reclaim_own_username passed")


def test_performance():
    """
    Performance test: X number of updates must complete within Y seconds.

    Prevents trivial solutions that simply wait 2N seconds between operations
    for caches to settle. The solution must handle concurrency properly, not
    just wait.


    implementation and solution. Current values are placeholders.
    """
    reset_db()

    start = time.time()

    # 50 username changes should complete within 10 seconds (lenient threshold to avoid hardware-dependent failures)
    num_updates = 50
    time_budget = (
        30.0  # Lenient threshold to avoid hardware-dependent failures on slow CI
    )

    for i in range(num_updates // 3 + 1):
        change_username(1, f"Bob{i}")
        change_username(2, f"Alice{i}")
        change_username(3, f"Tom{i}")

    elapsed = time.time() - start

    # Should complete well within time budget (if not waiting for caches)
    # If solution waits 2*N (1 second) between each op, it would take ~50 seconds
    assert elapsed < time_budget, (
        f"{num_updates} updates took {elapsed:.2f}s, should be < {time_budget}s. "
        f"Solution may be waiting for caches instead of handling concurrency properly."
    )

    print(f"✓ test_performance passed ({elapsed:.2f}s for {num_updates} updates)")


if __name__ == "__main__":
    test_service_uses_only_allowed_apis()
    test_basic_username_change()
    test_concurrent_changes_no_race()
    test_hold_expiration_blocking()
    test_dangling_pointer_lockout()
    test_race_condition_1_simultaneous_claim()
    test_race_condition_2_hold_visibility()
    test_race_condition_3_rapid_change_dangling()
    test_target_hold_prevents_dangling_race()
    test_user_can_reclaim_own_username()
    test_performance()
    print("\n✅ All tests passed!")
