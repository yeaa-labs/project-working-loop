---
name: deepseek-indep-review
description: Use when high-confidence large or consequential work—roughly more than ten minutes, a meaningful feature, refactor, migration, or multi-file change, a substantial decision-grade plan/specification/report, or a consequential external mutation—requires independent DeepSeek completion verification. Skip routine read-only scans, status checks, handoffs, short plans or drafts, small edits, bounded lookups, ordinary explanation, and routine multi-step work; when uncertain, do not invoke.
---

# DeepSeek Independent Review

Prevent three false-completion modes: no execution, partial execution, and work
that does not match the approved request.

## User-facing language

This is completion verification, not quality review.

- Call it `DeepSeek completion verification` or `DeepSeek 完成验收`.
- Never call it a quality gate, quality review, `质量门槛`, or `质量审查`.
- Before `verified_complete`, never claim the task is complete. Say:
  `候选交付物和证据已整理，正在进行 DeepSeek 完成验收；验收通过前不宣称完成。`
- Verify only the approved baseline. Even when it contains quality requirements,
  do not invent an independent quality standard.

## Hard gates

For a substantial action:

1. Present the plan before execution.
2. Wait for explicit approval by default.
3. Gather fresh local evidence after execution.
4. Obtain a locally valid DeepSeek `verified_complete` verdict.
5. Only then claim completion.

The user may pre-authorize execution after the plan. This removes the second
pause, not the plan, permission checks, evidence collection, or DeepSeek audit.

Never convert `rework_required`, `verification_blocked`, an API error, or the
absence of evidence into a success claim.

## Decide whether it applies

Invoke automatically only when there is high confidence that at least one of
these is true: the work is expected to take roughly more than ten minutes; it
is a meaningful feature, refactor, migration, or multi-file change; it creates
a substantial decision-grade plan, specification, report, or other major
deliverable; or it performs a consequential external mutation. Explicit user invocation always applies.

Multiple steps, an output file, a lookup, or an external source do not by
themselves make work substantial. Do not invoke for routine read-only scans,
status checks, handoffs, short plans or drafts, small edits, bounded lookups,
ordinary explanation, or routine multi-step work. When uncertain, do not
invoke. Ask one concise question only when the classification would materially
change execution; otherwise continue without this Skill.

`ye-prompt-review` Consultant Mode is also excluded: it is an explicitly
Sol-only advisory workflow, even when the consultation is substantial. This
Skill verifies completion and alignment against an approved baseline; it is
not a general quality gate.

## Invocation from ye-prompt-review Review Mode

When `ye-prompt-review` invokes this Skill as its DeepSeek track, the user has
already approved one neutral Context Brief for the underlying task. Treat that
approved brief as the verification baseline and start without waiting for the
parallel Sol professional reviewer.

- Do not present a duplicate plan or request a second approval solely for the
  DeepSeek track. Stop only if the approved brief lacks a material requirement,
  success criterion, expected-work mapping, artifact boundary, or permission
  needed for a valid audit.
- Audit the underlying task: whether it was actually executed, fully completed,
  and aligned with the approved baseline. Do not audit whether
  `ye-prompt-review`, Sol, or the synthesis process ran correctly.
- Remain blind to Sol's role prompt, analysis, findings, recommendations, and
  verdict. Those are neither evidence nor requirements.
- Project the approved neutral baseline into the existing evidence bundle as
  specified in the auditor contract. A reconstructed expected-work baseline is
  acceptable only when it is labeled and the user approved it; it does not
  prove what an executor historically planned.

The Context Brief's approval authorizes the audit, not new execution. Corrections
still follow the three-round and scope rules below. Sol advisory improvements
never become DeepSeek requirements unless the user approves a new baseline.

## Plan contract

Before execution, present a short, proportional plan in natural language:

- state the goal and interpretation;
- list only the requirements needed to define success; requirement labels such
  as `R1` and `R2` are optional when they materially improve clarity;
- use an ordinary numbered list (`1`, `2`, `3`) for execution steps, never
  user-facing `P1`, `P2`, or similar protocol labels;
