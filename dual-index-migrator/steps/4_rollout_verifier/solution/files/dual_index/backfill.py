import json
from pathlib import Path
from .shard import ShardStore
from .user_store import UserStore
from . import encoding

def _collect_usernames(base_dir, num_shards):
    # collect all usernames from ig and threads shards
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
    # also check legacy single store at base_dir directly (for Step1 compat, but Step3 uses per-universe)
    # Not needed for Step3
    return sorted(usernames)

def _is_locked(base_dir, username):
    return (Path(base_dir) / f".hold_{username}").exists() or (Path(base_dir) / f".lock_{username}").exists()

def backfill(base_dir, num_shards):
    base = Path(base_dir)
    ig_store = ShardStore(str(base / "ig"), num_shards) if (base / "ig").exists() else ShardStore(str(base), num_shards)
    threads_store = ShardStore(str(base / "threads"), num_shards) if (base / "threads").exists() else None
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
        if ig_rec is None:
            # check legacy?
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            inconsistent += 1
            continue
        # check format
        if dec.get("format") == "dual":
            already_dual += 1
        else:
            needs_backfill += 1
        # verify blobs
        # For single, check ig blob
        # For dual linked, check both, for dual unlinked only ig
        uids_to_check = []
        expected = []
        if dec.get("format") == "single":
            uids_to_check.append(dec["uid"])
            expected.append(("ig", dec["uid"]))
        elif dec.get("format") == "dual":
            uids_to_check.append(dec["ig_uid"])
            expected.append(("ig", dec["ig_uid"]))
            if dec["link_state"] == "linked":
                uids_to_check.append(dec["threads_uid"])
                expected.append(("threads", dec["threads_uid"]))
        # check each uid blob
        is_consistent = True
        for (exp_universe, uid) in expected:
            blob = user_store.get(uid)
            if blob is None:
                is_consistent = False
                break
            if blob.get("username") != username or blob.get("universe") != exp_universe:
                is_consistent = False
                break
        # also check Threads index consistency for dual
        if dec.get("format") == "dual" and dec.get("link_state") == "linked":
            if threads_store:
                thr_rec = threads_store.get(username)
                if thr_rec is None:
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
        "errors": errors
    }

