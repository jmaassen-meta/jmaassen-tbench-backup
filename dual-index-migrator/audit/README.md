---
generated: 2026-08-18T20-24-56Z
host: devvm16851.scu0.facebook.com
env: devserver
user: jmaassen
tool: lh-audit v1
---

# Audit — codimango/dual-index-migrator

Provenance: greenfield, 4 steps, binary, 1P (IDENTITY_1P_META).

## Checklist

| Gate | Status | Evidence | Report |
|------|--------|----------|--------|
| 1. Packaged & runs (sanity + oracle) | **PASS** | oracle 2026-08-18__13-23-22__294604 Mean 1.00 Solved 1/1, per-step 1.0/1.0/1.0/1.0 (F2P 45+24+20+10=99), baseline 0.0, 23/23 checks PASS, solve.sh exit 0 | [sanity 2026-08-18T20-24-48Z](sanity/2026-08-18T20-24-48Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 2. Task quality (review) | **PASS** | no Critical/High; multi-turn genuine chain, spec↔test aligned | [review 2026-08-18T20-10-58Z](review/2026-08-18T20-10-58Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 3. Grader strength (coverage) | **PASS** | pytest + coverage baked (Dockerfile), staged runs 45/24/20/10 green; numeric --cov available inside container | [coverage 2026-08-18T20-10-58Z](coverage/2026-08-18T20-10-58Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 4. Empirical difficulty (rollouts) | **MISSING (deferred — spends $)** | no avocado/opus/gpt jobs yet — run `codimango bench run -p jmaassen-tbench/dual-index-migrator -a avocado --n 3` when ready to settle Gates A/B/C | — |
| 4b. Token / context cost | **MISSING (deferred)** | reuses same rollout jobs via `lh-count-tokens` | — |
| 5. Each step gradable + has solution | **PASS** | all 4 steps non-empty solve.sh, min_reward 1.0, fail_to_pass 45/24/20/10, oracle cleared | sanity per-step table |
| 6. Provenance links | **WARN (advisory)** | 1P branch Muse Spark 1.2, no laundering | — |
| 7. Human writeup | **PASS** | README.md with rationale, what grader catches, known gaps | README.md |

## Per-step gradability

| Step | solve.sh | min_reward | fail_to_pass | Oracle reward | Verdict |
|------|----------|------------|--------------|---------------|---------|
| 1_dual_format_index | present, ${APP_DIR:-/app}/dual_index | 1.0 | 45 | 1.0 | PASS |
| 2_atomic_changeset | present | 1.0 | 24 | 1.0 | PASS |
| 3_backfill_gc | present | 1.0 | 20 | 1.0 | PASS |
| 4_rollout_verifier | present | 1.0 | 10 | 1.0 | PASS |

## Summary

Fixable gates cleared: top-level `tests/config.json` substitute, Dockerfile now bakes `coverage`, README writeup, per-step staging all green, oracle 1.00. Rollouts/tokens are the only remaining blocker and are intentionally deferred pending spend approval — audit will flip to **READY TO SUBMIT** once rollouts show sane solve rates.

## Verdict

**BLOCKED** — pending Gates 4/4b rollouts (cost-bearing). All fixable-before-push gates are PASS.

### Next actions (when ready to spend)

- `codimango bench run -p jmaassen-tbench/dual-index-migrator -a avocado --n 3` (+ opus/gpt if budget)
- `lh-count-tokens jmaassen-tbench/dual-index-migrator`
- Re-run `lh-audit --auto`

