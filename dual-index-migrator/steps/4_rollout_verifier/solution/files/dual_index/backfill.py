# dual-index-migrator - deterministic offline migration v4.3
# step 4 - file-backed contract
# step 4 - deterministic
import json
from pathlib import Path
from .shard import ShardStore
from .user_store import UserStore
from . import encoding


def _collect_usernames(base_dir, num_shards):
    ig_dir = Path(base_dir) / "ig"
    threads_dir = Path(base_dir) / "threads"
    usernames = set()
    for d in [ig_dir, threads_dir]:
        if not d.exists():
            continue
        for i in range(num_shards):
            p = d / f"shard_{i}.json"
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            usernames.update(data.keys())
                except Exception:
                    continue
    return sorted(usernames)


def _is_locked(base_dir, username):
    return (Path(base_dir) / f".hold_{username}").exists() or (
        Path(base_dir) / f".lock_{username}"
    ).exists()


def backfill(base_dir, num_shards):
    base = Path(base_dir)
    ig_store = (
        ShardStore(str(base / "ig"), num_shards)
        if (base / "ig").exists()
        else ShardStore(str(base), num_shards)
    )
    threads_store = (
        ShardStore(str(base / "threads"), num_shards)
        if (base / "threads").exists()
        else None
    )
    user_store = UserStore(str(base), num_shards)
    usernames = _collect_usernames(base_dir, num_shards)
    total = len(usernames)
    already_dual = 0
    needs_backfill = 0
    consistent = 0
    inconsistent = 0
    errors = 0
    for username in usernames:
        if _is_locked(base_dir, username):
            errors += 1
            continue
        ig_rec = ig_store.get(username)
        thr_rec = threads_store.get(username) if threads_store else None
        # Threads without IG is inconsistent
        if ig_rec is None and thr_rec is not None:
            inconsistent += 1
            continue
        if ig_rec is None:
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            inconsistent += 1
            continue
        if dec.get("format") == "dual":
            already_dual += 1
        else:
            needs_backfill += 1
        uids_to_check = []
        if dec.get("format") == "single":
            uids_to_check.append((dec["uid"], "ig"))
        elif dec.get("format") == "dual":
            uids_to_check.append((dec["ig_uid"], "ig"))
            if dec["link_state"] == "linked":
                uids_to_check.append((dec["threads_uid"], "threads"))
        is_consistent = True
        for uid, exp_universe in uids_to_check:
            blob = user_store.get(uid)
            if blob is None:
                is_consistent = False
                break
            if blob.get("username") != username or blob.get("universe") != exp_universe:
                is_consistent = False
                break
        if dec.get("format") == "dual" and dec.get("link_state") == "linked":
            if threads_store:
                if threads_store.get(username) is None:
                    is_consistent = False
        if is_consistent:
            consistent += 1
        else:
            inconsistent += 1
    return {
        "total": total,
        "already_dual": already_dual,
        "needs_backfill": needs_backfill,
        "consistent": consistent,
        "inconsistent": inconsistent,
        "errors": errors,
    }


