# Usage-aware routing

Read this reference once per session when Codex account or context pressure may
affect routing. At normal usage only heavy knowledge work goes to Web; quota
pressure may widen Web routing as described below.

## Read account windows

When local command execution is available, run the bundled read-only probe:

```bash
python3 scripts/read_codex_usage.py
```

It starts the official local Codex app-server, calls
`account/rateLimits/read`, prints sanitized JSON, and exits. It never reads or
prints auth tokens. If sandbox access prevents the app-server from reading its
own Codex state, request the normal scoped command approval. If the probe is
unavailable or errors, preserve the default route (local unless heavy) and do not guess.

Interpret `used_percent`, `window_duration_minutes`, and `resets_at`; do not
call credits or percentages an absolute token balance.

## Routing pressure

| Observable state | Routing behavior |
|---|---|
| Any usage level or unknown | Send only heavy knowledge work (Deep Research scale, long-form drafting, >~10 min) to Web; small and medium tasks stay local. |
| Any account window at least 70% used | Also route medium-size separable research or drafting to Web. |
| Any account window at least 90% used, or a rate limit is reached | Reserve Codex for local files/code/tests/commands, sensitive context, external actions, and authoritative verification. |

The user may always override a route for a named task.

## Current thread context

Account quota and thread context are different. If the host exposes current
thread context through `/status` or `thread/tokenUsage/updated` and 20% or less
remains, compact, hand off, or start a clean Codex task as appropriate. Do not
create, resume, or mutate a thread merely to inspect context usage, and do not
route local-only work to Web just because context is low.

