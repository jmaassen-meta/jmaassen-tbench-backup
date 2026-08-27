# lh-coverage — dual-index-migrator — MEASURED 78.6% FAIL (threshold 90%) — after subprocess wrap + 4 targeted tests, CLI 72% measured; remaining gaps atomic 74% / backfill 78%

**Golden passes first:** yes — 114 passed in 4.74s via `coverage run -m pytest steps/` with PYTHONPATH=/tmp/app_test_cov (solution at /tmp/app_test_cov from staged solve.sh for 4 steps, DOCKERFILE base python:3.12-slim, host-local fallback). LCOV validated: 9 files, 1217 lines, 956 covered — `validate.py` OK. With wrap: 30+ .coverage.* files combined (was 1).

## 1. Code coverage — whole-solution scope (no added-lines diff yet, greenfield 4 steps cumulative)

| step · scope | line% | br% | cov/scope | verdict |
|---|---|---|---|---|
| whole-solution (956/1217) | 78.6% | 66.9% | 956/1217 | **FAIL** (+26 lines vs 76.4% before) vs 90% |
| dual_index/atomic.py | 74.3% | 59.2% | 281/378 | gap (+18 lines from rename to_hold + crash with to snapshot) |
| dual_index/backfill.py | 78.2% | 72.1% | 201/257 | gap (+8 lines from orphan threads removal + both-lock busy) |
| dual_index/cli.py | 72.3% | 51.7% | 193/267 | **MEASURED** with wrap — COVERAGE_PROCESS_START+user-site pth+parallel (30+ .coverage.* files, was 1) now covers CLI subprocesses; plateau closed honestly |
| dual_index/encoding.py | 88.1% | 81.6% | 52/59 | near threshold |
| dual_index/rollout.py | 91.7% | 82.3% | 110/120 | near PASS |
| dual_index/shard.py | 85.1% | 58.3% | 40/47 | gap |
| dual_index/store.py | 95.5% | 75.0% | 21/22 | PASS |
| dual_index/user_store.py | 86.6% | 73.1% | 58/67 | near threshold |

**Ranked uncovered lines (biggest genuine gaps, not the CLI plateau):**
- `atomic.py:165-200` — crash recovery WAL branches for `old_from_*`/`old_to_*` rename restore (only happy-path link is covered)
- `atomic.py:296-322` — `rename` ordered-lock second-lock hold-check and `read` returning partial vs raising `ValueError`
- `backfill.py:134-160` — `gc_dangling` repair `available` branch (move index to blob vs fix blob) and `threads without IG` dangling removal
- `backfill.py:172-186` — `backfill` `errors++` skip for `.lock`/`.hold` (busy rows)

**Note:** `cli.py 0%` is the expected **black-box plateau** per Backend Playbook §5 — internals aren't reachable from the external interface (`python -m dual_index.cli` is the CLI). The task's `instruction.md` does pin the CLI contract (init/write/read/link/rename/backfill/rollout), so `py_httpblackbox_cov.sh` (subprocess hook + sigterm flush) would be the faithful backend to cover it. Host-local `coverage run -m pytest` legitimately shows 0% for CLI — **do NOT close the plateau by adding white-box `import dual_index.cli` tests** that couple to internal layout; an honest plateau beats a coupled grader (sank capable agents 0/60 on layout mismatch).

## 2. Instruction ↔ grader (spec completeness + assertion quality)

Per-step `N/M requirements asserted` (from `test_inventory.py` + manual instruction read):

- **1_dual_format_index:** 5/5 asserted — encode_single/decode, encode_dual, ShardStore stable hash, DualIndex, CLI init/write/read. All behaviors pinned.
- **2_atomic_changeset:** ~28/30 asserted — link/unlink/rename atomic with WAL same-id, sorted([from,to]) ordered lock, file-backed `.lock`, crash recovery, hold marker. **Gaps:** `[COVERED-BUT-UNASSERTED]` `read` must not return partial write while locked (covered lines 65-68 but no assert pins the exact return `None` vs stale) → reward-hack: agent could return stale and still pass partial; `[UNTESTED]` `rename` with `from==to` must fail even when source exists (now asserted via test_rename_same_user_fails, but earlier gap).
- **3_backfill_gc:** ~35/40 asserted — UserStore universe immutable, backfill idempotent, gc available vs not-available repair. **Gaps:** `gc_dangling` `available` branch move-index vs fix-blob — covered 75% but weakest assertions are loose `inconsistent_users` check without pinning shard file deletion.
- **4_rollout_verifier:** 17/19 asserted — rollout_status, advance_rollout exact shard lists, monotonic, lock-first before verify/backfill/gc, rollout_verify overall. **Gaps:** `advance_rollout` lock check via `.hold_*` scan with `md5%N` before consistency — covered but one path `[UNTESTED]` where `partial` skips lock and passes via verify alone (would not be caught if verify already clean).

No **contradiction** (test requires opposite of instruction) found.

## 3. Regression & anti-cheat

- **Regression:** `regression_check.py` — no `pass_to_pass` entries in `steps/*/tests/config.json` (all `pass_to_pass: []`). Steps are accumulative (1→2→3→4) but later steps do not re-assert earlier formats. Risk: step 3 could break shard determinism and still pass. **Medium** — add `pass_to_pass` for encoding/shard contract in steps 2-4, or document that cumulative `solve.sh` re-applies all prior files under `audit/`.
- **Anti-cheat:** No obvious gameable grader — `fail_to_pass` lists 45/33/26/10 all must hold 100% baseline 0.0, no `pass_to_pass` credit, harness re-applies held-out tests at grade time, no `reward.json` self-report (test.sh v3 writes from python eval). No `touch` cheat vector beyond missing `workdir/setup.sh` already fixed (`rm -rf /tests` per step).

**Cache:** Host-local draft backend — no shipped recipe for `python` greenfield pytest shape (`py_pytest_cov.sh` missing), so D1 is `MEASURED` via host-local `coverage run -m pytest` with `COVERAGE_FILE=/tmp/cov_out/.coverage` + `source=/tmp/app_test_cov`. For container-faithful measurement, write `audit/coverage/backend/py_greenfield_pytest_cov.sh` per playbook (wrap `python -m dual_index.cli` subprocess via `COVERAGE_PROCESS_START` + `sitecustomize`).

