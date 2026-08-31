# codimango/dual-index-migrator

Secondary index dual-format migration - sharded username → uid index that moves from single-format (`{username,uid}`) to dual-format (IG/Threads linked) with atomic cross-shard writes, backfill verification, dangling-pointer GC, and phased rollout.

## Why this task

Models must carry file-backed state across 4 turns: no in-memory fixtures survive - every check re-reads `base_dir/ig/`, `base_dir/threads/`, `base_dir/users/`, `base_dir/rollout.json`, and the per-username global lock `.lock_<username>` / test hold marker `.hold_<username>`.

## Steps

1. **1_dual_format_index** - `encoding.py` (single/dual `{username,uid,link_state,threads_uid}` + validation), `shard.py` (stable hash, not salted `hash()`), `store.py` (single vs dual format, overwrite semantics), `cli init/write/read`. ~45 tests.
2. **2_atomic_changeset** - `atomic.py` (`AtomicIndex.link/unlink/rename` across two `ShardStore`s + `UserStore` via `write-ahead log (WAL)` intent→commit, per-username global file locks, deterministic hold `.hold_<username>`, ordered two-lock for `rename` via `sorted([from,to])`). ~24 tests.
3. **3_backfill_gc** - `user_store.py` (`universe` in `{ig,threads}`, immutable once set, verified before any index write), `backfill.py` (`backfill` is verification-only: reports `needs_backfill`/`consistent`, never creates missing blobs; `gc_dangling` repairs dangling pointers: available→move index to `blob.username`+delete old, not-available→fix `blob.username`; `verify` read-only). ~20 tests.
4. **4_rollout_verifier** - `rollout.py` (`rollout_status`/`advance_rollout`/`rollout_verify`), rollout state at `base_dir/rollout.json` with exact shards: canary `[0]`, partial `[0..N//2-1]`, full `[0..N-1]`; `advance_rollout` requires `verify[inconsistent]==0`, `backfill[needs_backfill]==0`, `gc[dangling_found]==0`, per-username hold check on target shards, monotonic `not_started→canary→partial→full` without skip/backwards; `rollout-verify` end-to-end.

## Grading

`binary`, `min_reward=1.0` per step. See `audit/` for the oracle (green) / baseline (empty, 0.0) and the gated sanity checks. Greenfield: empty `/app` at start, `solve.sh` writes to `${APP_DIR:-/app}/dual_index`, offline (`environment/Dockerfile` bakes `pytest`, `pytest-xdist`, `hypothesis`, `click`), CLI-only via `python -m dual_index.cli`.

## What the grader catches

- Stable sharding via `md5` not `hash()` (would be nondeterministic across CLI subprocesses → cross-shard failures)
- Cross-shard cross-universe `rename bob→alice` with ordered locks (deadlock if locks taken in arbitrary order)
- Universe immutability / verify-before-write (thread's blob must not be silently re-parented)
- Dangling available-vs-not-available repair keeping `universe` unchanged
- Rollout shard-exactness / monotonic / hold-gating / pre-condition checks before `advance_rollout`

## Known gaps

- No WAL crash injection beyond the two provided `.hold`/pending tests; broader fault injection not in scope.
- No enforcement-time metrics beyond `verify`/`backfill`/`gc` counts.

## Repo

Greenfield `swebench-multi`, `workstream=swe_public_repo`, `category=new_library/algorithms_and_data_structures`. Tags include mandatory `swe-bench-long-horizon-track`.