def gc_dangling(base_dir, num_shards):
    base = Path(base_dir)
    ig_store = (
        ShardStore(str(base / "ig"), num_shards)
        if (base / "ig").exists()
        else ShardStore(str(base), num_shards)
    )
    threads_store = (
        ShardStore(str(base / "threads"), num_shards)
        if (base / "threads").exists()
        else None
    )
    user_store = UserStore(str(base), num_shards)
    usernames = _collect_usernames(base_dir, num_shards)
    scanned = len(usernames)
    dangling_found = 0
    repaired = 0
    removed = 0
    for username in list(usernames):
        if _is_locked(base_dir, username):
            continue
        ig_rec = ig_store.get(username)
        thr_rec = threads_store.get(username) if threads_store else None
        # Threads without IG -> dangling
        if ig_rec is None and thr_rec is not None:
            dangling_found += 1
            p_thr = threads_store._shard_path(username)
            data_thr = threads_store._load_shard(p_thr)
            if username in data_thr:
                del data_thr[username]
                threads_store._save_shard(p_thr, data_thr)
                removed += 1
            continue
        if ig_rec is None:
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            dangling_found += 1
            p = ig_store._shard_path(username)
            data = ig_store._load_shard(p)
            if username in data:
                del data[username]
                ig_store._save_shard(p, data)
                removed += 1
            if threads_store:
                p_thr = threads_store._shard_path(username)
                data_thr = threads_store._load_shard(p_thr)
                if username in data_thr:
                    del data_thr[username]
                    threads_store._save_shard(p_thr, data_thr)
            continue
        uids = []
        if dec.get("format") == "single":
            uids = [(dec["uid"], "ig")]
        elif dec.get("format") == "dual":
            uids = [(dec["ig_uid"], "ig")]
            if dec["link_state"] == "linked":
                uids.append((dec["threads_uid"], "threads"))
        mismatch_found = False
        for uid, exp_universe in uids:
            blob = user_store.get(uid)
            if blob is None:
                dangling_found += 1
                p_ig = ig_store._shard_path(username)
                data_ig = ig_store._load_shard(p_ig)
                if username in data_ig:
                    del data_ig[username]
                    ig_store._save_shard(p_ig, data_ig)
                if threads_store:
                    p_thr = threads_store._shard_path(username)
                    data_thr = threads_store._load_shard(p_thr)
                    if username in data_thr:
                        del data_thr[username]
                        threads_store._save_shard(p_thr, data_thr)
                removed += 1
                mismatch_found = True
                break
            else:
                if (
                    blob.get("username") != username
                    or blob.get("universe") != exp_universe
                ):
                    dangling_found += 1
                    blob_username = blob.get("username")
                    other_ig = ig_store.get(blob_username)
                    is_available = False
                    if other_ig is None:
                        is_available = True
                    else:
                        try:
                            other_dec = encoding.decode(other_ig)
                            if (
                                other_dec.get("format") == "single"
                                and other_dec.get("uid") == uid
                            ):
                                is_available = True
                            elif other_dec.get("format") == "dual" and (
                                other_dec.get("ig_uid") == uid
                                or other_dec.get("threads_uid") == uid
                            ):
                                is_available = True
                        except ValueError:
                            is_available = False
                    if is_available:
                        # Move index to blob_username
                        new_rec = dict(ig_rec)
                        new_rec["username"] = blob_username
                        try:
                            if dec.get("format") == "single":
                                new_rec = encoding.encode_single(
                                    blob_username, dec["uid"]
                                )
                            else:
                                new_rec = encoding.encode_dual(
                                    blob_username,
                                    dec["ig_uid"],
                                    dec["threads_uid"],
                                    dec["link_state"],
                                )
                        except ValueError:
                            new_rec = dict(ig_rec)
                            new_rec["username"] = blob_username
                        ig_store.put(blob_username, new_rec)
                        if (
                            dec.get("format") == "dual"
                            and dec.get("link_state") == "linked"
                            and threads_store
                        ):
                            thr_rec_cur = threads_store.get(username)
                            if thr_rec_cur:
                                new_thr = dict(thr_rec_cur)
                                new_thr["username"] = blob_username
                                threads_store.put(blob_username, new_thr)
                        # Delete old
                        p_ig_old = ig_store._shard_path(username)
                        data_old = ig_store._load_shard(p_ig_old)
                        if username in data_old:
                            del data_old[username]
                            ig_store._save_shard(p_ig_old, data_old)
                        if threads_store:
                            p_thr_old = threads_store._shard_path(username)
                            data_thr_old = threads_store._load_shard(p_thr_old)
                            if username in data_thr_old:
                                del data_thr_old[username]
                                threads_store._save_shard(p_thr_old, data_thr_old)
                        # Update all blobs for this username to new username to keep consistency
                        for m_uid, m_exp in uids:
                            b = user_store.get(m_uid)
                            if b and b.get("username") != blob_username:
                                try:
                                    nb = dict(b)
                                    nb["username"] = blob_username
                                    user_store.put(m_uid, nb)
                                except ValueError:
                                    pass
                        repaired += 1
                    else:
                        new_blob = dict(blob)
                        new_blob["username"] = username
                        try:
                            user_store.put(uid, new_blob)
                            repaired += 1
                        except ValueError:
                            pass
                    mismatch_found = True
                    break
        if mismatch_found:
            continue
    return {
        "scanned": scanned,
        "dangling_found": dangling_found,
        "repaired": repaired,
        "removed": removed,
    }


def verify(base_dir, num_shards):
    res = backfill(base_dir, num_shards)
    total = res["total"]
    consistent = res["consistent"]
    inconsistent = res["inconsistent"]
    base = Path(base_dir)
    ig_store = (
        ShardStore(str(base / "ig"), num_shards)
        if (base / "ig").exists()
        else ShardStore(str(base), num_shards)
    )
    threads_store = (
        ShardStore(str(base / "threads"), num_shards)
        if (base / "threads").exists()
        else None
    )
    user_store = UserStore(str(base), num_shards)
    usernames = _collect_usernames(base_dir, num_shards)
    inconsistent_list = []
    for username in usernames:
        ig_rec = ig_store.get(username)
        thr_rec = threads_store.get(username) if threads_store else None
        if ig_rec is None and thr_rec is not None:
            inconsistent_list.append(username)
            continue
        if ig_rec is None:
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            inconsistent_list.append(username)
            continue
        uids = []
        if dec.get("format") == "single":
            uids = [(dec["uid"], "ig")]
        elif dec.get("format") == "dual":
            uids = [(dec["ig_uid"], "ig")]
            if dec["link_state"] == "linked":
                uids.append((dec["threads_uid"], "threads"))
        is_consistent = True
        for uid, exp_universe in uids:
            blob = user_store.get(uid)
            if (
                blob is None
                or blob.get("username") != username
                or blob.get("universe") != exp_universe
            ):
                is_consistent = False
                break
        if (
            threads_store
            and dec.get("format") == "dual"
            and dec.get("link_state") == "linked"
        ):
            if threads_store.get(username) is None:
                is_consistent = False
        if not is_consistent:
            inconsistent_list.append(username)
    return {
        "total": total,
        "consistent": consistent,
        "inconsistent": inconsistent,
        "inconsistent_list": inconsistent_list,
        "inconsistent_users": inconsistent_list,
        "needs_backfill": res["needs_backfill"],
        "already_dual": res.get("already_dual", 0),
        "errors": res.get("errors", 0),
    }

# step - file-backed contract - deterministic
