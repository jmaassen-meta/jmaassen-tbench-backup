# Username Swap Race Condition

## Description
Fix race conditions in a distributed username change service that spans three DB tables (UsernameIndex, UsernameHold, UserBlob) with per-client read caches and global per-pkey locking.

The buggy service implements a multi-step username change that is vulnerable to three main race conditions:
1. **Simultaneous claim**: When 2+ users try to claim the same available username concurrently, only one should succeed. The buggy version allows both to proceed because the index creation is not atomic with the other updates.
2. **Hold visibility**: A user might see a username as available before the hold is written to their cache (due to read cache staleness), allowing them to claim a username that should be blocked by a hold. The buggy version only checks the hold in the cache, which may be stale.
3. **Rapid change with dangling pointer**: A user rapidly changes A->B->A while another tries to claim A concurrently. The second user might see the first user's user.blob as B (updated) before seeing the index change (A still points to user 1) and before seeing the hold on A. The second user sees A as a dangling pointer (index points to user 1 but user 1's username is B) and, if the dangling pointer is older than the lockout, deletes it and takes A. But the first user just created a hold on A, which the second user couldn't see yet. Both users end up owning A, but the index only points to one of them.

The service must use the atomic changeset API to make multi-table updates atomic and create a hold for the target username to prevent the dangling pointer race. The target hold is a temporary "intent to claim" lock that ensures only one user can claim a username at a time, even if the cache is stale. If the hold already exists (even if not visible in the cache), the create will fail and the changeset will revert.

The task includes 13 tests:
- Basic functionality and table consistency
- Concurrent changes without races
- Hold expiration blocking
- Dangling pointer lockout
- Three specific race condition scenarios (simultaneous claim, hold visibility, dangling pointer)
- Rapid chained changes (Bob changes Bob->Robert->Bobby->Bob while Alice tries to claim intermediate names)
- User reclaim of own username
- Concurrent reclaim scenarios
- Performance: 100 username changes must complete within 5 seconds (a correct solution completes in <1s; naive waiting solutions take 50+ seconds and fail)
- API restriction: Service can only use allowed DB APIs, no direct table access

## Completion Rates
- Oracle: 3/3 passed (100%)
- Opus 4.6: 4/5 passed (80%)
- Avocado: 4/5 passed (80%)
- GPT-5.5 (Codex): 0/5 passed (0%)

*Note: Results from previous version. The task has since been made harder with stricter performance requirements, generic error messages, an additional rapid chained changes test, and DB implementation hiding. The core solution remains the same.*

## Model Analysis

**Opus 4.6: 4/5 passed**
- 4 trials passed by correctly using the atomic changeset API and creating a target hold to prevent the dangling pointer race.
- 1 trial failed because the agent did not use the atomic changeset at all, leaving steps 4-7 non-atomic. Concurrent operations interleaved, causing the tables to go out of sync.
- The failure reflects a reasoning gap about the need for atomicity across multiple tables, not a task setup issue.

**Avocado: 4/5 passed**
- 4 trials passed by correctly implementing the target hold fix and using the atomic changeset properly.
- 1 trial failed because the agent used `time.time() + HOLD_TIME_SECONDS` in the atomic changeset operations for deleting the existing hold, instead of using the actual `time_expired` value from the hold. The delete operation requires the exact expire time to match, and the calculated time did not match the hold's actual expire time, so the delete did not remove the hold. The subsequent create then failed because the hold still existed, causing the changeset to revert.
- The failure reflects a reasoning gap about the need for precise hold expire time matching when deleting holds, not a task setup issue.

**GPT-5.5 (Codex): 0/5 passed**
- All 5 trials failed to correctly handle the race conditions.
- The model did not properly understand the distributed cache consistency model and the need for the target hold fix. The failures were due to not using the atomic changeset correctly or not implementing the target hold to prevent the dangling pointer race.
- The failures reflect reasoning gaps about distributed systems concurrency, not task setup issues.

**Failure categorization across all models:**
- **Missing atomic changeset**: 1 failure (Opus) - Agent did not wrap steps 4-7 in an atomic changeset, leaving the multi-table updates vulnerable to interleaving.
- **Incorrect hold expire time**: 1 failure (Avocado) - Agent used calculated time instead of actual hold time_expired when deleting, causing the delete to not match.

