# Delegation receipt

Read this reference before reporting a Web attempt or a substantial route
decision. The receipt records the transaction boundary without copying the full
prompt or sensitive browser state.

## Required fields

```yaml
task_id: stable local identifier
timestamp: ISO-8601 with timezone
route: codex | chatgpt_standard | chatgpt_search | chatgpt_deep_research
authorization: explicit_request | confirmed_after_preview | not_required_codex
packet_summary: information categories sent; never the secret-bearing payload
target_project: Y.EAA | null
conversation_reference: visible title or stable URL when safely available
submission_count: 0 | 1
result_status: adopted | partial | rejected | unavailable | ambiguous
verification: concise checks performed by Codex
fallback_reason: null | standard fallback reason
```

Do not record cookies, access tokens, hidden IDs, authentication material,
passwords, full sensitive prompts, `.env` values, or `local-secrets/` content.

## Compact conversation form

Use this when the user wants brevity:

```text
Receipt — task=<id>; route=<route>; auth=<authorization>; project=<project>;
conversation=<reference>; submissions=<0|1>; status=<status>;
verification=<checks>; fallback=<none|reason>.
```

Example:

```text
Receipt — task=market-2026-09-01; route=chatgpt_deep_research;
auth=explicit_request; project=Y.EAA; conversation="Competitor API study";
submissions=1; status=adopted; verification=2 primary sources checked;
fallback=none.
```

## Persistence

- For substantial work or a pilot, save the receipt with the task artifacts.
- For lightweight delegation, the compact conversation form is sufficient.
- For a Codex-only classification, use `submission_count: 0`,
  `target_project: null`, and the matching fallback reason when applicable.
- If submission state is uncertain, use `result_status: ambiguous` and keep
  `submission_count: 1`; do not represent a retry that did not occur.
