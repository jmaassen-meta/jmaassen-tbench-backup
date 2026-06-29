"""
Username service with fixed change_username function.
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


def change_username(user_id: int, target_username: str) -> Tuple[bool, str]:
    """Change a user's username to the target username."""
    user = read_user_by_id(user_id)
    if not user:
        return False, "User not found"
    
    current_username = user.username
    target_index = read_username_index(target_username)
    
    if target_username == current_username and target_index is not None and target_index.user_id == user_id:
        return True, "Already has target username"
    
    old_hold_expire = time.time() + HOLD_TIME_SECONDS
    hold_created = create_username_hold(current_username, user_id, old_hold_expire)
    if not hold_created:
        # Hold already exists. Delete the existing hold first, then create a new one.
        # Do not use atomic changeset with delete+create on the same data, as it's not allowed.
        existing_hold = read_username_hold(current_username)
        if existing_hold:
            delete_username_hold(current_username, existing_hold.user_id, existing_hold.time_expired)
        hold_created = create_username_hold(current_username, user_id, old_hold_expire)
        if not hold_created:
            return False, "Failed to create hold for old username"
    
    target_hold = read_username_hold(target_username)
    if target_hold:
        now = time.time()
        if target_hold.time_expired > now:
            if target_hold.user_id != user_id:
                delete_username_hold(current_username, user_id, old_hold_expire)
                return False, f"Target username has active hold by user {target_hold.user_id}"
    
    dangling_pointer = None
    if target_index:
        target_user = read_user_by_id(target_index.user_id)
        if target_user and target_user.username != target_username:
            age = time.time() - target_index.time_created
            if age < DANGLING_POINTER_LOCKOUT:
                delete_username_hold(current_username, user_id, old_hold_expire)
                return False, "Dangling pointer too recent, lockout period not expired"
            dangling_pointer = target_index
        else:
            delete_username_hold(current_username, user_id, old_hold_expire)
            return False, f"Target username already taken by user {target_index.user_id}"
    
    ops = []
    
    if target_hold:
        ops.append({
            'op': 'delete_username_hold',
            'username': target_username,
            'user_id': target_hold.user_id,
            'hold_expire_time': target_hold.time_expired,
        })
    
    target_hold_expire = time.time() + HOLD_TIME_SECONDS
    ops.append({
        "op": "create_username_hold",
        "username": target_username,
        "user_id": user_id,
        "hold_expire_time": target_hold_expire,
    })
    
    if dangling_pointer:
        ops.append({
            "op": "delete_username_index",
            "username": target_username,
            "user_id": dangling_pointer.user_id,
        })
    
    ops.append({"op": "create_username_index", "username": target_username, "user_id": user_id})
    ops.append({"op": "update_user_username", "user_id": user_id, "new_username": target_username})
    ops.append({"op": "delete_username_index", "username": current_username, "user_id": user_id})
    
    if not atomic_changeset(ops):
        delete_username_hold(current_username, user_id, old_hold_expire)
        return False, "Failed to execute atomic username change (race condition prevented)"
    
    return True, f"Successfully changed username from {current_username} to {target_username}"