**Why these failures reflect reasoning gaps:**
- The task clearly describes the three tables, the read cache eventual consistency, and the available DB APIs including the atomic changeset. The agent must understand that the 7 steps are not atomic and that concurrent operations can interleave.
- The dangling pointer race is subtle - it requires understanding that a user might see the user.blob update before the index/hold updates, thinking a username is a dangling pointer when it's actually just a recent change with a hold that is not yet visible. The target hold fix is not obvious and requires deep reasoning about the race scenario.
- The hold expire time matching is a detail of the DB API that the agent must understand from the API documentation. Using the actual time_expired from the hold instead of a calculated time is necessary for the delete to succeed.
- The hold cleanup on failure is a resource management issue that the agent must handle. If a change fails after creating the old hold, the hold should be deleted before returning, otherwise it will block future attempts.

These are all reasoning gaps about distributed systems concurrency, not task setup issues. The task is well-designed and the tests reliably trigger the race conditions.

## Anti-Cheating Analysis
- **Hardcoded outputs**: The solution must handle arbitrary username changes for the three premade users (Bob, Alice, Tom) to any target username, not hardcoded values. Tests use dynamic usernames like "Bob0", "Alice1", "Charlie", "Dangling1", etc., and the specific usernames are not known in advance. A hardcoded solution would fail.
- **Overfitting to visible tests**: The race conditions are triggered by concurrent threads with realistic timing and cache staleness, which cannot be predicted by hardcoding. The `run_concurrent_1`, `run_concurrent_2`, and `run_concurrent_3` helpers use `threading.Barrier` to ensure threads start the critical operations together, and the helpers disable auto cache updates to deterministically simulate stale caches. The solution must correctly use atomic changesets and target holds to handle any concurrent interleaving, not just the specific test scenarios.
- **Modifying test files**: Tests are in `/tests` which is hidden from the agent in production (Harbor-mounted at verify time and not present in the agent container). The agent cannot see the test files, test names, or test output. The agent only receives a final reward of 0 or 1. The agent cannot modify test files.
- **Reading test helpers**: The test helper file (`db_test_helpers.py`) is now in the `tests/` directory, which is only mounted at `/tests` at test time and not available during agent execution. The helpers contain detailed descriptions of the race conditions and solution hints. Agents cannot read these files. In previous versions, the helpers were copied to the Docker image in site-packages, allowing agents to read the detailed race descriptions. This has been fixed.
- **Reading DB implementation**: The DB implementation (tables, lock manager, changeset logic, read cache) is now in `tests/db/` and not copied to the Docker image. Only the `db_api.py` interface stub (function signatures and docstrings, no implementation) is available in the Docker image at `/usr/local/lib/python3.12/site-packages/db/db_api.py`. Agents can read the public API but cannot see the implementation details of how tables store data, how locking works, how the changeset backs up and reverts state, or how the read cache handles staleness. This prevents agents from understanding the system by reading the implementation rather than reasoning from the API documentation and buggy code.
- **Bypassing the intended solution path**: The DB implementation is hidden (only the db_api interface stub is visible to the agent in `/usr/local/lib/python3.12/site-packages/db/`). The agent can only see and modify files in `/app` (username_service.py and config.py). The DB enforces per-pkey locking and the atomic changeset API ensures all-or-nothing semantics. The test `test_service_uses_only_allowed_apis` verifies that the service only imports from allowed modules (db.db_api, config, typing, time) and does not directly access db.tables, db.lock_manager, or use importlib/__import__ to bypass the import restriction. The blocking rule in the atomic changeset prevents using create/delete of the same username in the same changeset as a master read stand-in to check if a key exists (create then delete is blocked; only delete then create is allowed for legitimate dangling pointer cleanup). The agent must use the public API correctly and implement the proper fix with target holds and atomic changesets.
- **Waiting out the races**: The performance test requires 100 username changes to complete within 5 seconds. A correct solution using atomic changesets completes in under 1 second. A naive solution that waits for cache update intervals (0.5s) between operations would take 50+ seconds and fail. A solution that waits for holds to expire (3s) would take 300+ seconds and fail. The strict time budget ensures agents implement a truly correct concurrent solution rather than simply waiting for caches to settle.
