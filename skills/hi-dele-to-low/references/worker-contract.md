# Staged worker and review contract

## Worker stage

The controller creates a fresh stage with three separate directories, plus one
more for a continuation:

- inputs contains only explicitly selected regular source files;
- outputs starts empty and contains only exact approved candidate paths;
- workspace/control contains the brief, output manifest instructions, the
  persisted controller plan, and the continuation lineage when one exists;
- reference/partial-outputs exists only for a continuation and holds one
  labelled `<NN>-<route>` directory per earlier attempt, each containing that
  attempt's unverified partial candidate files.

The source worktree and stage root must be disjoint. Input symlinks, output
globs, path traversal, stage-root symlinks, and nested dispatch requests are
rejected before a child starts.

The controller supplies its already-completed project-working-loop selection,
enabled or declined, on each worker and reviewer command. This is a required
session-scoped value, never global configuration. It is recorded in the worker
receipt and bound through review; a child must not ask the selection question
again.

Before the worker starts, a fresh read-only controller process on the selected
host assesses the proposed route. It receives only the brief, staged inputs,
approved-output list, and continuation lineage when present. Its approved plan
binds the configuration digest, host, route, workflow selection, brief digest,
input manifest, approved outputs, and that lineage. A rejected or clarification
response starts no worker and does not select an alternate route automatically.
The worker must read and follow `control/controller-plan.json`.

## Two separately described write boundaries

The Codex worker (Terra or DeepSeek) receives an explicit OS permission profile
as one inline TOML table. It grants root read, the resolved stage-root write
capability, and no child-tool network. The command intentionally avoids legacy
sandbox settings, which would override this profile. It also disables native
agents, multi-agent/app/hook features, web search, and approval prompts. Its
enforced write boundary is the whole stage root, recorded as
`permission_profile=delegation_worker` and
`write_boundary=explicit_stage_root_only`.

The Claude worker (Opus) has no OS sandbox profile. Its enforced boundary is the
restricted file-tool set plus exact permission rules, recorded as
`permission_profile=claude_restricted_file_tools` and
`write_boundary=approved_output_paths_only`. The verified command is `-p` with
the configured model and effort, `--safe-mode`, `--restricted`, `--no-chrome`,
`--no-session-persistence`, `--strict-mcp-config` with an empty inline MCP
configuration, `--tools Read,Glob,Grep,Write,Edit`, `--permission-mode dontAsk`,
`--output-format json`, and `--allowedTools` rules. Absolute rules begin with a
doubled slash: `Read(//stage/**)`, `Glob(//stage/**)`, `Grep(//stage/**)` for
the whole stage including inputs, reference, and outputs, plus one
`Write(//stage/outputs/<approved path>)` and one
`Edit(//stage/outputs/<approved path>)` rule per approved output. The rules are
passed as one separated value, so a stage root or approved output containing a
comma or whitespace fails closed rather than splitting into an unintended rule.
There is no shell tool, no agent or MCP surface, no global permission bypass,
and no bare mode. The child runs with the stage root as its working directory,
because `--restricted` confines its file tools to that directory and inputs,
outputs, reference, and workspace/control are siblings inside it; the prompt
reports that same directory and still uses absolute staged paths. The Codex
controller, reviewer, and worker working-directory contracts are unchanged, and
a Codex worker still runs in the stage workspace. HOME is preserved so the
standard Claude login works and is never replaced by an empty directory;
inherited user settings cannot widen the rights granted by this command.

Neither boundary promises that the child cannot read other root-accessible data,
and neither is a defence against a hostile process. Therefore the brief must
never include secrets, the controller must avoid broad workspace context, and
output validation runs before application.

## Worker completion

The child returns one JSON object with:

~~~json
{
  "status": "completed | blocked | failed | needs_clarification",
  "changed_outputs": ["exact/approved/path"],
  "self_tests": ["command description"],
  "limitations": ["optional evidence string"],
  "nested_dispatch": "not_attempted"
}
~~~

A Codex worker returns it through the launcher's last-message file. A Claude
worker returns it as its final message; the parent controller parses the result
envelope, accepts a fenced JSON report, and persists the structured object
itself. A Claude child is never required to write a control file, and it has no
permission to write one.

Subprocess success alone is not completion. The controller rejects a missing
or malformed object, output paths outside the exact list, unapproved staged
files, output/report mismatch, a nested-dispatch report, timeout, or nonzero
exit. It hashes every accepted output and records the manifest in the worker
receipt. An unfinished attempt may record verified partial files in that
manifest without claiming them as changed outputs. The worker never copies
results into the source tree.

