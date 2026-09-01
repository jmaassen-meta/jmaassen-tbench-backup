Now that the single and dual record formats and sharded storage are in place, let's add atomic cross-universe and cross-shard operations - the part that's tricky when a single username has to update two places at once. In this design the username index is split by universe: IG and Threads each have their own sharded store, so even one logical username touches two physical shard trees.

Extend the model under /app/dual_index - you're building on the encoding/shard/store you already have. The foundation from the previous step stays as-is (dual_index/encoding.py, dual_index/shard.py, dual_index/store.py, and the init/write/read commands). Hidden tests will import the exact new paths below via PYTHONPATH=/app.

| Module | Key symbols | Purpose |
|---|---|---|
| dual_index/atomic.py | AtomicIndex | Cross-universe, cross-shard atomic operations with per-row global locking |
| dual_index/cli.py (extended) | link, unlink, rename commands | Command-line entry points for atomic operations |

Add per-universe, cross-shard atomic operations in `dual_index/atomic.py` with an `AtomicIndex(base_dir, num_shards)` that maintains two separate sharded stores (`<base_dir>/ig` and `<base_dir>/threads`) under `base_dir`, a durable write-ahead log at `<base_dir>/wal.jsonl`, and per-username file-backed locks observable across processes as `<base_dir>/.lock_<username>`. Implement `link` and `read` with all-or-nothing semantics: `link` writes are journaled before being applied and committed after; a crash mid-`link` must auto-recover to the pre-operation state, release locks, and raise `ValueError` on failure; `read` never returns a partial write and silently reconciles a pending journal entry before returning committed state without raising, so the next mutation sees a clean state. `unlink` and `rename` are also atomic and respect the same lock/journal guarantees, but hidden tests for this step focus primarily on `link`/`read` atomicity and `rename` deadlock avoidance; if `unlink`/`rename` are implemented with the same WAL/lock pattern they will pass, but the core grading for this step is `link`/`read`. `rename` moves a username across both universes and shards only when the destination is free in both, to reject same-name renames, and to acquire the two locks in sorted global order (`sorted([from_user, to_user])`) to avoid deadlock. If a hold marker `<base_dir>/.hold_<username>` exists for any affected username, treat it as a held lock and the operation fails with `ValueError` without creating a `.lock_` or WAL entry and leaves no stale lock.

File-backed layout - this is how the durable state looks on disk (hidden tests inspect the exact on-disk artifacts below as one valid file-backed contract; matching this contract is expected to guarantee a pass):

- **Journal:** a durable append-only log at `<base_dir>/wal.jsonl` (one JSON object per line). For `link`/`unlink`/`rename` each operation appends an `intent` before mutating shards and a `commit` after, sharing the same UUID `id` carrying `old_*` snapshots of the records before the operation (or `null` if absent). After success there should be no pending `intent` without a matching `commit` for completed operations.

- **Locks:** per-username file-backed locks observable across processes as `<base_dir>/.lock_<username>`, created exclusively at operation start (after checking no hold exists), held for the whole operation, and removed on success and on recovery. No stale lock should remain after success.

- **Hold markers:** an externally created hold marker `<base_dir>/.hold_<username>` simulates cross-process contention. If any affected username is held, the operation fails with `ValueError` without creating a lock or journal entry and leaves no stale lock.

- **Shard record shapes:** IG shards store the full dual record as produced by `encode_dual`/`encode_single`; Threads shards store the same values but with the Threads uid under the key `uid` (not `threads_uid`). Preserve this shape so the two universes remain distinct.

Extend the CLI with `link`/`unlink`/`rename` subcommands and update `init` to create the per-universe trees and an empty journal if desired. Keep everything file-backed, deterministic, and offline. Hidden tests check this via the Python API and CLI.

Atomic API - `dual_index/atomic.py`

| Class / Method | Signature | Behavior |
|---|---|---|
| AtomicIndex | `AtomicIndex(base_dir, num_shards)` | Wraps two sharded stores (IG and Threads) under `base_dir` with crash-safe atomicity and per-username serialization. Any username being operated on is serialized across both universes until the operation is complete - either successfully committed or fully recovered - so the next operation sees a clean state. No explicit recover command is needed; recovery is automatic on next access. |
| link | `link(username, ig_uid, threads_uid)` | Atomically links a username: creates or updates the dual state by writing the username to both IG and Threads shards in one all-or-nothing operation. Must serialize on that username across both universes for the entire operation. On crash, it auto-recovers both universes, release locks, and raise `ValueError`, leaving both stores in pre-operation state (tests check `pytest.raises(ValueError)` after a pending WAL entry). |
| unlink | `unlink(username)` | Atomically unlinks a username: removes the Threads entry and updates the IG entry to unlinked. Is expected to serialize across both universes with same auto-recovery. Raises `ValueError` if absent or already unlinked. |
| rename | `rename(from_user, to_user)` | Atomically moves a username across both universes and shards only if destination is free in both, preserving `ig_uid`, `threads_uid`, `link_state`, `format` and updating `username` to `to_user`. Must serialize on both usernames in sorted global order to avoid deadlock and fail with `ValueError` if `from_user == to_user` or `from_user` absent or `to_user` taken. On crash, it auto-recovers both universes, release locks, and raise `ValueError` (same as `link` crash recovery). |
| read | `read(username)` | Returns the decoded record or `None`, reflecting only committed state across both universes. Respects ongoing serialization and silently recovers any pending journal entry before returning committed value without raising, so the next mutation sees a clean state. |

CLI - new commands on `python -m dual_index.cli` (all must be added to the same `cli.py` and work via `python -m dual_index.cli <command> --help`)

| Command | Options | Behavior |
|---|---|---|
| link | `--user NAME --ig-uid A --threads-uid B --base-dir PATH` (default `./data`) | Atomically links via `AtomicIndex` link. Serializes on that user across both stores; on crash auto-recovers and exits non-zero. |
| unlink | `--user NAME --base-dir PATH` | Atomically unlinks across both universes, same serialization and recovery, non-zero on failure. |
| rename | `--from USER --to USER --base-dir PATH` | Atomically renames across both universes and shards only if destination free. Serializes on both usernames in deterministic order; on crash auto-recovers and exits non-zero. |

Existing `init`, `write`, and `read` commands keep working as before, but `init` should now create the per-universe sharded trees and record shards and format. The new operations should be usable like `python -m dual_index.cli link --user alice --ig-uid 100 --threads-uid 200 --base-dir ./data` then `python -m dual_index.cli read --user alice --base-dir ./data --output json` showing a linked dual record, and `python -m dual_index.cli rename --from bob --to alice --base-dir ./data` only succeeding when alice is free in both universes.

Keep everything deterministic and offline. Hidden tests for this step will exercise `link`, `unlink`, and `rename` through both the Python API and the command-line tool, including simulated crashes and concurrent attempts on the same usernames (including opposite-order renames) to verify atomicity, crash recovery, and serialization without via direct file edits.