- make each step state its expected output or observable completion check, so a
  separate repetitive evidence list is unnecessary;
- state the deliverable or final completion standard; and
- include scope boundaries, meaningful risks, and external side effects only
  when they matter to the decision.

Keep the plan compact: normally two to five execution steps. Do not split one
idea into separate requirement, procedure, evidence, and verification bullets
when a single clear step can carry that information.

Before presenting an approval-ready plan, ask the user when a requirement,
success criterion, or scope boundary is uncertain and the answer could
materially change the work. Ask one concise clarification at a time. Do not put
a guessed default into the plan and merely invite correction when that answer
would change the scope or outcome. If an ambiguity is immaterial, state the
reasonable assumption in the plan instead. Clear requirements do not need
redundant confirmation.

User-facing numbering is presentation, not the audit schema. After approval,
preserve an internal traceability map with stable `R/P/A/E/C/V` identifiers for
the evidence bundle. Do not expose those internal IDs in the initial plan merely
to make the later audit easier.

Stop at `approval_pending` unless the user already authorized execution after
the plan. User corrections create a new internal plan version (`V1`, `V2`,
...). The latest explicitly approved version is the only verification baseline.

## Execute and collect evidence

After approval, preserve the approved plan in a task-specific temporary file so
it is not reconstructed at the end. Execute only that scope.

Run the existing `verification-before-completion` workflow first. Build a JSON
evidence bundle with stable `R/P/A/E/C/V` identifiers. Statements such as
"implemented successfully" are claims, not evidence. Prefer diffs, hashes,
fresh test output and exit codes, authoritative read-backs, receipts, artifact
excerpts, and cited research sources.

Before describing the verification scope to the user, assembling a bundle,
calling DeepSeek, or interpreting a verdict, read
[references/auditor-contract.md](references/auditor-contract.md).

## Invoke DeepSeek

Resolve this Skill's installed directory, then run:

The API requires network access. In Codex, run the first verifier attempt with
`sandbox_permissions=require_escalated`; do not first run a command known to
fail in the DNS-restricted sandbox. Existing transmission authorization does
not waive the runtime's execution-permission check. If network access is
denied, return `verification_blocked` without a sandbox retry loop.

```bash
python3 <skill-directory>/scripts/deepseek_verify.py \
  --bundle <task-evidence-bundle.json> \
  --output <task-verdict.json>
```

The verifier reads `DEEPSEEK_API_KEY` from the process environment, falling
back to `~/.codex/secrets/deepseek.env`. Never print or copy the key into the
bundle.

Read the complete verdict file. A zero exit code is necessary but not
sufficient: confirm `verdict=verified_complete` and
`claim_complete_allowed=true`.

## Correction rounds

Round 1 is the initial execution and audit. Rounds 2 and 3 are corrections.
Approval of the original plan authorizes corrections that stay inside its
scope. Before round 2 or 3, show the user:

- every prior DeepSeek comment;
- why the prior work failed;
- expected versus observed behavior and cited evidence;
- whether each older comment is fixed, still open, or regressed;
- the next correction plan.

Then correct every open blocking item and re-run fresh local verification and
DeepSeek. New scope, destructive work, consequential new external actions, or a
new user decision requires fresh approval.

In `ye-prompt-review` Review Mode, DeepSeek comments belong to the underlying
task and its executor. Keep them separate from Sol professional findings. An
unresolved non-blocking DeepSeek comment is disclosed but does not override a
locally valid `verified_complete` verdict; every blocking comment must be fixed
for completion verification to pass.

After three non-passing business rounds, stop and report
`failed_after_three_rounds` with all open comments. Transport retries performed
inside the verifier do not consume a business round.

## Result language

- `verified_complete`: completion may be claimed with evidence and audit model.
- `rework_required`: work is incomplete; disclose comments and continue only
  within the three-round policy.
- `verification_blocked`: execution may have occurred, but completion is not
  verified; disclose the blocker and do not claim completion.
