# YE Prompt Review Contract

Read this file whenever `ye-prompt-review` is invoked.

## 1. Neutral Context Brief

Create one versioned brief before selecting final prompts. It is neutral: it
contains the task and evidence, not the main agent's preferred conclusion or
either reviewer's findings.

```yaml
brief_version: V1
mode: consultant | review
review_target: what is being advised on or reviewed
decision_or_purpose: what the user will decide or do with the result
original_request: the user's request without reinterpretation
background:
  problem: relevant history and current state
  stakeholders: affected people or systems
approved_baseline:
  requirements: explicit approved outcomes
  success_metrics: observable success measures
  expectations: quality, timing, risk, and audience expectations
  plan_or_expected_work: approved plan, or a clearly labeled reconstructed baseline
scope:
  included: in-scope questions and artifacts
  excluded: out-of-scope work
  mutation_authority: read-only unless separately authorized
sources_of_truth: authoritative documents, data, laws, specifications, or decisions
artifacts_and_evidence: target files, diffs, outputs, receipts, tests, or research sources
facts: evidence-backed facts
inferences: labeled reasonable inferences
material_uncertainties: unknowns that may alter scope, roles, sources, or verdict
privacy_exclusions: credentials, secrets, local-secrets, and irrelevant sensitive data
user_approval: what the user approved and when
```

If historical work lacks an authoritative approved plan, reconstruct only the
minimum verification baseline from the original request, acceptance criteria,
and authoritative decisions. Label it as reconstructed and obtain user
approval. Do not present it as proof of what the executor historically planned.

### Approval presentation

Present a compact natural-language brief:

- what will be reviewed and for what decision;
- requirements, success metrics, and scope;
- sources and evidence;
- role panel with one reason per role;
- material assumptions or uncertainties;
- mode, reviewers, and output.

Ask one question at a time when a material field cannot be recovered. If the
brief is ready, ask for approval. A user instruction such as “after you show the
brief, proceed” pre-authorizes dispatch after presentation.

## 2. Dynamic role panel

Choose roles by material decision coverage, not by a fixed count.

1. Identify who owns the decision, who builds or operates the result, who uses
   it, who bears downside, and which specialists govern material constraints.
2. Add every role that can reveal a distinct failure, incentive, trade-off, or
   success condition.
3. Merge roles only when their review questions and incentives are materially
   identical for this task.
4. Remove a role if it cannot plausibly change a finding or recommendation.

For each role define:

```yaml
role: specific professional or stakeholder perspective
reason: why this role materially affects this task
mandate: questions, risks, and success conditions it owns
authoritative_sources: sources it should privilege
boundaries: claims it cannot make without further evidence
```

Typical roles include Founder, Executive, PM, Designer, User, Engineer,
Accessibility, Security, Privacy, Legal, Finance, Operations, GTM, Sales,
Support, and domain specialists. These are candidates, not a checklist.

