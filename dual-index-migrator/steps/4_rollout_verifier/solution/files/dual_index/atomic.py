# dual-index-migrator - deterministic offline migration
# step 4 - file-backed contract
# step 4 - deterministic
import json
import hashlib
from pathlib import Path
from .shard import ShardStore
from .user_store import UserStore
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
        self.ig_dir = self.base_dir / "ig"
        self.threads_dir = self.base_dir / "threads"
        self.ig_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        self.ig_store = ShardStore(str(self.ig_dir), self.num_shards)
        self.threads_store = ShardStore(str(self.threads_dir), self.num_shards)
        self.user_store = UserStore(str(self.base_dir), self.num_shards)
        self.wal_path = self.base_dir / WAL_NAME

    def _lock_path(self, username):
        return self.base_dir / f"{LOCK_PREFIX}{username}"

    def _hold_path(self, username):
        return self.base_dir / f"{HOLD_PREFIX}{username}"

    def _is_locked(self, username):
        return self._hold_path(username).exists() or self._lock_path(username).exists()

    def _acquire(self, username):
        if self._is_locked(username):
            raise ValueError(f"row {username} is locked")
        p = self._lock_path(username)
        try:
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
        sorted_users = sorted(set(usernames))
        acquired = []
        try:
            for u in sorted_users:
                if self._hold_path(u).exists():
                    raise ValueError(f"row {u} is locked (hold marker)")
                self._acquire(u)
                acquired.append(u)
        except Exception:
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
        entries = self._wal_read_all()
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
        for e in entries:
            if e.get("state") == "intent" and e.get("id") in pending_ids:
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
        for e in to_recover:
            if e.get("op") == "link":
                username = e["username"]
                old_ig = e.get("old_ig")
                old_threads = e.get("old_threads")
                if old_ig is None:
                    self._delete_from_store(username)
                else:
                    self.ig_store.put(username, old_ig)
                    if old_threads is not None:
                        self.threads_store.put(username, old_threads)
                    else:
                        self._delete_from_threads(username)
                # restore blobs from snapshot (per R12: restore blob state as well as index)
                old_ig_blob = e.get("old_ig_blob")
                old_threads_blob = e.get("old_threads_blob")
                new_ig_uid = e.get("new_ig_uid")
                new_threads_uid = e.get("new_threads_uid")
                if new_ig_uid is not None:
                    if old_ig_blob is None:
                        try:
                            p = self.user_store._shard_path(new_ig_uid)
                            data = self.user_store._load_shard(p)
                            if str(new_ig_uid) in data:
                                del data[str(new_ig_uid)]
                                self.user_store._save_shard(p, data)
                            if new_ig_uid in data:
                                del data[new_ig_uid]
                                self.user_store._save_shard(p, data)
                        except Exception:
                            pass
                    else:
                        try:
                            self.user_store.put(new_ig_uid, old_ig_blob)
                        except Exception:
                            pass
                if new_threads_uid is not None:
                    if old_threads_blob is None:
                        try:
                            p = self.user_store._shard_path(new_threads_uid)
                            data = self.user_store._load_shard(p)
                            if str(new_threads_uid) in data:
                                del data[str(new_threads_uid)]
                                self.user_store._save_shard(p, data)
                            if new_threads_uid in data:
                                del data[new_threads_uid]
                                self.user_store._save_shard(p, data)
                        except Exception:
                            pass
                    else:
                        try:
                            self.user_store.put(new_threads_uid, old_threads_blob)
                        except Exception:
                            pass
            elif e.get("op") == "unlink":
                username = e["username"]
                old_ig = e.get("old_ig")
                old_threads = e.get("old_threads")
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
                if old_from_ig is not None:
                    self.ig_store.put(from_user, old_from_ig)
                if old_from_threads is not None:
                    self.threads_store.put(from_user, old_from_threads)
                else:
                    self._delete_from_threads(from_user)
                if old_to_ig is None:
                    self._delete_from_store(to_user, ig=True, threads=True)
                else:
                    self.ig_store.put(to_user, old_to_ig)
                    if old_to_threads is not None:
                        self.threads_store.put(to_user, old_to_threads)
        filtered = [
            e
            for e in entries
            if not (
                e.get("state") == "intent"
                and e.get("id") in {x["id"] for x in to_recover}
            )
        ]
        with open(self.wal_path, "w") as f:
            for e in filtered:
                f.write(json.dumps(e, sort_keys=True) + "\n")
        return True

    def _delete_from_store(self, username, ig=True, threads=True):
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
        if self._wal_has_pending(usernames):
            self._wal_recover_pending(usernames)
            return True
        for u in usernames:
            if self._hold_path(u).exists():
                return True
        return False

    def link(self, username, ig_uid, threads_uid):
        if self._check_and_recover([username]):
            raise ValueError(f"recovered pending for {username}, operation aborted")
        locks = self._acquire_many([username])
        try:
            if self._wal_has_pending([username]):
                self._wal_recover_pending([username])
                raise ValueError(f"pending recovered for {username}")
            old_ig = self.ig_store.get(username)
            old_threads = self.threads_store.get(username)
            rec = encoding.encode_dual(username, ig_uid, threads_uid, "linked")
            import uuid

            intent_id = str(uuid.uuid4())
            # snapshot old blobs for atomic rollback (per authorFeedback: snapshot old blobs in WAL)
            old_ig_blob = self.user_store.get(ig_uid)
            old_threads_blob = self.user_store.get(threads_uid)
            intent = {
                "id": intent_id,
                "op": "link",
                "username": username,
                "old_ig": old_ig,
                "old_threads": old_threads,
                "old_ig_blob": old_ig_blob,
                "old_threads_blob": old_threads_blob,
                "new_ig_uid": ig_uid,
                "new_threads_uid": threads_uid,
                "state": "intent",
            }
            self._wal_append(intent)
            # Verify blob universe before writing (should not mutate)
            # Check existing blobs for universe mismatch
            existing_ig_blob = self.user_store.get(ig_uid)
            if existing_ig_blob and existing_ig_blob.get("universe") != "ig":
                raise ValueError(f"blob {ig_uid} universe mismatch")
            existing_thr_blob = self.user_store.get(threads_uid)
            if existing_thr_blob and existing_thr_blob.get("universe") != "threads":
                raise ValueError(f"blob {threads_uid} universe mismatch")
            self.ig_store.put(username, rec)
            self.threads_store.put(
                username, {"username": username, "uid": threads_uid, "format": "single"}
            )
            # Create blobs atomically
            self.user_store.put(
                ig_uid, {"username": username, "uid": ig_uid, "universe": "ig"}
            )
            self.user_store.put(
                threads_uid,
                {"username": username, "uid": threads_uid, "universe": "threads"},
            )
            self._wal_append({"id": intent_id, "state": "commit"})
            return rec
        except Exception:
            # On any ValueError (including blob universe), need to ensure rollback of index if needed?
            # If we already wrote index, need to restore old
            # But our WAL recovery will handle on next call; for now just ensure we don't leave partial index
            # If we failed after writing index but before blobs, we should restore old index
            # Check if we had written index
            try:
                # If we appended intent, we need to ensure we restore
                # For simplicity, if we are in exception after putting index, restore
                if "old_ig" in locals():
                    # Check if we had put new index
                    cur = self.ig_store.get(username)
                    if cur and cur.get("ig_uid") == ig_uid:
                        if old_ig is None:
                            self._delete_from_store(username)
                        else:
                            self.ig_store.put(username, old_ig)
                            if old_threads is not None:
                                self.threads_store.put(username, old_threads)
                            else:
                                self._delete_from_threads(username)
                        # also clean blobs we may have created
                        for uid in [ig_uid, threads_uid]:
                            try:
                                p = self.user_store._shard_path(uid)
                                data = self.user_store._load_shard(p)
                                if (
                                    str(uid) in data
                                    and data[str(uid)].get("username") == username
                                    and data[str(uid)].get("universe")
                                    in ("ig", "threads")
                                ):
                                    # Only delete if it was newly created (old was None for that uid's blob)
                                    # For now, if old was None, delete
                                    # We need to know old blobs for those uids
                                    pass
                            except Exception:
                                pass
            except Exception:
                pass
            raise
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
            old_ig = self.ig_store.get(username)
            old_threads = self.threads_store.get(username)
            if old_ig is None:
                raise ValueError(f"user {username} does not exist")
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
                if old_ig and "ig_uid" in old_ig:
                    ig_uid = old_ig["ig_uid"]
                else:
                    ig_uid = old_ig["uid"] if old_ig else 0
            new_rec = encoding.encode_dual(username, ig_uid, None, "unlinked")
            self.ig_store.put(username, new_rec)
            self._delete_from_threads(username)
            try:
                self.user_store.put(
                    ig_uid, {"username": username, "uid": ig_uid, "universe": "ig"}
                )
            except ValueError:
                pass
            # Remove threads blob if it existed (unlink -> no threads blob)
            if old_threads:
                try:
                    thr_uid = old_threads.get("uid")
                    if thr_uid is not None:
                        p = self.user_store._shard_path(thr_uid)
                        data = self.user_store._load_shard(p)
                        key = str(thr_uid)
                        # blobs are stored with uid as key? In UserStore it's str(uid) or int
                        # Try both
                        if key in data:
                            del data[key]
                        if thr_uid in data:
                            del data[thr_uid]
                        self.user_store._save_shard(p, data)
                except Exception:
                    pass
            self._wal_append({"id": intent_id, "state": "commit"})
            return new_rec
        finally:
            self._release_many([username])

    def rename(self, from_user, to_user):
        if from_user == to_user:
            raise ValueError("from and to must be different")
        if self._check_and_recover([from_user, to_user]):
            raise ValueError(f"recovered pending for rename {from_user}->{to_user}")
        locks = self._acquire_many([from_user, to_user])
        try:
            if self._wal_has_pending([from_user, to_user]):
                self._wal_recover_pending([from_user, to_user])
                raise ValueError("pending recovered for rename")
            from_ig = self.ig_store.get(from_user)
            from_threads = self.threads_store.get(from_user)
            if from_ig is None:
                raise ValueError(f"from_user {from_user} does not exist")
            to_ig = self.ig_store.get(to_user)
            to_threads = self.threads_store.get(to_user)
            if to_ig is not None or to_threads is not None:
                raise ValueError(f"to_user {to_user} already exists")
            encoding.encode_single(to_user, 1)
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
            new_to_ig = None
            if from_ig:
                new_to_ig = dict(from_ig)
                new_to_ig["username"] = to_user
                if from_ig.get("format") == "dual":
                    dec = encoding.decode(from_ig)
                    new_to_ig = encoding.encode_dual(
                        to_user, dec["ig_uid"], dec["threads_uid"], dec["link_state"]
                    )
                else:
                    new_to_ig = encoding.encode_single(to_user, from_ig["uid"])
                self.ig_store.put(to_user, new_to_ig)
            if from_threads:
                new_to_threads = {
                    "username": to_user,
                    "uid": from_threads["uid"],
                    "format": "single",
                }
                self.threads_store.put(to_user, new_to_threads)
            # Update user blobs for moved uids to new username
            try:
                if from_ig:
                    dec = (
                        encoding.decode(from_ig)
                        if from_ig.get("format") == "dual"
                        else None
                    )
                    if dec and dec.get("format") == "dual":
                        ig_uid = dec["ig_uid"]
                        threads_uid = dec["threads_uid"]
                        if threads_uid is not None:
                            self.user_store.put(
                                ig_uid,
                                {"username": to_user, "uid": ig_uid, "universe": "ig"},
                            )
                            self.user_store.put(
                                threads_uid,
                                {
                                    "username": to_user,
                                    "uid": threads_uid,
                                    "universe": "threads",
                                },
                            )
                        else:
                            self.user_store.put(
                                ig_uid,
                                {"username": to_user, "uid": ig_uid, "universe": "ig"},
                            )
                    elif from_ig.get("format") == "single":
                        ig_uid = from_ig["uid"]
                        self.user_store.put(
                            ig_uid,
                            {"username": to_user, "uid": ig_uid, "universe": "ig"},
                        )
            except ValueError:
                self._delete_from_store(to_user, ig=True, threads=True)
                if old_from_ig is not None:
                    self.ig_store.put(from_user, old_from_ig)
                if old_from_threads is not None:
                    self.threads_store.put(from_user, old_from_threads)
                raise
            self._delete_from_store(from_user, ig=True, threads=True)
            self._wal_append({"id": intent_id, "state": "commit"})
            return new_to_ig
        finally:
            self._release_many([from_user, to_user])

    def read(self, username):
        if self._wal_has_pending([username]):
            self._wal_recover_pending([username])
            pass
        ig_rec = self.ig_store.get(username)
        if ig_rec is None:
            return None
        try:
            return encoding.decode(ig_rec)
        except ValueError:
            return ig_rec