A Claude result envelope may report `modelUsage` for several models. Internal
Haiku accounting can appear beside the primary model. The receipt records the
observed model list and auxiliary entries separately; only the requested primary
model may be reported as the observed primary, and the effort stays unobserved
because no runtime telemetry exposes it.

DeepSeek provider authentication prefers the configured environment variable.
If that variable is absent, the selected provider may read only its configured
`credential_file` from the single TOML source and extract only the configured
key using literal dotenv parsing. No shell sourcing/eval/expansion, alternate
file search, argv value, prompt value, receipt value, error value, or log value
is allowed. Missing, unreadable, malformed, empty, or duplicate target keys
fail before child startup. Terra, Opus, and the fresh controller/reviewer
processes never read or receive this fallback. Standard Codex authentication
remains available through supported HOME, CODEX_HOME, OPENAI_API_KEY, or
CODEX_API_KEY mechanisms while user policy config stays ignored.

A Claude worker receives PATH, locale, TERM, HOME, and the ordinary OS session
keys USER, LOGNAME, SHELL, TMPDIR, SECURITYSESSIONID, and
__CF_USER_TEXT_ENCODING. That bounded set is required for the standard existing
login to be visible: a controlled read-only diagnostic reported the CLI as
logged out with PATH, locale, TERM, and HOME alone. No provider credential and
no CLAUDE*/ANTHROPIC* variable is forwarded or invented, and no new credential
file is discovered.

## Failure categories and the continuation chain

Every non-completed worker receipt carries a coarse failure category. It is
`generic_failure` unless the worker's own top-level failure envelope classifies
as `token_quota_exhausted`, `usage_rate_window_exhausted`,
`context_window_exhausted`, or `provider_insufficient_balance`. Classification
reads only that envelope's designated fields. A successful envelope is never
inspected, so quota or balance words in worker prose, tool output, staged
inputs, or prompts change nothing. Auth failure, permission denial,
launch/network error, generic timeout, malformed completion, ordinary max-turn
limits, and per-response output truncation are excluded and stay generic.

`provider_insufficient_balance` is recorded only for a worker with a configured
provider, and only when that provider's own top-level Codex turn-failure or
error envelope reports HTTP 402 Payment Required together with insufficient
balance at the configured endpoint, or an equivalent structured verified
insufficient-balance code. Streamed item events, agent messages, and process
stderr are not scanned. A wrong endpoint, a payment failure without
insufficient-balance evidence, and any authentication, rate, quota, context, or
transport failure stay generic.

Each route has at most one configured continuation edge, and a failure continues
only on its own trigger: heavy-primary capacity exhaustion, or a verified
provider balance failure. Eligibility revalidates the failed receipt, its
approved controller plan and its lineage binding, staged inputs, original source
baseline, partial output hashes, and every earlier attempt in the chain. A route
is never used twice and the terminal fallback ends the chain, so a runtime chain
is at most Opus → DeepSeek → Terra or DeepSeek → Terra.

Each transition keeps the original source root, inputs, and approved outputs;
adds every earlier attempt's labelled partial reference material under its own
`<NN>-<route>` origin directory; and requires its own fresh highest controller
plan. Every earlier stage and its partial files are preserved and never
replayed, so useful material from an earlier provider survives a later provider
that fails before creating output. A failure with no configured edge stops.

## Independent review and application

The controller copies hash-bound staged outputs, the controller-authored
non-sensitive brief, the explicit staged input baseline, the controller receipt
and its persisted plan, the continuation lineage when present, and minimized
evidence to a fresh review package. The worker receipt records SHA-256 manifests
for the inputs and outputs plus the brief, controller-receipt, and
controller-plan digests. The controller re-hashes those materials before
packaging and before application. A new reviewer subprocess uses the same
configured host as the controller. Codex review receives a no-write permission
profile; Claude review uses plan mode, no Chrome, empty strict MCP
configuration, and only read/glob/grep tools.

The reviewer returns approved, rejected, or failed together with the exact
worker digest, output manifest, input manifest, brief digest, workflow
selection, controller-receipt digest, and controller-plan digest. The
controller records process evidence and rejects hand-built review objects.

Application re-hashes the stage and controller plan, verifies both receipts and
the request's controller-host/workflow/brief/input/stage/output/continuation
identities, then preflights every source destination before creating any parent
directory. A symlink, escape, changed byte, stale review, or extra file stops the
operation with no source copy. The destination is always the original source
root recorded in the receipt, including for a continuation result, and
`request_from_receipt` rebuilds that effective request so no staged snapshot can
be mistaken for the source tree.
