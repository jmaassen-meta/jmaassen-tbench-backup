With the dual-format index, per-universe sharding, atomic cross-universe operations, and bulk backfill plus dangling GC now handling the user blob store, the final piece is the phased rollout verifier — ensuring the migration can be rolled out gradually and verified end-to-end before being considered complete.

Please extend the model under /app/dual_index. All prior layers stay as-is (dual_index/encoding.py, dual_index/shard.py, dual_index/store.py, dual_index/atomic.py, dual_index/user_store.py, dual_index/backfill.py, and the init/write/read/link/unlink/rename/backfill/gc/verify commands). Hidden tests will import the exact new path below via PYTHONPATH=/app.

| Module | Key symbols | Purpose |
|---|---|---|
| dual_index/rollout.py | rollout_status, rollout_verify, advance_rollout | Phased rollout state and end-to-end verification |
| dual_index/cli.py (extended) | rollout, rollout-verify commands | Command-line entry points for rollout |

Rollout API — `dual_index/rollout.py`

| Function | Signature | Behavior |
|---|---|---|
| rollout_status | `rollout_status(base_dir)` | Reads the rollout state under `base_dir` and returns a dict `{"phase": "not_started"\|"canary"\|"partial"\|"full", "shards_migrated": [...], "total_shards": N, "verified": bool}`. If no state exists, returns `{"phase": "not_started", "shards_migrated": [], "total_shards": N, "verified": False}`. |
| advance_rollout | `advance_rollout(base_dir, num_shards, target_phase)` | Advances the rollout to the target phase by atomically updating the rollout state. Phases are ordered `not_started < canary < partial < full` and advancing must be **monotonic and stepwise** — you may only move to the immediate next phase: `not_started → canary → partial → full`. Any other transition must fail with `ValueError` without modifying state (for example `not_started → partial` must fail, `canary → canary` must fail). On success returns the new status dict. Before advancing it must ensure no username that belongs to a target shard is externally locked (including a pre-created hold condition) and that the whole store is consistent (`verify`/`backfill`/`gc` all clean), and must not modify state on any failure. The per-phase shard coverage is `canary` covers 1 shard, `partial` covers floor(N/2) shards, and `full` covers all shards (for example `N=8` → canary `[0]`, partial `[0,1,2,3]`, full `[0..7]`; hidden tests check the API-returned `shards_migrated` lists). |
| rollout_verify | `rollout_verify(base_dir, num_shards)` | End-to-end verification of the entire migration: runs `verify` across all shards and universes and blobs, checks that `backfill` shows `needs_backfill==0` and `inconsistent==0` and `errors==0`, that `gc_dangling` shows `dangling_found==0`, and that `rollout_status` is at least `partial` with all `shards_migrated` verified and `verified` True. Returns a dict `{"overall": bool, "verify": {...}, "backfill": {...}, "gc": {...}, "rollout": {...}}`. `overall` is `True` only if `verify["inconsistent"]==0` and `backfill["needs_backfill"]==0` and `backfill["inconsistent"]==0` and `backfill["errors"]==0` and `gc["dangling_found"]==0` and `rollout["phase"]` is at least `partial` with all `shards_migrated` verified. Does not modify data. |

Storage detail — keep it simple and deterministic. The rollout state is persisted under `base_dir` (you choose the file representation consistent with the atomicity guarantee). Advancing is monotonic and stepwise `not_started → canary → partial → full` — you cannot skip or repeat or go backwards. To enforce, first read current status via `rollout_status(base_dir)` and compare to the allowed next phase; any other target must raise `ValueError` without touching state. All operations are deterministic and offline. `advance_rollout` must fail without modifying state if any username in the target shards is locked, and must check locks before consistency, then verify the entire store is consistent before writing.

CLI — new commands on `python -m dual_index.cli`

| Command | Options | Behavior |
|---|---|---|
| rollout | `--phase canary\|partial\|full --base-dir PATH` (default `./data`) | Advances the rollout to the target phase via `advance_rollout` and prints the new status as JSON. Exits non-zero if the target shards are not verified or are locked, or if the phase would go backwards. |
| rollout-verify | `--base-dir PATH` (default `./data`) `--output json` | Runs `rollout_verify` and prints the overall verification result as JSON. |

All prior commands keep working as before. You should be able to run `python -m dual_index.cli init --shards 8 --format single --base-dir ./data`, then add users, link some, run backfill and gc, then `python -m dual_index.cli rollout --phase canary --base-dir ./data` and check that rollout status shows 1 shard migrated, then partial shows half, then full shows all, and finally `python -m dual_index.cli rollout-verify --base-dir ./data --output json` showing `overall` true.

Keep everything deterministic and offline. Hidden tests for this step will exercise `rollout_status`, `advance_rollout`, and `rollout_verify` through both the Python API and the command-line tool, including advancing through phases in order and verifying that a canary cannot advance if its shard is inconsistent or locked.
