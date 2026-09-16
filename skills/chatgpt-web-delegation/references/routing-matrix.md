# Routing matrix

Read this reference when a task is mixed, ambiguous, or could use more than one
ChatGPT surface.

## Surface decision

| Observable task shape | Route | Reason |
|---|---|---|
| Stable synthesis from current approved Project sources | Codex unless the synthesis is long-form/heavy | Small syntheses cost more via Web than locally. |
| Writing, rewriting, translation, summarization, outlining, brainstorming, explanation, critique, or planning with safe self-contained context | Codex, unless the deliverable is long-form/heavy (then Standard Project chat) | Small and medium knowledge work is cheaper locally (measured 2026-09-02). |
| One or a few recent facts with direct public sources | Codex (local web fetch/search) | Bounded lookups are cheaper locally. |
| Market, competitor, policy, literature, or ecosystem investigation | Deep Research in the Project | Benefits from an explicit multi-source research plan. |
| Local code, uncommitted state, logs, tests, git, or artifact generation | Codex | Requires the local environment and authoritative execution. |
| Secrets or context that cannot be minimized safely | Codex | The information must not cross the boundary. |
| External write, account setting, Project mutation, publishing, or messaging | Codex/tool-specific workflow | A ChatGPT answer cannot authorize or verify the action. |
| Small self-contained knowledge or text task | Codex | The user narrowed the standing preference to heavy work only (2026-09-02). |

Use only a surface that is visibly available. A Pro label is not proof that a
specific mode is available.

## Authorization states

| State | Allowed next action |
|---|---|
| Skill classifies the task as heavy Web work under the standing preference | Announce route and information categories; preflight and submit once without another pause. |
| User explicitly said keep it in Codex | Do not prepare or submit a Web packet. |
| Project/source/settings change is needed | Show exact change and request separate action-time approval. |
| First submission is ambiguous or unusable | Stop; no retry until fresh direction. |

## Visible preflight

Before submitting, confirm:

1. ChatGPT is logged in and usable.
2. The active target is the private `Y.EAA` Project.
3. A clean conversation is used for this task.
4. The selected standard/search/research surface is visible.
5. Required sources are visible or the packet is self-contained.
6. No login challenge, CAPTCHA, unusual-activity warning, rate limit,
   permission error, selector ambiguity, or uncertain prior submission exists.

If known Project instructions or sources are stale, do not mutate them. For a
public-web task, make the packet self-contained and label stale Project material
historical. If the task depends on current private Project context, stop and ask
whether to update the Project or keep the work in Codex.

## Mixed-task example

Request: “Research six competitors' current APIs and then update my local
uncommitted implementation.”

- Web receives only the public research question, deliverable, and safe product
  category context.
- Codex receives the cited research result, checks the important sources, then
  inspects local files, chooses an approach, edits code, and runs tests.
- Local diffs, secrets, logs, and test output never go to ChatGPT Web unless the
  user separately approves a minimized, non-sensitive packet.
- Do not substitute ChatGPT Work cloud, a new Codex task, or a subagent for the
  visible Web route.

Split mixed work only when the knowledge portion is itself heavy (Deep Research
scale); otherwise keep the whole task local. The local, sensitive, executable, or
authoritative-verification portion always stays in Codex.

## Standard fallback reasons

Use one concise value: `codex_only`, `unsafe_context`, `project_unavailable`,
`surface_unavailable`, `source_unavailable`, `login_or_account_block`,
`selector_ambiguity`, `submission_ambiguous`, `result_unusable`, or
`verification_failed`.
