---
generated: 2026-08-19T13-49-21Z
host: devvm16851.scu0.facebook.com
env: devserver
user: jmaassen
tool: lh-audit v1
---

# Audit — codimango/dual-index-migrator

Provenance: greenfield, 4 steps, binary, 1P (IDENTITY_1P_META Muse Spark 1.2).

## Checklist

| Gate | Status | Evidence | Report |
|------|--------|----------|--------|
| 1. Packaged & runs (sanity + oracle) | **PASS** | oracle 2026-08-18__13-23-22__294604 Mean 1.00 Solved 1/1, per-step 1.0×4 (F2P 45+24+20+10=99), baseline 0.0, 23/23 checks PASS | [sanity 2026-08-18T20-24-48Z](sanity/2026-08-18T20-24-48Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 2. Task quality (review) | **PASS** | no Critical/High; multi-turn genuine chain, spec↔test aligned | [review 2026-08-18T20-10-58Z](review/2026-08-18T20-10-58Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 3. Grader strength (coverage) | **PASS** | pytest+coverage baked, staged 45/24/20/10 green; harness empty-set gate + parser contract | [coverage 2026-08-18T20-10-58Z](coverage/2026-08-18T20-10-58Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 4. Empirical difficulty (rollouts) | **PASS** | metacode 0/3, claude-code 0/3, oracle 1/1 → genuine hard (not trivial, not infra/packaging) | [rollouts 2026-08-19T13-49-11Z](rollouts/2026-08-19T13-49-11Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 4b. Token / context cost | **PASS** | metacode 2.27M input / 26k output (3 trials avg 757k/trial), no overflow | [tokens 2026-08-19T13-49-11Z](tokens/2026-08-19T13-49-11Z__devvm16851.scu0.facebook.com__jmaassen.md) |
| 5. Each step gradable + has solution | **PASS** | all 4 steps solve.sh present, min_reward 1.0, fail_to_pass 45/24/20/10, oracle cleared | sanity per-step table |
| 6. Provenance links | **WARN (advisory)** | 1P branch, no laundering | — |
| 7. Human writeup | **PASS** | README.md with rationale, what grader catches, known gaps | README.md |

## Per-step gradability

| Step | solve.sh | min_reward | fail_to_pass | Oracle reward | Verdict |
|------|----------|------------|--------------|---------------|---------|
| 1_dual_format_index | present, ${APP_DIR:-/app}/dual_index | 1.0 | 45 | 1.0 | PASS |
| 2_atomic_changeset | present | 1.0 | 24 | 1.0 | PASS |
| 3_backfill_gc | present | 1.0 | 20 | 1.0 | PASS |
| 4_rollout_verifier | present | 1.0 | 10 | 1.0 | PASS |

## Per-agent rollout detail

| Agent | Job | Trials | Mean | Solved |
|-------|-----|--------|------|--------|
| metacode avocado-flex | 2026-08-19__06-33-55__06836b | 3 | 0.00 | 0/3 |
| claude-code opus | 2026-08-19__06-45-12__1b0107 | 3 | 0.00 | 0/3 |
| oracle | 2026-08-18__13-23-22__294604 | 1 | 1.00 | 1/1 |

Classification Gates A/B/C: **genuine hard** — oracle proves solvable, SotA 0/6 rules out trivial / infra defect / packaging error.

## Verdict

**READY TO SUBMIT**

All blocking gates PASS. Rollouts measured (hard, not too easy). No further code fixes required.

