---
name: ye-prompt-review
description: Use when a plan, design, legal or commercial document, research deliverable, product decision, or other non-code work needs multi-perspective professional consultation or review. Prefer requesting-code-review for a code-only diff review.
---

# YE Prompt Review

Use one approved context baseline to obtain genuinely different professional
perspectives. Keep advice, substantive quality review, and proof of task
completion distinct.

## Choose the mode

| Observable state | Mode | Reviewers |
|---|---|---|
| The work is not complete; the user wants advice, options, critique, or decision support | Consultant | One fresh `gpt-6-astra` subagent at `high` |
| A work product or completion claim exists; the user wants it assessed | Review | Astra professional review and DeepSeek completion verification |

If both states are present, ask which outcome the user wants first. Do not infer
Review Mode merely because the word “review” appears.

## Freeze the context before dispatch

Read [references/review-contract.md](references/review-contract.md). Build its
neutral Context Brief from the conversation and authoritative artifacts.

Ask one concise question when a missing fact could materially change the
review boundary, roles, sources, success metrics, or verdict. Otherwise label a
reasonable inference. Present the Context Brief, proposed role panel, sources,
and deliverable in natural language and obtain approval before dispatch. When
the user explicitly pre-authorizes starting after the brief, presenting the
brief satisfies the pause; it does not remove the brief.

Select every role whose incentives, expertise, or exposure can materially
change the result. There is no default role count or maximum. State why each
role is relevant. Omit decorative roles.

## Consultant Mode

Dispatch a fresh `gpt-6-astra` subagent with `high` reasoning. Give it only the
approved neutral brief, approved role panel, sources, and professional output
contract. It acts as a multi-perspective consultant and returns advice,
trade-offs, risks, questions, and decision criteria.

**DeepSeek is not part of Consultant Mode.** Do not add it as a second pass,
verifier, confidence check, or optional enhancement.

## Review Mode

Prepare both inputs before starting either track. Then:

1. Start a fresh `gpt-6-astra` subagent at `high` with the neutral brief, role
   panel, target artifacts, sources, and professional output contract.
2. Without waiting for Astra, invoke `deepseek-indep-review` with the same
   approved task baseline projected into its evidence bundle.
3. Do not give either track the other's role-specific prompt, analysis,
   findings, summary, or verdict.

Astra judges substantive quality from all approved perspectives. DeepSeek judges
whether the **underlying task** was actually executed, fully completed, and
aligned with the approved baseline. It does not audit whether this Skill ran
correctly. Preserve both verdicts even when they disagree.

## Result and mutation boundary

Render the professional verdict, DeepSeek verdict, agreements, differences,
required changes, and advisory improvements as separate sections. Review is
read-only by default. Findings do not authorize edits, implementation,
signing, sending, publishing, or other mutations.

For legal, medical, financial, security, or similarly high-stakes work, require
the material jurisdiction/date/factual context and current authoritative
sources before a definitive verdict. Identify the review as issue-spotting and
decision support, not a substitute for a licensed professional.

## Red flags

- DeepSeek appears anywhere in Consultant Mode.
- Review Mode waits for one verdict before starting the other.
- A reviewer sees the other reviewer's output before locking its verdict.
- One or two default roles replace materially relevant perspectives.
- A material unknown is silently converted into a scope-changing assumption.
- Professional recommendations are reported as proof that execution occurred.
- A completion pass is reported as proof of professional quality.

