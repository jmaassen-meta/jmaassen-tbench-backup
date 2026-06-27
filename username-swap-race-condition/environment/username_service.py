"""
Username service with change_username function.

This implementation is buggy and has race conditions that can lead to
violations of the username ownership rules when multiple users change
usernames concurrently.

The agent must fix the race conditions to ensure correct behavior
under concurrent access.
"""

import time
from typing import Tuple

from db.db_api import (
    read_user_by_id,
    read_username_index,
    read_username_hold,
    create_username_index,
    delete_username_index,
    create_username_hold,
    delete_username_hold,
    update_user_username,
    atomic_changeset,
    get_cache_update_interval,
    get_dangling_pointer_lockout,
    get_hold_time_seconds,
)
from config import HOLD_TIME_SECONDS, DANGLING_POINTER_LOCKOUT


def change_username_buggy(user_id: int, target_username: str) -> Tuple[bool, str]:
    """
    Change a user's username to the target username.

    BUGGY VERSION - Has race conditions!

    Steps:
    1. Read current values of target username index and current user blob's username.
       If target is not different from the current user.username, early return success.
    2. Create a new username hold for the current/old username with user id and default hold time.
       If any holds exist for old username, do atomic changeset to delete then create new one.
    3. Check if allowed to proceed:
       - If any holds exist for target username, verify they are expired. If expired, they will be deleted in next step.
       - Check if username index for target points to an id. If not, OK. If yes, check if dangling.
         If dangling, verify the dangling pointer is older than DANGLING_POINTER_LOCKOUT before allowing claim.
    4. Delete any expired holds or dangling pointers (can be done concurrently)
    5. Write new username index for target_username -> user_id
    6. Update user blob's user.username field to new username
    7. Delete user's old username -> user_id entry from username index

    Returns:
        (success, message) tuple
    """
    # Step 1: Read current values
    user = read_user_by_id(user_id)
    if not user:
        return False, "User not found"

    current_username = user.username
    target_index = read_username_index(target_username)

    # Only early return if user already has the target username AND the index points to this user
    if (
        target_username == current_username
        and target_index is not None
        and target_index.user_id == user_id
    ):
        return True, "Already has target username"

    # Step 2: Create hold for old username
    hold_created = create_username_hold(
        current_username, user_id, time.time() + HOLD_TIME_SECONDS
    )
    if not hold_created:
        # Hold already exists, try atomic delete then create
        existing_hold = read_username_hold(current_username)
        if existing_hold:
            ops = [
                {
                    "op": "delete_username_hold",
                    "username": current_username,
                    "user_id": existing_hold.user_id,
                    "hold_expire_time": existing_hold.time_expired,
                },
                {
                    "op": "create_username_hold",
                    "username": current_username,
                    "user_id": user_id,
                    "hold_expire_time": time.time() + HOLD_TIME_SECONDS,
                },
            ]
            if not atomic_changeset(ops):
                return False, "Failed to create hold for old username"

    # Step 3: Check if allowed to proceed
    target_hold = read_username_hold(target_username)
    if target_hold:
        # Check if hold is expired
        now = time.time()
        if target_hold.time_expired > now:
            return (
                False,
                f"Target username has active hold by user {target_hold.user_id}",
            )
        # Hold is expired, will delete in step 4

    dangling_pointer = None
    if target_index:
        # Check if dangling: user.username != target_username
        target_user = read_user_by_id(target_index.user_id)
        if target_user and target_user.username != target_username:
            # Dangling pointer detected
            age = time.time() - target_index.time_created
            if age < DANGLING_POINTER_LOCKOUT:
                return False, "Dangling pointer too recent, lockout period not expired"
            dangling_pointer = target_index
        else:
            # Not dangling, username actually taken
            return (
                False,
                f"Target username already taken by user {target_index.user_id}",
            )

    # Step 4: Delete expired holds or dangling pointers
    if target_hold:
        now = time.time()
        if target_hold.time_expired <= now:
            delete_username_hold(
                target_username, target_hold.user_id, target_hold.time_expired
            )

    if dangling_pointer:
        delete_username_index(target_username, dangling_pointer.user_id)

    # Step 5: Write new username index
    if not create_username_index(target_username, user_id):
        return (
            False,
            "Failed to create username index (race condition: someone else claimed it)",
        )

    # Step 6: Update user blob's username field
    if not update_user_username(user_id, target_username):
        return False, "Failed to update user username"

    # Step 7: Delete old username index entry
    delete_username_index(current_username, user_id)

    return (
        True,
        f"Successfully changed username from {current_username} to {target_username}",
    )


def change_username(user_id: int, target_username: str) -> Tuple[bool, str]:
    """
    Change a user's username to the target username.

    This is the function the agent needs to fix. The current implementation is subject to race conditions.

    The agent should modify this function to properly handle concurrent
    username changes without violation of the username change constraints.

    Args:
        user_id: The ID of the user changing their username
        target_username: The desired new username

    Returns:
        (success, message) tuple. Success means the user now has the target
        username or already had it. Failure means the username could not be
        claimed (hold active, already taken, etc.) and the user remains unchanged.
    """
    return change_username_buggy(user_id, target_username)
