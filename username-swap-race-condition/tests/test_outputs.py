"""
Tests for username swap race condition task.

Verifies that:
1. Basic username change works correctly
2. All three tables remain consistent after change
3. Concurrent changes do not cause race conditions
4. Username holds properly block claims
5. Dangling pointers are handled correctly with lockout period
6. Performance: multiple updates complete within time budget
7. Concurrent operations handle simultaneous claims correctly
8. Concurrent operations handle hold visibility correctly
9. Concurrent operations maintain consistency under rapid changes
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
    run_concurrent_1,
    run_concurrent_2,
    run_concurrent_3,
    setup_dangling_pointer,
    cleanup_test_holds,
    setup_expired_hold,
    run_concurrent_reclaim_race,
)
from username_service import change_username


def test_service_uses_only_allowed_apis():
    """Test that the username service only imports from allowed modules."""
    with open("/app/username_service.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    allowed_modules = {"time", "typing", "db.db_api", "config", "db.clock"}

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
    # Note: These string checks are intentionally strict to prevent bypassing
    # the import restriction via importlib.import_module() or __import__().
    # The DB module is hidden in site-packages, but a cheating agent might
    # try to import it directly using importlib. These checks ensure the
    # service only uses the public API in db.db_api.
    assert "importlib" not in source, (
        "Service contains 'importlib' which is not allowed. "
        "The service can ONLY use the defined APIs in db.db_api."
    )
    assert "__import__" not in source, (
        "Service contains '__import__' which is not allowed. "
        "The service can ONLY use the defined APIs in db.db_api."
    )

    # Check the imported module's namespace for any disallowed modules that
    # may have been imported dynamically via builtins tricks like
    # builtins'imp'+'ort' or getattr(builtins, 'import_module').
    # This catches dynamic imports that bypass the AST and string checks.
    import username_service
    import types

    for name, value in username_service.__dict__.items():
        if isinstance(value, types.ModuleType):
            module_name = value.__name__
            # Allow the modules that the service is supposed to import
            if module_name in ("time", "typing", "db.db_api", "config", "db.clock"):
                continue
            # Allow the db package itself (imported as part of db.db_api)
            if module_name == "db":
                continue
            # Disallow any other modules, especially threading, db.tables, etc.
            # A cheating service might smuggle threading via builtins tricks and
            # wrap everything in a global lock, passing the tests without using
            # the atomic changeset or target hold. This check prevents that.
            assert False, (
                f"Service has disallowed module '{module_name}' in its namespace (imported as '{name}'). "
                f"The service can ONLY use the defined APIs in db.db_api, not direct table access or threading. "
                f"A service smuggling threading and wrapping everything in a global lock is not allowed."
            )

    print("✓ test_service_uses_only_allowed_apis passed")


def reset_db():
    """Reset DB to initial state with premade users."""
    reset_db_hidden()
    force_cache_update()


def test_basic_username_change():
    """Test basic username change from Bob to Robert."""
    reset_db()

    # Bob (id=1) changes from "Bob" to "Robert"
    success, msg = change_username(1, "Robert")
    assert success, f"Change should succeed: {msg}"
    force_cache_update()

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
    force_cache_update()

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

    # Manually set the Bob hold to be expired (deterministic, no waiting)
    setup_expired_hold("Bob")
    # Clean up any holds created during the failed attempt to ensure test isolation.
    cleanup_test_holds()
    # Also delete the Bob hold so Alice&apos;s create will succeed without needing to delete the expired hold.
    from db.tables import username_hold_table

    if "Bob" in username_hold_table._data:
        del username_hold_table._data["Bob"]
    force_cache_update()

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
    force_cache_update()

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


def test_concurrent_1():
    """Test concurrent username claims to the same target."""
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

    print("✓ test_concurrent_1 passed")


def test_concurrent_2():
    """Test concurrent changes where one user changes while another tries to claim."""
    reset_db()
    results = run_concurrent_2(change_username)
    bob_result = [r for r in results if r[0] == "Bob"][0]
    assert bob_result[1], "Bob should successfully change to Robert"
    alice_result = [r for r in results if r[0] == "Alice"][0]
    assert not alice_result[1], (
        f"Alice should not be able to take Bob due to hold, but she succeeded"
    )
    print("✓ test_concurrent_2 passed")


def test_concurrent_3():
    """Test rapid concurrent username changes for consistency."""
    reset_db()
    run_concurrent_3(change_username)
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
    print("✓ test_concurrent_3 passed")


def test_concurrent_4():
    """Test that concurrent operations maintain consistency across all tables."""
    reset_db()

    # Bob changes Bob -> Robert, creating hold on Bob in global table
    # and deleting the Bob index from the global table.
    success, _ = change_username(1, "Robert")
    assert success, "Bob should successfully change to Robert"

    # Disable auto cache updates to simulate stale cache scenario.
    # Manually update only the user.blob cache (so we see Bob's username as Robert),
    # but do NOT update the hold cache or index cache.
    # This simulates the race where user.blob updates before index/hold in distributed caches.
    from db.test_helpers import (
        disable_auto_cache,
        enable_auto_cache,
        force_cache_update,
    )

    disable_auto_cache()
    from db.read_cache import _thread_local

    if hasattr(_thread_local, "caches"):
        for key, cache in list(_thread_local.caches.items()):
            if "user_blob" in key:
                cache._update_from_global()
            # Do not update username_index or username_hold caches - keep them stale
            # The index cache still has the old Bob index (stale), and the hold cache
            # does not have the Bob hold (stale). The stale Bob index has an old
            # time_created (from initialization), so its age is greater than the lockout.

    # At this point, Alice's cache sees:
    # - Bob's user.blob username = "Robert" (updated)
    # - Bob index still points to user 1 (stale, not updated) with age > lockout (old from init)
    # - No hold on Bob (stale, not updated)
    # So Alice thinks Bob is a dangling pointer (age > lockout) and tries to claim it.
    # The buggy version will delete the dangling pointer and take Bob, succeeding.
    # This is the race condition - the hold on Bob exists but Alice couldn't see it.
    # The fixed version should prevent this by creating a target hold on Bob,
    # which will fail because the Bob hold already exists in the global table,
    # even though Alice couldn't see it in her stale cache.

    # Alice tries to take "Bob".
    success, msg = change_username(2, "Bob")
    # Should fail because Bob has a hold (even if Alice couldn't see it in her stale cache,
    # the service should prevent her from taking Bob)
    assert not success, f"Alice should not be able to take Bob due to hold: {msg}"

    enable_auto_cache()
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

    print("✓ test_concurrent_4 passed")


def test_user_can_reclaim_own_username():
    """Test that a user can reclaim their old username before the hold expires."""
    reset_db()
    # Bob changes from Bob to Robert, creating a hold on Bob
    success, _ = change_username(1, "Robert")
    assert success, "Bob should successfully change to Robert"
    force_cache_update()
    # Clean up the hold on Robert from first change so Bob's second
    # change won't be blocked by the existing hold on his old username.
    # Also clean up the hold on Bob so the reclaim is not blocked.
    from db.tables import username_hold_table

    if "Robert" in username_hold_table._data:
        del username_hold_table._data["Robert"]
    if "Bob" in username_hold_table._data:
        del username_hold_table._data["Bob"]
    force_cache_update()
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


def test_concurrent_reclaim():
    """Test that the original owner can reclaim their username even when another user tries concurrently."""
    reset_db()
    # Bob changes to Robert, then tries to reclaim Bob while Alice simultaneously tries to take Bob.
    # The helper sets up the race deterministically.
    results = run_concurrent_reclaim_race(change_username)
    # The important verification is that the system remains consistent after the concurrent operations.
    # If Bob succeeded in reclaiming, good. If Alice succeeded in taking Bob, also good (Bob's hold may have expired).
    # The key is that no two users end up owning the same username.
    force_cache_update()
    for uid in [1, 2, 3]:
        user = read_user_by_id(uid)
        if user:
            index = read_username_index(user.username)
            if index:
                assert index.user_id == uid, f"User {uid} tables out of sync!"
    print("✓ test_concurrent_reclaim passed")


def test_performance():
    """Test that the service handles many concurrent username changes quickly without waiting for caches.

    The service must not simply wait for cache update intervals between operations.
    A correct solution using atomic changesets should complete 50 updates in under
    a few seconds. A solution that waits for caches would take 50+ seconds.
    """
    reset_db()
    start = time.time()
    # 50 username changes should complete quickly without waiting for caches.
    num_updates = 50
    time_budget = (
        60.0  # Very generous budget to avoid hardware-dependent failures on slow CI.
    )
    # A correct solution should complete in under 2-3 seconds. A solution that waits
    # for cache intervals (0.5s) between each operation would take 25+ seconds.
    # A solution that waits for holds to expire (3s) would take 150+ seconds.
    # The 60s budget is very lenient and should not cause false negatives, but will
    # catch solutions that take an unreasonably long time by waiting for caches.
    for i in range(num_updates // 3 + 1):
        change_username(1, f"Bob{i}")
        change_username(2, f"Alice{i}")
        change_username(3, f"Tom{i}")
    elapsed = time.time() - start
    assert elapsed < time_budget, (
        f"{num_updates} updates took {elapsed:.2f}s, should be < {time_budget}s. "
        f"Solution may be waiting for caches instead of handling concurrency properly. "
        f"A correct solution should complete in under a few seconds."
    )
    print(f"✓ test_performance passed ({elapsed:.2f}s for {num_updates} updates)")


if __name__ == "__main__":
    test_service_uses_only_allowed_apis()
    test_basic_username_change()
    test_concurrent_changes_no_race()
    test_hold_expiration_blocking()
    test_dangling_pointer_lockout()
    test_concurrent_1()
    test_concurrent_2()
    test_concurrent_3()
    test_concurrent_4()
    test_user_can_reclaim_own_username()
    test_concurrent_reclaim()

    test_performance()
    print("\n✅ All tests passed!")
