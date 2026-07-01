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

**Previous version results (before hardening):**
- Oracle: 3/3 passed (100%)
- Opus 4.6: 4/5 passed (80%)
- Avocado: 4/5 passed (80%)
- GPT-5.5 (Codex): 0/5 passed (0%)

**Current version:** The task has been significantly hardened since those runs (see Key Differences below). No new trial runs have been conducted on the hardened version yet. The core solution remains the same (atomic changeset + target hold), but agents now have much less information to work with.

### Key Differences from Previous Version

The current version is significantly harder than the version used for the above results:

1. **DB implementation completely hidden** (was visible):
   - **Before**: Agent could read `/usr/local/lib/python3.12/site-packages/db/tables.py`, `changeset.py`, `read_cache.py`, `lock_manager.py` to understand table structures, locking, backup/revert logic, and cache staleness handling.
   - **After**: Only the `db_api.py` interface stub (function signatures and docstrings, no implementation) is visible. The real implementation is in `tests/db/` which is only mounted at test time, not during agent execution. Agents must reason about the system from the API documentation and buggy code alone.

2. **Test helpers with solution hints no longer visible** (was visible):
   - **Before**: `db_test_helpers.py` was copied to site-packages, allowing agents to read detailed descriptions like: "Fixed version creates a target hold on Bob as part of the atomic changeset, which fails because the Bob hold already exists in the global table."
   - **After**: Test helpers are in `tests/db_test_helpers.py`, which is only mounted at `/tests` at test time, not available during agent execution. Agents cannot read the race condition descriptions or solution hints.

3. **Performance test 12x stricter**:
   - **Before**: 50 updates in 60 seconds (very lenient; naive waiting solutions could pass)
   - **After**: 100 updates in 5 seconds (a correct solution completes in <1s; naive solutions that wait for cache intervals take 50+ seconds and fail, solutions that wait for holds take 300+ seconds and fail)

4. **Generic error messages** (was descriptive):
   - **Before**: "Dangling pointer too recent, lockout period not expired", "Target username has active hold by user 1", "Failed to create username index (race condition: someone else claimed it)"
   - **After**: All errors are generic "Username not available". Agents cannot infer the solution from error message hints.

5. **Additional rapid chained changes test**:
   - **New test**: `test_rapid_chained_changes` - Bob changes Bob->Robert->Bobby->Bob rapidly while Alice tries to claim intermediate names. Tests proper hold chaining, reclaim of own username, and system consistency. The buggy version fails this test because Bob cannot reclaim Robert (his own hold blocks him).

6. **Instruction significantly less explicit**:
   - **Before**: Structured "core rules" list with precise language, explicit "dangling pointer" term and detailed explanation, "eventual consistency is fine" reassurance, explicit hold reclaim rule ("The user can claim it back before the hold expires"), detailed table operation semantics ("Create fails if exists", "Delete is idempotent"), verification bullet points hinting at test scenarios, explicit performance warning.
   - **After**: Narrative paragraph instead of bulleted rules, no "dangling pointer" term (just describes the scenario without naming it), no eventual consistency reassurance, hold rule less explicit (doesn't state user can reclaim own username), simplified table descriptions (just what they store, not operation semantics), no verification bullets, shorter atomic changeset description, no performance warning. Agents must extract rules from the narrative, infer concepts from the code, and reason about consistency without reassurance.

### Predicted Impact on Pass Rates

The hardening changes significantly increase difficulty:

- **Oracle**: Expected to remain 3/3 (100%) - the oracle solution is correct and handles all cases.
- **Opus 4.6**: Expected to drop from 4/5 (80%) - Previously, 1 failure from not using atomic changeset. With hidden implementation, no test helper hints, less explicit instruction, and stricter performance test, more agents may fail to figure out the atomic changeset + target hold solution. The new rapid chained test may also catch additional issues with hold cleanup.
- **Avocado**: Expected to drop from 4/5 (80%) - Similar reasons. The previous failure was from incorrect hold expire time matching; with less explicit API documentation (only stub visible), more agents may make this mistake. The generic error messages provide less feedback for debugging.
- **GPT-5.5**: Expected to remain 0/5 or very low - The task was already too hard for this model, and the hardening makes it even more challenging.

The core solution remains unchanged (atomic changeset + target hold for the target username), but agents now have much less information to work with and must reason more deeply about the distributed systems concepts from the API interface and buggy code alone.

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
