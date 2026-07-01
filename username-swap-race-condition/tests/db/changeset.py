"""
Atomic Changeset API.

Accepts arbitrary number of operations, acquires all locks upfront,
backs up state, and reverts on any failure.
"""

import time
from typing import List, Dict, Any
from copy import deepcopy

from db.lock_manager import lock_manager
from db.tables import username_index_table, username_hold_table, user_blob_table
from db.tables import UsernameIndexEntry, UsernameHoldEntry


class AtomicChangeset:
    """Executes multiple operations atomically with backup/revert on failure."""

    def __init__(self):
        self.operations = []
        self.pkeys = set()

    def add_operation(self, op: Dict[str, Any]):
        """Add an operation to the changeset."""
        self.operations.append(op)
        # Collect pkeys for locking
        if op["op"] in ("create_username_index", "delete_username_index"):
            self.pkeys.add(f"username_index:{op['username']}")
        elif op["op"] in ("create_username_hold", "delete_username_hold"):
            self.pkeys.add(f"username_hold:{op['username']}")
        elif op["op"] == "update_user_username":
            self.pkeys.add(f"user_blob:{op['user_id']}")

    def execute(self) -> bool:
        """
        Execute all operations atomically.

        Acquires global locks for all pkeys involved before starting.
        Backs up current state of all pkeys.
        If any operation fails, reverts to initial state and returns False.
        If all succeed, returns True.

        BLOCKS: Deleting and writing the same data in the same changeset is not
        allowed. For example, delete_username_index("bob", 123) followed by
        create_username_index("bob", 123) in the same changeset will be blocked.
        Similarly, create then delete of the same username is also blocked.
        This is tracked by the username for index/hold operations (regardless of
        user_id), and by user_id for user blob operations. This prevents using
        delete/create or create/delete as a way to bypass the "create should fail
        if pkey exists" rule or as a master read stand-in to check if a key exists.
        """
        # Check for delete/create of the same data in the same changeset
        # This is not allowed in a real prod setting.
        # For index and hold, any create/delete on the same username in the same
        # changeset is blocked, regardless of user_id. This prevents using
        # create/delete as a master read stand-in (e.g., create "bob" with user 123,
        # then delete "bob" with user 456 to check if "bob" exists).
        # Delete then create on the same username IS allowed (e.g., delete the old
        # dangling pointer, then create the new index). But create then delete is
        # NOT allowed, as it's likely a read stand-in.
        # For holds, the hold_expire_time is NOT included in the args key, so
        # delete/create with different expire times is also blocked. The only
        # allowed way to refresh a hold is to delete the old hold (with its actual
        # expire time) and create a new hold (with the new expire time) in separate
        # changesets, or to use the same expire time for both operations.
        # For user_blob, multiple updates to the same user_id are blocked.
        seen_args = {}
        for op in self.operations:
            if op["op"] in ("create_username_index", "delete_username_index"):
                args_key = ("username_index", op["username"])
                op_type = "create" if "create" in op["op"] else "delete"
            elif op["op"] in ("create_username_hold", "delete_username_hold"):
                args_key = ("username_hold", op["username"])
                op_type = "create" if "create" in op["op"] else "delete"
            elif op["op"] == "update_user_username":
                args_key = ("user_blob", op["user_id"])
                op_type = "update"
            else:
                continue

            if args_key in seen_args:
                prev_op_type = seen_args[args_key]
                # Allow delete then create, but not create then delete or other combos
                if not (prev_op_type == "delete" and op_type == "create"):
                    return False
            seen_args[args_key] = op_type

        with lock_manager.acquire(list(self.pkeys)):
            backup = self._backup_state()
            try:
                for op in self.operations:
                    success = self._execute_single_no_lock(op)
                    if not success:
                        self._restore_state(backup)
                        return False
                return True
            except Exception:
                self._restore_state(backup)
                return False

    def _backup_state(self) -> Dict[str, Any]:
        """Backup current state of all involved pkeys."""
        backup = {"username_index": {}, "username_hold": {}, "user_blob": {}}
        for op in self.operations:
            if op["op"] in ("create_username_index", "delete_username_index"):
                username = op["username"]
                if username not in backup["username_index"]:
                    backup["username_index"][username] = username_index_table.get(
                        username
                    )
            elif op["op"] in ("create_username_hold", "delete_username_hold"):
                username = op["username"]
                if username not in backup["username_hold"]:
                    backup["username_hold"][username] = username_hold_table.get(
                        username
                    )
            elif op["op"] == "update_user_username":
                user_id = op["user_id"]
                if user_id not in backup["user_blob"]:
                    backup["user_blob"][user_id] = user_blob_table.get(user_id)
        return backup

    def _restore_state(self, backup: Dict[str, Any]):
        """Restore state from backup."""
        for username, entry in backup["username_index"].items():
            if entry is None:
                if username in username_index_table._data:
                    del username_index_table._data[username]
            else:
                username_index_table._data[username] = entry
        for username, entry in backup["username_hold"].items():
            if entry is None:
                if username in username_hold_table._data:
                    del username_hold_table._data[username]
            else:
                username_hold_table._data[username] = entry
        for user_id, user in backup["user_blob"].items():
            if user is None:
                if user_id in user_blob_table._data:
                    del user_blob_table._data[user_id]
            else:
                user_blob_table._data[user_id] = user

    def _execute_single_no_lock(self, op: Dict[str, Any]) -> bool:
        """
        Execute a single operation without acquiring locks.
        Locks are already held by the atomic changeset.
        """
        if op["op"] == "create_username_index":
            username, user_id = op["username"], op["user_id"]
            if username in username_index_table._data:
                return False
            username_index_table._data[username] = UsernameIndexEntry(user_id=user_id)
            return True
        elif op["op"] == "delete_username_index":
            username, user_id = op["username"], op["user_id"]
            if username in username_index_table._data:
                if username_index_table._data[username].user_id == user_id:
                    del username_index_table._data[username]
            return True
        elif op["op"] == "create_username_hold":
            username, user_id, hold_time = (
                op["username"],
                op["user_id"],
                op["hold_expire_time"],
            )
            if username in username_hold_table._data:
                return False
            now = time.time()
            username_hold_table._data[username] = UsernameHoldEntry(
                user_id=user_id, time_created=now, time_expired=hold_time
            )
            return True
        elif op["op"] == "delete_username_hold":
            username, user_id, hold_time = (
                op["username"],
                op["user_id"],
                op["hold_expire_time"],
            )
            if username in username_hold_table._data:
                entry = username_hold_table._data[username]
                if entry.user_id == user_id:
                    expected_expired = hold_time
                    if abs(entry.time_expired - expected_expired) < 0.1:
                        del username_hold_table._data[username]
            return True
        elif op["op"] == "update_user_username":
            user_id, new_username = op["user_id"], op["new_username"]
            if user_id not in user_blob_table._data:
                return False
            user_blob_table._data[user_id].username = new_username
            return True
        else:
            return False


def atomic_changeset(operations: List[Dict[str, Any]]) -> bool:
    """Public API for atomic changeset."""
    cs = AtomicChangeset()
    for op in operations:
        cs.add_operation(op)
    return cs.execute()
