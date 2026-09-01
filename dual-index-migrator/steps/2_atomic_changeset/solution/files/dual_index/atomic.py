# dual-index-migrator - deterministic offline migration
# step 2 - file-backed contract
# step 2 - deterministic
import json
import hashlib
from pathlib import Path
from .shard import ShardStore
from . import encoding

WAL_NAME = "wal.jsonl"
LOCK_PREFIX = ".lock_"
HOLD_PREFIX = ".hold_"


class AtomicIndex:
    def __init__(self, base_dir, num_shards):
        self.base_dir = Path(base_dir)
        self.num_shards = int(num_shards)
        if self.num_shards <= 0:
            raise ValueError("num_shards must be >0")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # per-universe dirs
        self.ig_dir = self.base_dir / "ig"
        self.threads_dir = self.base_dir / "threads"
        self.ig_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        # shard stores per universe
        self.ig_store = ShardStore(str(self.ig_dir), self.num_shards)
        self.threads_store = ShardStore(str(self.threads_dir), self.num_shards)
        self.wal_path = self.base_dir / WAL_NAME

    def _lock_path(self, username):
        return self.base_dir / f"{LOCK_PREFIX}{username}"

    def _hold_path(self, username):
        return self.base_dir / f"{HOLD_PREFIX}{username}"

    def _is_locked(self, username):
        # check hold marker (test hook) or actual lock file
        return self._hold_path(username).exists() or self._lock_path(username).exists()

    def _acquire(self, username):
        if self._is_locked(username):
            raise ValueError(f"row {username} is locked")
        # try to create lock file exclusively
        p = self._lock_path(username)
        try:
            # Use 'x' to fail if exists - handles race
            with open(p, "x") as f:
                f.write(username)
        except FileExistsError:
            raise ValueError(f"row {username} is locked")
        return p

    def _release(self, username):
        p = self._lock_path(username)
        try:
            p.unlink()
        except FileNotFoundError:
            pass

    def _acquire_many(self, usernames):
        # sorted order to avoid deadlock
        sorted_users = sorted(set(usernames))
        acquired = []
        try:
            for u in sorted_users:
                if self._hold_path(u).exists():
                    raise ValueError(f"row {u} is locked (hold marker)")
                self._acquire(u)
                acquired.append(u)
        except Exception:
            # release any acquired on failure
            for u in acquired:
                self._release(u)
            raise
        return acquired

    def _release_many(self, usernames):
        for u in sorted(set(usernames)):
            self._release(u)

    def _wal_append(self, entry):
        with open(self.wal_path, "a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def _wal_read_all(self):
        if not self.wal_path.exists():
            return []
        try:
            with open(self.wal_path, "r") as f:
                lines = [json.loads(l) for l in f if l.strip()]
                return lines
        except Exception:
            return []

    def _wal_has_pending(self, usernames):
        # check if any pending intent for these users without commit
        # pending = intent without matching commit id
        entries = self._wal_read_all()
        intents = {
            e["id"]: e
            for e in entries
            if e.get("state") == "intent"
            and e.get("username") in usernames
            or e.get("from_user") in usernames
            or e.get("to_user") in usernames
        }
        # Actually for rename, from_user/to_user; for link/unlink, username
        # Simpler: any intent id that has no commit entry
        intent_ids = set()
        commit_ids = set()
        for e in entries:
            if e.get("state") == "intent":
                intent_ids.add(e.get("id"))
            elif e.get("state") == "commit":
                commit_ids.add(e.get("id"))
        pending_ids = intent_ids - commit_ids
        if not pending_ids:
            return False
        # check if any pending involves these usernames
        for e in entries:
            if e.get("state") == "intent" and e.get("id") in pending_ids:
                # check if operation involves any of usernames
                op_users = set()
                if "username" in e:
                    op_users.add(e["username"])
                if "from_user" in e:
                    op_users.add(e["from_user"])
                    op_users.add(e["to_user"])
                if op_users & set(usernames):
                    return True
        return False

    def _wal_recover_pending(self, usernames):
        entries = self._wal_read_all()
        intent_ids = {e["id"]: e for e in entries if e.get("state") == "intent"}
        commit_ids = {e["id"] for e in entries if e.get("state") == "commit"}
        pending_ids = set(intent_ids.keys()) - commit_ids
        if not pending_ids:
            return False
        # Find pending that affects our usernames
        to_recover = []
        for pid in pending_ids:
            e = intent_ids[pid]
            op_users = set()
            if "username" in e:
                op_users.add(e["username"])
            if "from_user" in e:
                op_users.add(e["from_user"])
                op_users.add(e["to_user"])
            if op_users & set(usernames):
                to_recover.append(e)
        if not to_recover:
            return False
        # For each pending, restore old values
        for e in to_recover:
            # old state stored in entry
            if e.get("op") == "link":
                username = e["username"]
                old_ig = e.get("old_ig")
                old_threads = e.get("old_threads")
                # restore IG
                if old_ig is None:
                    # was not present before? Need to delete if we created
                    # We created new entry; to restore, delete current
                    # Remove from both stores
                    # Use direct shard delete via put? We need to delete key from shard file
                    self._delete_from_store(username)
                else:
                    self.ig_store.put(username, old_ig)
                    if old_threads is not None:
                        self.threads_store.put(username, old_threads)
                    else:
                        # old was single, threads should be absent
                        self._delete_from_threads(username)
            elif e.get("op") == "unlink":
                username = e["username"]
                old_ig = e.get("old_ig")
                old_threads = e.get("old_threads")
                # restore old dual
                if old_ig is not None:
                    self.ig_store.put(username, old_ig)
                if old_threads is not None:
                    self.threads_store.put(username, old_threads)
            elif e.get("op") == "rename":
                from_user = e["from_user"]
                to_user = e["to_user"]
                old_from_ig = e.get("old_from_ig")
                old_from_threads = e.get("old_from_threads")
                old_to_ig = e.get("old_to_ig")
                old_to_threads = e.get("old_to_threads")
                # Restore from_user
                if old_from_ig is not None:
                    self.ig_store.put(from_user, old_from_ig)
                else:
                    self._delete_from_store(from_user, ig=True, threads=True)
                    # Actually from_user existed before, so we need to restore it; if old was None means it didn't exist before? But rename from_user always exists
                    pass
                # More robust: restore both
                if old_from_ig is not None:
                    self.ig_store.put(from_user, old_from_ig)
                if old_from_threads is not None:
                    self.threads_store.put(from_user, old_from_threads)
                else:
                    self._delete_from_threads(from_user)
                # Restore to_user (should have been absent before, so delete what we created)
                if old_to_ig is None:
                    self._delete_from_store(to_user, ig=True, threads=True)
                else:
                    self.ig_store.put(to_user, old_to_ig)
                    if old_to_threads is not None:
                        self.threads_store.put(to_user, old_to_threads)
            # Also need to handle generic?
        # After recovery, write commit for those pending to mark them recovered (so not pending again)
        # Instead, we can just truncate WAL or mark recovered? Simpler: rewrite WAL without pending intents
        # We'll rewrite WAL to keep only committed entries
        remaining = [
            e
            for e in entries
            if e.get("id") not in pending_ids or e.get("state") == "commit"
        ]
        # Actually we want to remove pending intents that we recovered
        # Keep only entries whose id not in pending_ids that were recovered
        # For those pending we recovered, remove their intent
        filtered = [
            e
            for e in entries
            if not (
                e.get("state") == "intent"
                and e.get("id") in {x["id"] for x in to_recover}
            )
        ]
        # Write back
        with open(self.wal_path, "w") as f:
            for e in filtered:
                f.write(json.dumps(e, sort_keys=True) + "\n")
        return True

    def _delete_from_store(self, username, ig=True, threads=True):
        # delete key from shard files
        for store, do in [(self.ig_store, ig), (self.threads_store, threads)]:
            if not do:
                continue
            path = store._shard_path(username)
            data = store._load_shard(path)
            if username in data:
                del data[username]
                store._save_shard(path, data)

    def _delete_from_threads(self, username):
        self._delete_from_store(username, ig=False, threads=True)

    def _check_and_recover(self, usernames):
        # if hold marker exists, treat as locked - but recovery check still?
        # For deterministic hold test, we should treat hold as pending lock, not WAL pending
        # So check WAL pending first, if found, recover and return True to signal error
        if self._wal_has_pending(usernames):
            # perform recovery
            self._wal_recover_pending(usernames)
            # after recovery, release any locks that might be held (but locks not yet acquired in this call)
            # The caller will have not yet acquired locks; we just recovered
            return True
        # Also check hold marker as locked
        for u in usernames:
            if self._hold_path(u).exists():
                return True  # treat as pending lock -> should error
        return False

    def link(self, username, ig_uid, threads_uid):
        # check hold/WAL before acquiring
        if self._check_and_recover([username]):
            raise ValueError(f"recovered pending for {username}, operation aborted")
        locks = self._acquire_many([username])
        try:
            # check pending again after acquiring (in case race)
            if self._wal_has_pending([username]):
                self._wal_recover_pending([username])
                raise ValueError(f"pending recovered for {username}")
            # Validate via encoding
            # Get old values
            old_ig = self.ig_store.get(username)
            old_threads = self.threads_store.get(username)
            # Validate new
            # Use encoding to validate
            rec = encoding.encode_dual(username, ig_uid, threads_uid, "linked")
            # WAL intent with old
            intent_id = hashlib.md5(
                f"link:{username}:{ig_uid}:{threads_uid}".encode()
            ).hexdigest()[:16]
            # Ensure unique per call: add random? Use hash of current time? Simplify to use username + uuid
            import uuid, time

            intent_id = str(uuid.uuid4())
            intent = {
                "id": intent_id,
                "op": "link",
                "username": username,
                "old_ig": old_ig,
                "old_threads": old_threads,
                "new_ig": {"username": username, "uid": ig_uid, "format": "single"}
                if False
                else None,  # placeholder
                "state": "intent",
            }
            # Store new values for recovery clarity
            intent["new_ig"] = {
                "username": username,
                "ig_uid": ig_uid,
                "threads_uid": threads_uid,
                "link_state": "linked",
                "format": "dual",
            }
            intent["new_threads"] = {
                "username": username,
                "uid": threads_uid,
                "format": "single",
            }  # threads store holds simple
            self._wal_append(intent)
            # Apply writes
            # IG store should hold dual record (with both uids) or single? For per-universe, IG holds ig part, Threads holds threads part
            # Let's store dual encoding in IG as full dual, and threads store holds simple mapping
            self.ig_store.put(username, rec)
            self.threads_store.put(
                username, {"username": username, "uid": threads_uid, "format": "single"}
            )
            # Commit
            self._wal_append({"id": intent_id, "state": "commit"})
            # Clean WAL: remove intent+commit? Keep both but pending check will see both and not pending
            # Optionally compact
            return rec
        finally:
            self._release_many([username])

    def unlink(self, username):
        if self._check_and_recover([username]):
            raise ValueError(f"recovered pending for {username}, operation aborted")
        locks = self._acquire_many([username])
        try:
            if self._wal_has_pending([username]):
                self._wal_recover_pending([username])
                raise ValueError(f"pending recovered for {username}")
            # Check exists
            old_ig = self.ig_store.get(username)
            old_threads = self.threads_store.get(username)
            if old_ig is None:
                raise ValueError(f"user {username} does not exist")
            # Check already unlinked: if old is single (no threads) or dual unlinked
            # Determine if already unlinked: if no threads entry, considered unlinked/single
            if old_threads is None:
                decoded = None
                if old_ig:
                    try:
                        decoded = encoding.decode(old_ig)
                    except ValueError:
                        decoded = None
                if (
                    decoded
                    and decoded.get("format") == "dual"
                    and decoded.get("link_state") == "unlinked"
                ):
                    raise ValueError(f"user {username} already unlinked")
                if old_ig is None and old_threads is None:
                    raise ValueError(f"user {username} already unlinked")
                # single with no threads is allowed to materialize to unlinked - pass
            import uuid

            intent_id = str(uuid.uuid4())
            intent = {
                "id": intent_id,
                "op": "unlink",
                "username": username,
                "old_ig": old_ig,
                "old_threads": old_threads,
                "state": "intent",
            }
            self._wal_append(intent)
            # Apply: update IG to dual unlinked, delete Threads
            # Need ig_uid from old
            old_decoded = (
                encoding.decode(old_ig)
                if old_ig and old_ig.get("format") == "dual"
                else None
            )
            if old_decoded and old_decoded.get("format") == "dual":
                ig_uid = old_decoded["ig_uid"]
            elif old_ig and old_ig.get("format") == "single":
                ig_uid = old_ig["uid"]
            else:
                # if old_ig is dual linked, get ig_uid
                if old_ig and "ig_uid" in old_ig:
                    ig_uid = old_ig["ig_uid"]
                else:
                    ig_uid = old_ig["uid"] if old_ig else 0
            new_rec = encoding.encode_dual(username, ig_uid, None, "unlinked")
            self.ig_store.put(username, new_rec)
            self._delete_from_threads(username)
            self._wal_append({"id": intent_id, "state": "commit"})
            return new_rec
        finally:
            self._release_many([username])

    def rename(self, from_user, to_user):
        # Validate usernames
        if from_user == to_user:
            raise ValueError("from and to must be different")
        # Check hold/WAL before acquiring
        if self._check_and_recover([from_user, to_user]):
            raise ValueError(f"recovered pending for rename {from_user}->{to_user}")
        # Acquire both locks in sorted order
        locks = self._acquire_many([from_user, to_user])
        try:
            if self._wal_has_pending([from_user, to_user]):
                self._wal_recover_pending([from_user, to_user])
                raise ValueError("pending recovered for rename")
            # Check from exists, to free
            from_ig = self.ig_store.get(from_user)
            from_threads = self.threads_store.get(from_user)
            if from_ig is None:
                raise ValueError(f"from_user {from_user} does not exist")
            to_ig = self.ig_store.get(to_user)
            to_threads = self.threads_store.get(to_user)
            if to_ig is not None or to_threads is not None:
                raise ValueError(f"to_user {to_user} already exists")
            # Validate to_user name
            # Use encoding to validate new username (via dummy encode)
            encoding.encode_single(to_user, 1)  # will validate username
            import uuid

            intent_id = str(uuid.uuid4())
            intent = {
                "id": intent_id,
                "op": "rename",
                "from_user": from_user,
                "to_user": to_user,
                "old_from_ig": from_ig,
                "old_from_threads": from_threads,
                "old_to_ig": to_ig,
                "old_to_threads": to_threads,
                "state": "intent",
            }
            self._wal_append(intent)
            # Apply: delete from, create to
            # Get decoded from to preserve format
            # For IG, copy from_ig but change username
            new_from_ig = None
            new_to_ig = None
            new_to_threads = None
            # Create to_user entries with same data but new username
            if from_ig:
                # Need to change username field in record
                new_to_ig = dict(from_ig)
                new_to_ig["username"] = to_user
                # If from was dual, keep same ig_uid/threads_uid/link_state but with new username
                # Re-encode to ensure validation
                if from_ig.get("format") == "dual":
                    # decode and re-encode with new username
                    dec = encoding.decode(from_ig)
                    new_to_ig = encoding.encode_dual(
                        to_user, dec["ig_uid"], dec["threads_uid"], dec["link_state"]
                    )
                else:
                    new_to_ig = encoding.encode_single(to_user, from_ig["uid"])
                self.ig_store.put(to_user, new_to_ig)
            if from_threads:
                new_to_threads = dict(from_threads)
                new_to_threads["username"] = to_user
                # threads store holds simple single
                new_to_threads = {
                    "username": to_user,
                    "uid": from_threads["uid"],
                    "format": "single",
                }
                self.threads_store.put(to_user, new_to_threads)
            # Delete from
            self._delete_from_store(from_user, ig=True, threads=True)
            self._wal_append({"id": intent_id, "state": "commit"})
            return new_to_ig
        finally:
            self._release_many([from_user, to_user])

    def read(self, username):
        # Check pending for this user first (auto-recover)
        if self._wal_has_pending([username]):
            self._wal_recover_pending([username])
            # After recovery, we still return current committed value, but we also indicate error? For read, we just return after recovery
            pass
        # Also check hold marker: if hold exists, should not return partial, but we can return committed
        # For read, if locked, we return last committed (which is current)
        ig_rec = self.ig_store.get(username)
        if ig_rec is None:
            return None
        try:
            return encoding.decode(ig_rec)
        except ValueError:
            return ig_rec
# step - file-backed contract - deterministic
# patch keepalive - deterministic
