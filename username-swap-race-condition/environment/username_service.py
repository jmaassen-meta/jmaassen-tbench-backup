"""Username service with change_username function."""

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


def change_username(user_id: int, target_username: str) -> Tuple[bool, str]:
    """
    Change a user's username to the target username.

    Args:
        user_id: The ID of the user changing their username
        target_username: The desired new username

    Returns:
        (success, message) tuple. Success means the user now has the target
        username or already had it. Failure means the username could not be
        claimed (hold active, already taken, etc.) and the user remains unchanged.
    """
    user = read_user_by_id(user_id)
    if not user:
        return False, "User not found"

    current_username = user.username
    target_index = read_username_index(target_username)

    if (
        target_username == current_username
        and target_index is not None
        and target_index.user_id == user_id
    ):
        return True, "Already has target username"

    hold_created = create_username_hold(
        current_username, user_id, time.time() + HOLD_TIME_SECONDS
    )
    if not hold_created:
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

    target_hold = read_username_hold(target_username)
    if target_hold:
        now = time.time()
        if target_hold.time_expired > now:
            return (
                False,
                f"Target username has active hold by user {target_hold.user_id}",
            )

    dangling_pointer = None
    if target_index:
        target_user = read_user_by_id(target_index.user_id)
        if target_user and target_user.username != target_username:
            age = time.time() - target_index.time_created
            if age < DANGLING_POINTER_LOCKOUT:
                return False, "Dangling pointer too recent, lockout period not expired"
            dangling_pointer = target_index
        else:
            return (
                False,
                f"Target username already taken by user {target_index.user_id}",
            )

    if target_hold:
        now = time.time()
        if target_hold.time_expired <= now:
            delete_username_hold(
                target_username, target_hold.user_id, target_hold.time_expired
            )

    if dangling_pointer:
        delete_username_index(target_username, dangling_pointer.user_id)

    if not create_username_index(target_username, user_id):
        return (
            False,
            "Failed to create username index (race condition: someone else claimed it)",
        )

    if not update_user_username(user_id, target_username):
        return False, "Failed to update user username"

    delete_username_index(current_username, user_id)

    return (
        True,
        f"Successfully changed username from {current_username} to {target_username}",
    )