def gc_dangling(base_dir, num_shards):
    base = Path(base_dir)
    ig_store = ShardStore(str(base / "ig"), num_shards) if (base / "ig").exists() else ShardStore(str(base), num_shards)
    threads_store = ShardStore(str(base / "threads"), num_shards) if (base / "threads").exists() else None
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
        if ig_rec is None:
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            dangling_found += 1
            # remove invalid
            # Use atomic delete via shard directly (not via AtomicIndex to avoid lock)
            # For simplicity, delete via store
            p = ig_store._shard_path(username)
            data = ig_store._load_shard(p)
            if username in data:
                del data[username]
                ig_store._save_shard(p, data)
                removed += 1
            continue
        # Determine expected uids and check blobs
        # Check for Threads without IG (should not happen for Step3, but handle)
        # Check blob mismatches
        uids = []
        if dec.get("format") == "single":
            uids = [(dec["uid"], "ig")]
        elif dec.get("format") == "dual":
            uids = [(dec["ig_uid"], "ig")]
            if dec["link_state"] == "linked":
                uids.append((dec["threads_uid"], "threads"))
        for (uid, exp_universe) in uids:
            blob = user_store.get(uid)
            if blob is None:
                dangling_found += 1
                # missing blob -> remove index as dangling (since we must not create blobs)
                # For now, treat as dangling and remove index entry
                # But only if we want to remove? Spec says must not create missing blobs, so remove index
                # We'll remove the username index entry from both IG and Threads
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
                break  # only count once per username
            else:
                # check username and universe match
                if blob.get("username") != username or blob.get("universe") != exp_universe:
                    dangling_found += 1
                    # Repair per available-vs-taken rule
                    blob_username = blob.get("username")
                    # Check if blob's username is available
                    # Available if no IG entry for that name, or that entry already points to same uid
                    # Check IG store for blob_username
                    other_ig = ig_store.get(blob_username)
                    is_available = False
                    if other_ig is None:
                        is_available = True
                    else:
                        try:
                            other_dec = encoding.decode(other_ig)
                            # Check if other points to same uid
                            if other_dec.get("format") == "single" and other_dec.get("uid") == uid:
                                is_available = True
                            elif other_dec.get("format") == "dual" and (other_dec.get("ig_uid") == uid or other_dec.get("threads_uid") == uid):
                                is_available = True
                        except ValueError:
                            is_available = False
                    if is_available:
                        # update available index entry to point at blob and delete initial
                        # Create new index entry for blob_username with same data as current username's index
                        # Copy current ig_rec but change username to blob_username
                        new_rec = dict(ig_rec)
                        new_rec["username"] = blob_username
                        # For dual, need to re-encode with new username to keep validation
                        try:
                            if dec.get("format") == "single":
                                new_rec = encoding.encode_single(blob_username, dec["uid"])
                            else:
                                new_rec = encoding.encode_dual(blob_username, dec["ig_uid"], dec["threads_uid"], dec["link_state"])
                        except ValueError:
                            new_rec = dict(ig_rec)
                            new_rec["username"] = blob_username
                        ig_store.put(blob_username, new_rec)
                        if dec.get("format") == "dual" and dec.get("link_state") == "linked" and threads_store:
                            thr_rec = threads_store.get(username)
                            if thr_rec:
                                new_thr = dict(thr_rec)
                                new_thr["username"] = blob_username
                                threads_store.put(blob_username, new_thr)
                        # Delete initial
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
                        repaired += 1
                    else:
                        # not available -> update blob username to match original index lookup (keep universe)
                        # Keep universe unchanged (immutable)
                        new_blob = dict(blob)
                        new_blob["username"] = username
                        # Ensure universe stays same
                        try:
                            user_store.put(uid, new_blob)
                            repaired += 1
                        except ValueError:
                            # if put fails due to universe immutable? Should not happen since we keep same universe
                            pass
                    break  # only handle first mismatch per username
        # Also check Threads without IG
        if threads_store:
            thr_rec = threads_store.get(username)
            ig_rec_check = ig_store.get(username)
            if thr_rec is not None and ig_rec_check is None:
                dangling_found += 1
                p_thr = threads_store._shard_path(username)
                data_thr = threads_store._load_shard(p_thr)
                if username in data_thr:
                    del data_thr[username]
                    threads_store._save_shard(p_thr, data_thr)
                removed += 1
    return {"scanned": scanned, "dangling_found": dangling_found, "repaired": repaired, "removed": removed}

def verify(base_dir, num_shards):
    # Similar to backfill but read-only
    res = backfill(base_dir, num_shards)
    # backfill already computes consistent/inconsistent
    total = res["total"]
    consistent = res["consistent"]
    inconsistent = res["inconsistent"]
    # also collect inconsistent list
    base = Path(base_dir)
    ig_store = ShardStore(str(base / "ig"), num_shards) if (base / "ig").exists() else ShardStore(str(base), num_shards)
    threads_store = ShardStore(str(base / "threads"), num_shards) if (base / "threads").exists() else None
    user_store = UserStore(str(base), num_shards)
    usernames = _collect_usernames(base_dir, num_shards)
    inconsistent_list = []
    for username in usernames:
        ig_rec = ig_store.get(username)
        if ig_rec is None:
            continue
        try:
            dec = encoding.decode(ig_rec)
        except ValueError:
            inconsistent_list.append(username)
            continue
        # check blobs
        uids = []
        if dec.get("format") == "single":
            uids = [(dec["uid"], "ig")]
        elif dec.get("format") == "dual":
            uids = [(dec["ig_uid"], "ig")]
            if dec["link_state"] == "linked":
                uids.append((dec["threads_uid"], "threads"))
        is_consistent = True
        for (uid, exp_universe) in uids:
            blob = user_store.get(uid)
            if blob is None or blob.get("username") != username or blob.get("universe") != exp_universe:
                is_consistent = False
                break
        if threads_store and dec.get("format") == "dual" and dec.get("link_state") == "linked":
            if threads_store.get(username) is None:
                is_consistent = False
        if not is_consistent:
            inconsistent_list.append(username)
    return {"total": total, "consistent": consistent, "inconsistent": inconsistent, "inconsistent_list": inconsistent_list, "needs_backfill": res["needs_backfill"]}