Role wording may be adapted from the current public
[`f/prompts.chat`](https://github.com/f/prompts.chat) repository when it offers
a useful perspective. Treat community prompts as vocabulary and questioning
scaffolds only. The approved brief, evidence, permissions, and this output
contract remain authoritative; never copy a persona's invented background,
authority, style requirements, or hidden agenda into the review.

## 3. Astra professional-review input

Use a fresh subagent. Set model `gpt-6-astra` and reasoning effort `high`.
Provide:

- the approved neutral Context Brief;
- the approved role panel;
- the complete in-scope artifacts and sources, or a manifest with sufficient
  excerpts when size requires it;
- the professional result schema below.

Do not provide the executor's hidden reasoning, the main agent's preferred
answer, DeepSeek's prompt, or DeepSeek's output.

In Consultant Mode, the task is to advise before execution. Recommendations
must distinguish evidence, assumptions, options, trade-offs, and proposed
success/stop criteria. The reviewer must not claim that proposed work exists.

In Review Mode, the task is to assess the existing target from every approved
role. It must not infer completion merely from the executor's prose.

## 4. Professional result schema

The Astra subagent returns one structured object. Every field is required; arrays
may be empty only when the field genuinely has no items.

```json
{
  "brief_version": "V1",
  "mode": "consultant | review",
  "roles": [
    {
      "role": "Founder",
      "reason": "Why this role is material",
      "assessment": "pass | conditional_pass | reject | blocked",
      "findings": [
        {
          "finding_id": "F1",
          "severity": "blocking | major | minor",
          "classification": "required_change | advisory_improvement",
          "expected": "Approved baseline or professional expectation",
          "observed": "Evidence-backed observation",
          "evidence_refs": ["source or artifact reference"],
          "impact": "Why this matters from this role",
          "recommendation": "Specific response",
          "uncertainty": "Known limitation or none"
        }
      ]
    }
  ],
  "cross_role": {
    "agreements": ["shared conclusion"],
    "conflicts": [
      {
        "roles": ["Role A", "Role B"],
        "tradeoff": "the actual tension",
        "decision_needed": "who must decide what"
      }
    ]
  },
  "professional_verdict": "pass | conditional_pass | reject | blocked",
  "verdict_reason": "evidence-based task-level conclusion",
  "required_changes": ["changes needed to satisfy baseline or prevent blocking harm"],
  "advisory_improvements": ["valuable changes beyond the approved baseline"],
  "unresolved_questions": ["material unanswered question"]
}
```

Verdict rules:

- `pass`: no blocking or major required change remains.
- `conditional_pass`: no blocking issue, but one or more major required changes
  must be satisfied before reliance, release, signing, or the target decision.
- `reject`: at least one blocking substantive issue makes the target unsuitable.
- `blocked`: missing context, artifact, authority, or evidence prevents a
  responsible verdict.

A role may use stricter professional expectations than the approved baseline,
but findings outside that baseline are `advisory_improvement` unless they
prevent a safe or legally valid result.

## 5. Review Mode dual-track handoff

Create both inputs before dispatch so later output cannot influence either.

### Astra input

Neutral Context Brief + role panel + professional schema.

### DeepSeek input

Project the same neutral task baseline into the evidence bundle required by
`deepseek-indep-review`:

- `original_request` comes from the brief;
- `requirements` and `approved_plan` come from the approved baseline;
- `artifacts` and `evidence` come from the same in-scope manifest;
- `previous_comments` contains only prior DeepSeek completion comments.

Do not include the role panel, Astra prompt, Astra findings, Astra verdict, the main
agent's professional synthesis, or recommendations invented during review.
DeepSeek evaluates the underlying task, not whether Astra or this Skill followed
their process.

User approval of the Context Brief authorizes this DeepSeek verification when
project instructions already authorize the verifier and the bundle excludes
prohibited data. Do not request a redundant second approval solely because the
same approved baseline is being projected into the DeepSeek schema.

### Dispatch order

Start the Astra subagent, then immediately start the DeepSeek verifier without
waiting for Astra. If the runtime cannot execute both concurrently, freeze both
inputs first and preserve blindness; never pass the first result into the
second. Wait for both before synthesis.

## 6. User-facing synthesis

Render, in this order:

1. **Professional review** — roles, professional verdict, highest-impact
   findings, cross-role agreements/conflicts, required changes, and advisory
   improvements.
2. **DeepSeek completion verification** — its exact completion verdict,
   requirement coverage, evidence basis, and blocking comments.
3. **Relationship between verdicts** — what they agree on and why they differ.
4. **Decision and next authority** — what the user can rely on now and which
   proposed mutations require a new or existing approval.

Do not average the verdicts. Typical legitimate combinations include:

- DeepSeek passes; Astra rejects: approved work was completed, but professional
  review finds the approved result substantively weak or unsafe.
- DeepSeek fails; Astra likes the concept: the direction is promising, but the
  claimed work is absent, partial, or misaligned.
- Both pass: completion and substantive quality are independently supported.
- Either blocks: clearly state the missing evidence or context.

## 7. Corrections and high-stakes limits

The review itself is read-only. If the user requests changes, plan and obtain
authority under the normal execution workflow. DeepSeek's three-round policy
applies only to correcting the underlying task inside an already approved
scope; it does not authorize implementing Astra's new advisory ideas.

For legal work, obtain governing jurisdiction, relevant date, party posture,
governing documents, and current primary authority needed for the issues. For
other high-stakes domains, identify the equivalent jurisdiction, date, facts,
and authoritative sources. State material limitations and route decisions that
require licensure to an appropriate professional.

## 8. Common mistakes

| Mistake | Correct behavior |
|---|---|
| Add DeepSeek to “increase confidence” in Consultant Mode | Consultant Mode is Astra-only |
| Run DeepSeek after reading Astra's report | Freeze both inputs and start both tracks independently |
| Use one generic expert persona | Use every materially distinct approved role |
| Treat a prompt-library persona as authority | Use it only as role-questioning scaffolding |
| Reconstruct missing context silently | Ask one material question or label and approve the inference |
| Merge both verdicts into one score | Preserve and explain both verdicts |
| Auto-fix findings | Review remains read-only until separately authorized |
