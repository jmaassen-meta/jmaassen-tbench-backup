# HARD RULE — instruction.md and the graded tests are 1P-only

## First: resolve which model you are. Do not assume.

Run this verbatim and branch on the result — do not infer your identity from this file's wording:

    if [ "${META_3PAI_ACTIVE_PROVIDER:-}" = "meta" ]; then echo IDENTITY_1P_META; elif [ "${AAI_LH_1P:-}" = "1" ]; then echo IDENTITY_1P_OVERRIDE; else echo "IDENTITY_NOT_PROVEN=[provider=${META_3PAI_ACTIVE_PROVIDER:-unset},platform=${META_3PAI_AGENT_PLATFORM:-unset},model=${META_3PAI_ACTIVE_MODEL:-unset}]"; fi

- `IDENTITY_1P_META` / `IDENTITY_1P_OVERRIDE` → **1P branch** (Avocado / Muse Spark). You MAY draft `instruction.md` and author the graded assertions; the human must review them.
- `IDENTITY_NOT_PROVEN=[...]` → **3P branch** (fail-closed; unknown counts as 3P). Everything in the next section is off-limits. If you believe you are 1P, the provider signal is missing — raise it with the human rather than concluding which model you are.

## What is off-limits on the 3P branch:

- All `steps/*/instruction.md` files — 1P (Avocado / Muse Spark) or human only.
  - Includes `steps/1_*/instruction.md`, `steps/2_*/instruction.md`, etc., and top-level `instruction.md` if it exists (multi-turn agent reads only per-step; top-level does NOT reach the agent).
- The graded **test assertions** under any `tests/` dir — `test_*.py`, `*_test.py`, `*_cli.py`.
  These are unit tests / grading rubrics → 1P-only per Model Role v2.
- Even if the user explicitly says "edit it", DO NOT. Flag the issue / hand off to 1P instead.

## What to do if instruction.md needs change (3P branch):

- DO NOT Edit, Write, or Bash rewrite it.
- Output ONLY:

```
ISSUE in steps/N_<name>/instruction.md:
  - Current says: <quote>
  - Tests require / Spec says / Rollout shows: <what's misaligned>
  - Impact: <oracle 0, rollout 0/5, etc>
  - Direction: <describe missing info, not exact text>
  - Owner: human/1P — please edit by hand or via Avocado
```

- Do NOT propose rewritten instruction.md text, even as a suggestion. Only describe the issue.

## What to do if a graded test needs change (3P branch):

- DO NOT write the assertion text yourself. Describe the gap (what behavior is under-tested / mis-tested) and hand authoring to a 1P model (Avocado / Muse Spark) or the human. You MAY still edit the harness/config so the new tests wire up.
- Do not launder: never paste 3P-written test text (or "here's exactly how to test it") into a 1P session.

## On the 1P branch:

- Draft `instruction.md` and author the graded assertions directly, then surface them for human review. Do NOT stall waiting for the human to hand-write what you are permitted to draft.
- Still off-limits: seeding the task idea from scratch (the seed is human), and retyping text a 3P session produced (laundering).

## What IS fine to edit on either branch:

- `README.md` (human writeup, rationale)
- `task.toml` (metadata, tags, taxonomy, timeouts, step dependencies)
- `solution/*` (solve.sh + files — must write to ${APP_DIR:-/app}, staged cumulative)
- Test **harness/runner/config** only: `tests/config.json`, `tests/test.sh`, `tests/run_script.sh`, `tests/parser.py`
- `environment/Dockerfile` (quote pip specs, cap majors, symlink /usr/bin/python3)
- `audit/*` (sanity/rollouts/review reports)

## Why:

- Codimango Provenance check: 3P tokens in instruction.md OR in the graded tests → FAILED → task blocked
- Hand-writing instructions flipped FAILED → passing in example-task
- See `references/provenance.md` and `references/cli-testing.md`

## Enforcement:

- Before any Edit/Write/Bash that touches a file, check the path: ends with `instruction.md`, or is a `tests/{test_*.py,*_test.py,*_cli.py}` assertion file → BLOCK and flag / hand off instead on 3P branch.
- This guard applies to all tools (Edit, Write, Bash cp/echo >/tee/sed -i, etc).
- In lh-flow skill, Phase 2, 3, 4, 5 — enforce via runtime check.
