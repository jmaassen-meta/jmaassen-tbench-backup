# Username Swap Race Condition

## Description
Fix race conditions in a distributed username change service that spans three DB tables (UsernameIndex, UsernameHold, UserBlob) with per-client read caches and global per-pkey locking.

The buggy service implements a 7-step username change that is vulnerable to three main race conditions:
1. **Simultaneous claim**: When 2+ users try to claim the same available username concurrently, only one should succeed.
2. **Hold visibility**: A user might see a username as available before the hold is written to their cache, allowing them to claim a username that should be blocked by a hold.
3. **Rapid change with dangling pointer**: A user rapidly changes A->B->A while another tries to claim A concurrently. The second user might see a dangling pointer (index points to user but user.blob has different username) and claim A, leading to both users owning A.

The service must use the atomic changeset API to make multi-table updates atomic and create a hold for the target username to prevent the dangling pointer race.

## Completion Rates
- Oracle: 3/3 passed (100%)
- Sonnet 4.6: TBD
- Opus 4.6: TBD
- Avocado: TBD

## Model Analysis
TBD - Awaiting model runs.

## Anti-Cheating Analysis
- **Hardcoded outputs**: The solution must handle arbitrary username changes, not hardcoded values. Tests use dynamic usernames like "Bob0", "Alice1", etc.
- **Overfitting to visible tests**: The race conditions are triggered by concurrent threads and cache timing, which cannot be predicted by hardcoding. The solution must correctly use atomic changesets and target holds.
- **Modifying test files**: Tests are in /tests which is hidden from the agent in production (Harbor-mounted at verify time). The agent cannot modify test files.
- **Bypassing the intended solution path**: The DB implementation is hidden in site-packages (not visible to agent in /app). The agent can only modify the service layer in /app. The DB enforces per-pkey locking and the atomic changeset API ensures all-or-nothing semantics.
