With the dual-format index, per-universe sharding, atomic cross-universe operations, and bulk backfill plus dangling GC now handling the user blob store, the final piece is the phased rollout verifier — ensuring the migration can be rolled out gradually and verified end-to-end before being considered complete.

Please extend the model under /app/dual_index. All prior layers stay as-is (dual_index/encoding.py, dual_index/shard.py, dual_index/store.py, dual_index/atomic.py, dual_index/user_store.py, dual_index/backfill.py, and the init/write/read/link/unlink/rename/backfill/gc/verify commands). Hidden tests will import the exact new path below via PYTHONPATH=/app.

| Module | Key symbols | Purpose |
|---|---|---|
| dual_index/rollout.py | rollout_status, rollout_verify, advance_rollout | Phased rollout state and end-to-end verification |
| dual_index/cli.py (extended) | rollout, rollout-verify commands | Command-line entry points for rollout |

Rollout API — dual_index/rollout.py (all file-backed at `<base_dir>/rollout.json`)

| Function | Signature | Behavior |
|---|---|---|
| rollout_status | `rollout_status(base_dir)` | Reads the rollout state file at `<base_dir>/rollout.json` and returns a dict `{"phase": "not_started"|"canary"|"partial"|"full", "shards_migrated": [...], "total_shards": N, "verified": bool}`. If no state file exists, returns `{"phase": "not_started", "shards_migrated": [], "total_shards": N, "verified": False}`. The file, when present, is JSON like `{"phase": "canary", "shards_migrated": [0], "total_shards": 8}`. |
| advance_rollout | `advance_rollout(base_dir, num_shards, target_phase)` | Advances the rollout to the target phase by atomically updating `<base_dir>/rollout.json`. **Exact shard lists (must match):** `canary` is exactly `[0]` (1 shard), `partial` is exactly `[0, 1, ..., N//2 -1]` (floor half, e.g., N=8 → `[0,1,2,3]`), `full` is exactly `[0, 1, ..., N-1]` (all shards). Each advance **must** first verify the entire store is consistent before moving — call `verify(base_dir, num_shards)` and require `verify["inconsistent"]==0`, then `backfill(base_dir, num_shards)` and require `needs_backfill==0`, then `gc_dangling(base_dir, num_shards)` and require `dangling_found==0`. If any check fails, raise `ValueError` / return error without modifying `rollout.json`. Must also respect the per-username global lock at `<base_dir>/.lock_<username>` and the hold marker `<base_dir>/.hold_<username>` — if any username that hashes via `hashlib.md5(username.encode()).hexdigest() % num_shards` into a **target shard** is locked, the advance must fail without modifying `rollout.json` and raise `ValueError`. Phases are ordered `not_started < canary < partial < full`, and advancing must be **monotonic** (cannot go backwards or skip, e.g., `not_started → partial` must fail). Returns the new status dict on success, e.g., `{"phase": "canary", "shards_migrated": [0], "total_shards": 8, "verified": True}`. |
| rollout_verify | `rollout_verify(base_dir, num_shards)` | End-to-end verification of the entire migration: runs `verify` across all shards and universes and blobs, checks that `backfill` shows `needs_backfill==0`, that `gc_dangling` shows `dangling_found==0`, and that `rollout_status` is at least `partial` with all `shards_migrated` verified. Returns a dict `{"overall": bool, "verify": {...}, "backfill": {...}, "gc": {...}, "rollout": {...}}`. `overall` is `True` only if `verify["inconsistent"]==0` and `backfill["needs_backfill"]==0` and `gc["dangling_found"]==0` and `rollout["phase"]` is at least `partial` with all `shards_migrated` verified. Does not modify data. |

Storage detail — keep it simple and deterministic: the rollout state is a JSON file at base_dir/rollout.json with phase and shards_migrated list. Canary is exactly [0], partial is exactly [0,1,...,N//2-1], full is [0,...,N-1]. Advancing is monotonic not_started → canary → partial → full. All operations are file-backed, deterministic, and offline, and advance_rollout must verify the entire store is consistent before moving and must fail without modifying if any username in the target shards is locked.

CLI — new commands on python -m dual_index.cli

| Command | Options | Behavior |
|---|---|---|
| rollout | --phase canary\|partial\|full --base-dir PATH (default ./data) | Advances the rollout to the target phase via advance_rollout and prints the new status as JSON. Exits non-zero if the target shards are not verified or are locked, or if the phase would go backwards. |
| rollout-verify | --base-dir PATH (default ./data) --output json | Runs rollout_verify and prints the overall verification result as JSON. |

All prior commands keep working as before. You should be able to run python -m dual_index.cli init --shards 8 --format single --base-dir ./data, then add users, link some, run backfill and gc, then python -m dual_index.cli rollout --phase canary --base-dir ./data and check that rollout.json contains [0], then python -m dual_index.cli rollout --phase partial --base-dir ./data and check [0,1,2,3], then python -m dual_index.cli rollout --phase full --base-dir ./data and check [0..7], and finally python -m dual_index.cli rollout-verify --base-dir ./data --output json showing overall true.

Keep everything file-backed, deterministic, and offline. Hidden tests for this step will exercise rollout_status, advance_rollout, and rollout_verify through both the Python API and the command-line tool, including advancing through phases in order with exact shard lists, verifying that a canary cannot advance if its shard is inconsistent or locked (via .hold_<user>), and that rollout_verify reflects the true end-to-end state, not via direct file edits.
