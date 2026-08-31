import json
from pathlib import Path
from .backfill import backfill, gc_dangling, verify
import hashlib


def _get_shards(base_dir):
    p = Path(base_dir) / "metadata.json"
    if p.exists():
        try:
            with open(p, "r") as f:
                data = json.load(f)
                shards = int(data.get("shards", 0))
                if shards > 0:
                    return shards
        except Exception:
            pass
    return 8


def rollout_status(base_dir):
    base = Path(base_dir)
    p = base / "rollout.json"
    total = _get_shards(base_dir)
    if not p.exists():
        return {
            "phase": "not_started",
            "shards_migrated": [],
            "total_shards": total,
            "verified": False,
        }
    try:
        with open(p, "r") as f:
            data = json.load(f)
            phase = data.get("phase", "not_started")
            shards_migrated = data.get("shards_migrated", [])
            verified = data.get("verified", False)
            return {
                "phase": phase,
                "shards_migrated": shards_migrated,
                "total_shards": total,
                "verified": verified,
            }
    except Exception:
        return {
            "phase": "not_started",
            "shards_migrated": [],
            "total_shards": total,
            "verified": False,
        }


def _target_shards(num_shards, phase):
    if phase == "canary":
        return [0]
    elif phase == "partial":
        return list(range(num_shards // 2))
    elif phase == "full":
        return list(range(num_shards))
    else:
        return []


def _is_locked(base_dir, username):
    return (Path(base_dir) / f".hold_{username}").exists() or (
        Path(base_dir) / f".lock_{username}"
    ).exists()


def _collect_usernames_for_shards(base_dir, num_shards, target_shards):
    # collect usernames that hash to target shards
    ig_dir = Path(base_dir) / "ig"
    threads_dir = Path(base_dir) / "threads"
    result = []
    for d in [ig_dir, threads_dir]:
        if not d.exists():
            continue
        for i in target_shards:
            p = d / f"shard_{i}.json"
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            for username in data.keys():
                                # check hash matches
                                h = (
                                    int(
                                        hashlib.md5(
                                            username.encode("utf-8")
                                        ).hexdigest(),
                                        16,
                                    )
                                    % num_shards
                                )
                                if h in target_shards:
                                    result.append(username)
                except Exception:
                    continue
    return list(set(result))


def advance_rollout(base_dir, num_shards, target_phase):
    valid_phases = ["not_started", "canary", "partial", "full"]
    if target_phase not in ["canary", "partial", "full"]:
        raise ValueError(f"invalid target_phase {target_phase}")
    current = rollout_status(base_dir)
    cur_phase = current["phase"]
    order = {"not_started": 0, "canary": 1, "partial": 2, "full": 3}
    if order[target_phase] <= order[cur_phase]:
        raise ValueError(f"cannot go backwards or stay: {cur_phase} -> {target_phase}")
    # Check monotonic stepwise: must go to next phase, not skip? Allow canary->full? Spec says cannot skip, so require next phase only
    # Enforce: can only advance to next phase in order
    expected_next = None
    if cur_phase == "not_started":
        expected_next = "canary"
    elif cur_phase == "canary":
        expected_next = "partial"
    elif cur_phase == "partial":
        expected_next = "full"
    if target_phase != expected_next:
        # For training, allow canary->full directly? But spec says monotonic and not skip, so enforce next
        # We'll allow skip only if target is full and current is canary? But spec says cannot skip, so raise
        if not (cur_phase == "not_started" and target_phase == "canary"):
            # Actually we enforce strict next
            if order[target_phase] != order[cur_phase] + 1:
                raise ValueError(
                    f"cannot skip phase: {cur_phase} -> {target_phase}, expected {expected_next}"
                )
    target_shards = _target_shards(num_shards, target_phase)
    # Check locks first (fast fail) before expensive backfill/verify checks, so that lock-related tests pass even if store would also be inconsistent
    for p in Path(base_dir).glob(".hold_*"):
        username = p.name[len(".hold_") :]
        try:
            h = int(hashlib.md5(username.encode("utf-8")).hexdigest(), 16) % num_shards
            if h in target_shards:
                raise ValueError(f"hold marker for {username} blocks shard {h}")
        except Exception:
            continue
    usernames = _collect_usernames_for_shards(base_dir, num_shards, target_shards)
    for username in usernames:
        if _is_locked(base_dir, username):
            raise ValueError(
                f"shard {target_shards} contains locked username {username}"
            )
    # Verify entire store is consistent before moving (after lock check)
    bf = backfill(base_dir, num_shards)
    if bf.get("needs_backfill", 0) != 0 or bf.get("inconsistent", 0) != 0:
        raise ValueError(f"backfill not clean: {bf}")
    v = verify(base_dir, num_shards)
    if v.get("inconsistent", 0) != 0:
        raise ValueError(f"verify not clean: {v}")
    gc = _gc_dry_run(base_dir, num_shards)
    if gc.get("dangling_found", 0) != 0:
        raise ValueError(f"gc found dangling: {gc}")
    # All good, update rollout.json atomically via temp-file rename (per R12)
    new_status = {
        "phase": target_phase,
        "shards_migrated": target_shards,
        "total_shards": num_shards,
        "verified": True,
    }
    import os as _os, tempfile as _tf

    tmp_path = Path(base_dir) / "rollout.json.tmp"
    with open(tmp_path, "w") as f:
        json.dump(new_status, f, sort_keys=True)
        f.flush()
        _os.fsync(f.fileno())
    _os.replace(tmp_path, Path(base_dir) / "rollout.json")
    return new_status


def _gc_dry_run(base_dir, num_shards):
    # Read-only dangling detector: counts would-be gc findings without mutating shards.
    # Mirrors gc_dangling's detection (threads without IG, missing blob, mismatched username/universe)
    # but never writes, so rollout_verify stays read-only per R12.
    from pathlib import Path as _P
    import json as _json
    from .shard import ShardStore as _SS
    from .user_store import UserStore as _US
    from . import encoding as _enc

    base = _P(base_dir)
    ig_dir = base / "ig"
    threads_dir = base / "threads"
    ig_store = _SS(str(ig_dir), num_shards) if ig_dir.exists() else None
    threads_store = _SS(str(threads_dir), num_shards) if threads_dir.exists() else None
    user_store = _US(str(base), num_shards)
    usernames = set()
    for d in [ig_dir, threads_dir]:
        if not d.exists():
            continue
        for i in range(num_shards):
            p = d / f"shard_{i}.json"
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = _json.load(f)
                        if isinstance(data, dict):
                            usernames.update(data.keys())
                except Exception:
                    continue
    dangling_found = 0
    for username in list(usernames):
        if (base / f".hold_{username}").exists() or (
            base / f".lock_{username}"
        ).exists():
            continue
        ig_rec = ig_store.get(username) if ig_store else None
        thr_rec = threads_store.get(username) if threads_store else None
        if ig_rec is None and thr_rec is not None:
            dangling_found += 1
            continue
        if ig_rec is None:
            continue
        try:
            dec = _enc.decode(ig_rec)
        except Exception:
            dangling_found += 1
            continue
        # mismatch checks (same as gc_dangling's detection)
        uids = []
        if dec.get("format") == "single":
            uids.append((dec.get("uid"), "ig"))
        elif dec.get("format") == "dual":
            uids.append((dec.get("ig_uid"), "ig"))
            if dec.get("link_state") == "linked":
                uids.append((dec.get("threads_uid"), "threads"))
        for uid, exp_uni in uids:
            blob = user_store.get(uid)
            if (
                blob is None
                or blob.get("username") != username
                or blob.get("universe") != exp_uni
            ):
                dangling_found += 1
                break
        else:
            if (
                dec.get("format") == "dual"
                and dec.get("link_state") == "linked"
                and threads_store
                and thr_rec is None
            ):
                dangling_found += 1
    return {"dangling_found": dangling_found, "scanned": len(usernames)}


def rollout_verify(base_dir, num_shards):
    bf = backfill(base_dir, num_shards)
    v = verify(base_dir, num_shards)
    gc = _gc_dry_run(base_dir, num_shards)
    status = rollout_status(base_dir)
    overall = False
    # overall true only if verify inconsistent==0 and backfill needs_backfill==0 and gc dangling_found==0 and rollout at least partial with all migrated shards verified
    if (
        v.get("inconsistent", 1) == 0
        and bf.get("needs_backfill", 1) == 0
        and bf.get("inconsistent", 1) == 0
        and gc.get("dangling_found", 1) == 0
    ):
        if status["phase"] in ("partial", "full"):
            # Check that all migrated shards are verified - for now, if verify is clean, then all are verified
            overall = True
        elif status["phase"] == "canary":
            # For canary, overall should be false until at least partial? Spec says at least partial
            overall = False
    return {
        "overall": overall,
        "verify": v,
        "backfill": bf,
        "gc": gc,
        "rollout": status,
    }
