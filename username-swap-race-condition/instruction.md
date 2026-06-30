# Username Change Race Condition Task

## Overview
Fix the race conditions in the `change_username` function in `/app/username_service.py`.

## Database Tables

The system has three tables:

1. **UsernameIndex**: Maps username string to user ID and time created. This decides who owns a username.
   - Only create and delete operations allowed.
   - Create fails if username already exists.
   - Delete by username and user_id succeeds even if no matching entry (idempotent).

2. **UsernameHold**: Maps username to user ID, time created, and time expired. A username hold prevents others users from claiming the username until the hold expires.
   - Create by username, user_id, hold_expire_time (absolute timestamp when the hold expires). Fails if username already has a hold.
   - Delete by username, user_id, hold_expire_time (absolute timestamp).

3. **UserBlob**: Maps user ID to User object with username field. This is a stand-in for a user's actual data.
   - Update username field allowed.

## Available DB API

Import from `db.db_api`:

**Read operations** (from per-client per-table read cache, may be stale):
- `read_user_by_id(user_id: int) -> Optional[User]`
- `read_username_index(username: str) -> Optional[UsernameIndexEntry]`
- `read_username_hold(username: str) -> Optional[UsernameHoldEntry]`

**Write operations** (to global instance with per-pkey locking):
- `create_username_index(username: str, user_id: int) -> bool`
- `delete_username_index(username: str, user_id: int) -> bool`
- `create_username_hold(username: str, user_id: int, hold_expire_time: int) -> bool`
- `delete_username_hold(username: str, user_id: int, hold_expire_time: int) -> bool`
- `update_user_username(user_id: int, new_username: str) -> bool`

**Atomic changeset:**
- `atomic_changeset(operations: List[Operation]) -> bool`
  - Acquires locks for all pkeys, backs up state, reverts on failure.
  - Operations: create_username_index, delete_username_index, create_username_hold, delete_username_hold, update_user_username.

**Utility:**
- `get_cache_update_interval() -> float`
- `get_dangling_pointer_lockout() -> float` (2x cache interval)
- `get_hold_time_seconds() -> int`

## Premade Users
- Bob (id=1, username="Bob")
- Alice (id=2, username="Alice")
- Tom (id=3, username="Tom")

## The Username Update Service

The `change_username(user_id, target_username)` function in `/app/username_service.py` implements a sequence of operations to change a user's username.
The core rules it has to follow are:
- A user "Owns" a username if the Username index points that username to the user and their user blob's user.username matches.
- A dangling pointers can occur when the UsernameIndex points a username to a user_id, but that user's UserBlob has a different username (due to a previous failed update). Dangling pointers can only be claimed if they are older than the dangling pointer lockout period (2x cache update interval). If a dangling pointer is too recent (age < lockout period), the service should fail with a message indicating the lockout period has not expired (e.g., containing the words "lockout" or "recent").
- After a user changes their username, no other user can claim that username for the default hold time. The user can claim it back before the hold expires.
- At any given time (not including while write locks are in use), a username can only be owned by one user. This is enforced at the dB level for the username index, but has to be maintained in the service layer for the user blob username field. eventual consistentcy is fine, as in we expect the read cache to occasionally make it look like two users have the same username on their user blob, as long as they eventually have different usernames once all caches are refreshed.
- The service needs to be performant. It should be able to handle many concurrent username changes quickly.


## Your Task

The username update service is buggy and has race conditions that can lead to violations of the given rules.
Modify `/app/username_service.py` to fix the race conditions.

**Constraints:**
- DB implementation in `/db` is hidden and cannot be modified.
- Only modify files in `/app` (the service layer).
- The username service can ONLY access the DB via the defined APIs in `db.db_api`. Allowed imports in `username_service.py`: `time`, `typing`, `db.db_api`, `config`. No new imports are allowed beyond what is already imported in the buggy version. Direct access to the database tables (db.tables), lock manager (db.lock_manager), or other internal DB modules is not allowed.
- The solution must handle concurrent username changes correctly.
- The solution must be performant: A solution that simply waits out the cache timings will not be accepted.

## Files to Modify
- `/app/username_service.py` - Fix the `change_username` function

## Verification
Tests will verify:
- Basic username changes work correctly
- All three tables remain consistent
- Concurrent changes don't cause duplicates or inconsistencies
- Holds properly block claims before expiration
- Dangling pointers only claimed after lockout period
- Performance: multiple updates complete within time budget
- Race conditions are prevented
