# Username Swap Race Condition

## Description
Fix race conditions in a distributed username change service that spans three DB tables (UsernameIndex, UsernameHold, UserBlob) with per-client read caches and global per-pkey locking.

The buggy service implements a 7-step username change that is vulnerable to three main race conditions:
1. **Simultaneous claim**: When 2+ users try to claim the same available username concurrently, only one should succeed. The buggy version allows both to proceed because the index creation is not atomic with the other updates.
2. **Hold visibility**: A user might see a username as available before the hold is written to their cache (due to read cache staleness), allowing them to claim a username that should be blocked by a hold. The buggy version only checks the hold in the cache, which may be stale.
3. **Rapid change with dangling pointer**: A user rapidly changes A->B->A while another tries to claim A concurrently. The second user might see the first user's user.blob as B (updated) before seeing the index change (A still points to user 1) and before seeing the hold on A. The second user sees A as a dangling pointer (index points to user 1 but user 1's username is B) and, if the dangling pointer is older than the lockout, deletes it and takes A. But the first user just created a hold on A, which the second user couldn't see yet. Both users end up owning A, but the index only points to one of them.

The service must use the atomic changeset API to make multi-table updates atomic and create a hold for the target username to prevent the dangling pointer race. The target hold is a temporary "intent to claim" lock that ensures only one user can claim a username at a time, even if the cache is stale. If the hold already exists (even if not visible in the cache), the create will fail and the changeset will revert.

## Completion Rates
- Oracle: 3/3 passed (100%)
- Sonnet 4.6: 4/5 passed (80%)
- Opus 4.6: 4/5 passed (80%)
- Avocado: 4/5 passed (80%)

## Model Analysis

**Sonnet 4.6: 4/5 passed**
- 4 trials passed by correctly using the atomic changeset API to wrap steps 4-7 and creating a target hold for the target username to prevent the dangling pointer race.
- 1 trial failed because the agent did not properly clean up the old username hold on failure. When a change fails after creating the old hold, the hold remains and blocks future attempts by the same user. The agent's solution did not delete the old hold before returning False in step 3, leaving a leftover hold on the user's old username.
- The failure reflects a reasoning gap about resource cleanup on failure paths, not a task setup issue.

**Opus 4.6: 4/5 passed**
- 4 trials passed by correctly using the atomic changeset API and creating a target hold to prevent the dangling pointer race.
- 1 trial failed because the agent did not use the atomic changeset at all, leaving steps 4-7 non-atomic. Concurrent operations interleaved, causing the tables to go out of sync.
- The failure reflects a reasoning gap about the need for atomicity across multiple tables, not a task setup issue.

**Avocado: 4/5 passed**
- 4 trials passed by correctly implementing the target hold fix and using the atomic changeset properly.
- 1 trial failed because the agent used `time.time() + HOLD_TIME_SECONDS` in the atomic changeset operations for deleting the existing hold, instead of using the actual `time_expired` value from the hold. The delete operation requires the exact expire time to match, and the calculated time did not match the hold's actual expire time, so the delete did not remove the hold. The subsequent create then failed because the hold still existed, causing the changeset to revert.
- The failure reflects a reasoning gap about the need for precise hold expire time matching when deleting holds, not a task setup issue.

**Failure categorization across all models:**
- **Missing atomic changeset**: 1 failure (Opus) - Agent did not wrap steps 4-7 in an atomic changeset, leaving the multi-table updates vulnerable to interleaving.
- **Incorrect hold expire time**: 1 failure (Avocado) - Agent used calculated time instead of actual hold time_expired when deleting, causing the delete to not match.
- **Missing hold cleanup**: 1 failure (Sonnet) - Agent did not clean up the old username hold on failure, leaving a leftover hold that blocked future attempts.

**Why these failures reflect reasoning gaps:**
- The task clearly describes the three tables, the read cache eventual consistency, and the available DB APIs including the atomic changeset. The agent must understand that the 7 steps are not atomic and that concurrent operations can interleave.
- The dangling pointer race is subtle - it requires understanding that a user might see the user.blob update before the index/hold updates, thinking a username is a dangling pointer when it's actually just a recent change with a hold that is not yet visible. The target hold fix is not obvious and requires deep reasoning about the race scenario.
- The hold expire time matching is a detail of the DB API that the agent must understand from the API documentation. Using the actual time_expired from the hold instead of a calculated time is necessary for the delete to succeed.
- The hold cleanup on failure is a resource management issue that the agent must handle. If a change fails after creating the old hold, the hold should be deleted before returning, otherwise it will block future attempts.

These are all reasoning gaps about distributed systems concurrency, not task setup issues. The task is well-designed and the tests reliably trigger the race conditions.

## Anti-Cheating Analysis
- **Hardcoded outputs**: The solution must handle arbitrary username changes for the three premade users (Bob, Alice, Tom) to any target username, not hardcoded values. Tests use dynamic usernames like "Bob0", "Alice1", "Charlie", "Dangling1", etc., and the specific usernames are not known in advance. A hardcoded solution would fail.
- **Overfitting to visible tests**: The race conditions are triggered by concurrent threads with realistic timing and cache staleness, which cannot be predicted by hardcoding. The `run_concurrent_1`, `run_concurrent_2`, and `run_concurrent_3` helpers use `threading.Barrier` to ensure threads start the critical operations together, and the `run_hold_visibility_race` helper disables auto cache updates to deterministically simulate stale caches. The solution must correctly use atomic changesets and target holds to handle any concurrent interleaving, not just the specific test scenarios.
- **Modifying test files**: Tests are in `/tests` which is hidden from the agent in production (Harbor-mounted at verify time and not present in the agent container). The agent cannot see the test files, test names, or test output. The agent only receives a final reward of 0 or 1. The agent cannot modify test files.
- **Bypassing the intended solution path**: The DB implementation is hidden in `/usr/local/lib/python3.12/site-packages/db/` (not visible to agent in `/app`). The agent can only see and modify files in `/app` (username_service.py and config.py). The DB enforces per-pkey locking and the atomic changeset API ensures all-or-nothing semantics. The new test `test_service_uses_only_allowed_apis` verifies that the service only imports from allowed modules (db.db_api, config, typing, time) and does not directly access db.tables, db.lock_manager, or use importlib/__import__ to bypass the import restriction. The new blocking rule in the atomic changeset prevents using delete/create of the same data in the same changeset to bypass the "create should fail if pkey exists" rule. The agent must use the public API correctly and implement the proper fix with target holds and atomic changesets.
