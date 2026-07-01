# Username Change Service

Fix the race conditions in the `change_username` function in `/app/username_service.py`.

## Database

The system has three tables:

1. **UsernameIndex**: Maps username to user ID and creation time.
2. **UsernameHold**: Maps username to user ID, creation time, and expiration time.
3. **UserBlob**: Maps user ID to User object with username field.

## DB API

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
  Executes multiple operations atomically with backup/revert on failure.

**Utility:**
- `get_cache_update_interval() -> float`
- `get_dangling_pointer_lockout() -> float`
- `get_hold_time_seconds() -> int`

## Premade Users
- Bob (id=1, username="Bob")
- Alice (id=2, username="Alice")
- Tom (id=3, username="Tom")

## Background

The `change_username` function changes a user's username. When a user changes their username, a hold is created on the old username for that user. Holds prevent other users from claiming the username until they expire.

In some cases, the UsernameIndex may point to a user whose UserBlob has a different username. These entries can only be claimed after a certain age.

The service must handle concurrent username changes correctly and performantly.

## Your Task

The username update service has race conditions that can lead to incorrect behavior under concurrency.

Modify `/app/username_service.py` to fix the race conditions.

**Constraints:**
- DB implementation in `/db` is hidden and cannot be modified.
- Only modify files in `/app`.
- The service can ONLY use the defined APIs in `db.db_api`. Allowed imports: `time`, `typing`, `db.db_api`, `config`. No new imports.
- The solution must handle concurrent operations correctly and efficiently.

## Files to Modify
- `/app/username_service.py`

## Verification
Tests will verify correctness, consistency under concurrency, and performance.
