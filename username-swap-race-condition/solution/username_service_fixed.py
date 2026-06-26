"""
Username service with fixed change_username function.

This implementation uses atomic_changeset and target holds to prevent race conditions!
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
    """
    Change a user's username to the target username.
    
    FIXED VERSION - Uses atomic changesets and target holds to prevent race conditions.
    """
    # Step 1: Read current values
    user = read_user_by_id(user_id)
    if not user:
        return False, "User not found"
    
    current_username = user.username
    target_index = read_username_index(target_username)
    
    # Only early return if user already has the target username AND the index points to this user
    if target_username == current_username and target_index is not None and target_index.user_id == user_id:
        return True, "Already has target username"
    
    # Step 2: Create hold for old username (use atomic changeset if hold exists)
    old_hold_expire = time.time() + HOLD_TIME_SECONDS
    hold_created = create_username_hold(current_username, user_id, old_hold_expire)
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
                    "hold_expire_time": old_hold_expire,
                },
            ]
            if not atomic_changeset(ops):
                return False, "Failed to create hold for old username"
    
    # Step 3: Check if allowed to proceed
    target_hold = read_username_hold(target_username)
    if target_hold:
        now = time.time()
        if target_hold.time_expired > now:
            if target_hold.user_id != user_id:
                # Clean up old hold before returning
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
    
    # FIX: Use atomic changeset for steps 4-7 to make them atomic.
    # Include the target hold creation in the changeset, after we've checked
    # if we need to delete an expired hold. The target hold creation will fail
    # if the hold already exists (even if we couldn't see it due to cache staleness),
    # causing the entire changeset to revert. This prevents the dangling pointer
    # race where a claimer sees a dangling pointer but not the hold.
    ops = []
    
    # Step 4: Delete the target hold regardless (if it exists).
    # We already failed out for cases where the hold should block (not expired and not ours).
    # Even if it's for the same user, we want to delete and re-create to refresh the expiry time.
    # Use the target_hold's time_expired to ensure we delete the specific hold we saw.
    if target_hold:
        ops.append({
            'op': 'delete_username_hold',
            'username': target_username,
            'user_id': target_hold.user_id,
            'hold_expire_time': target_hold.time_expired,
        })
    
    # Create a hold for the target username. If the hold already exists
    # (e.g., someone else created it after our check), the create will fail
    # and the changeset will revert.
    # Calculate the expire time once to ensure consistency.
    target_hold_expire = time.time() + HOLD_TIME_SECONDS
    ops.append({
        "op": "create_username_hold",
        "username": target_username,
        "user_id": user_id,
        "hold_expire_time": target_hold_expire,
    })
    
    # Step 4 (continued): Delete dangling pointers
    if dangling_pointer:
        ops.append({
            "op": "delete_username_index",
            "username": target_username,
            "user_id": dangling_pointer.user_id,
        })
    
    # Step 5: Write new username index
    ops.append({"op": "create_username_index", "username": target_username, "user_id": user_id})
    
    # Step 6: Update user blob's username field
    ops.append({
        "op": "update_user_username",
        "user_id": user_id,
        "new_username": target_username,
    })
    
    # Step 7: Delete old username index entry
    ops.append({
        "op": "delete_username_index",
        "username": current_username,
        "user_id": user_id,
    })
    
    # Note: We do NOT delete the target hold we just created. The hold will
    # expire naturally after HOLD_TIME_SECONDS. The hold served its purpose
    # as a temporary "intent to claim" lock during the atomic operation.
    
    # Execute all operations atomically
    if not atomic_changeset(ops):
        # Clean up old hold on failure
        delete_username_hold(current_username, user_id, old_hold_expire)
        return False, "Failed to execute atomic username change (race condition prevented)"
    
    return True, f"Successfully changed username from {current_username} to {target_username}"
