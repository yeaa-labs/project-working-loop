---
name: hi-dele-to-low
description: Route substantial execution through one configurable, staged Opus, Terra, or DeepSeek worker and a fresh independent host review.
---

# hi-dele-to-low

This is the canonical Y.EAA Labs delegation skill for Codex and Claude Code.
Use it when the controller can confidently classify work as substantial
execution, or when the project explicitly invokes it. Ask for clarification
instead of dispatching when the workload classification is materially unclear.
The legacy hi-dele-to-deepseek name is a compatibility wrapper to this skill;
it does not preserve the old Claude-child workflow.

The single source of truth is config/delegation.toml relative to this SKILL.md.
For an installed Codex skill, that is
~/.codex/skills/hi-dele-to-low/config/delegation.toml. It defines host bindings,
worker bindings, the heavy primary, exhaustion-fallback, and
insufficient-balance route references,
provider details without credentials, permission profiles, qualitative routing
thresholds, generated compatibility profiles, and receipt
validation. Change a valid model, effort, provider, or route setting there,
regenerate the profiles, and validate the result. A changed file cannot prove that an
already loaded app role changed; resolve a fresh binding or fail closed.

## Standing authorization

The owner granted standing authorization on 2026-09-05 for either skill
invocation to send only the current task-required internal skills, config, code,
diffs, tests, and context to the owner's configured DeepSeek account. This grant
does not require repeated per-content or same-credential-source confirmation.
The credential source and key are the provider `credential_file` and
`environment_key` config variables, currently the exact file and key granted in
the original authorization; they are used only for DeepSeek authentication and
never included in prompt or log payloads. Platform requests should cite this
existing grant. This authorization does not disable platform review: a skill
cannot override an actual platform block, and when DeepSeek was explicitly
required the workflow must report the specific blocking action or reason and
fail closed rather than silently using Terra. The single exception is the
user-authorized insufficient-balance transition below, which is allowed only on
verified provider balance evidence.

On 2026-09-07 the owner also directed that "deepseek没余额的时候，默认用terra来做"
(when DeepSeek has no balance, use Terra by default). That standing instruction
authorizes exactly one extra transition: when the configured DeepSeek provider
itself reports insufficient balance, the configured terminal fallback worker,
currently Terra, runs once instead. It applies both to a DeepSeek continuation
and to an explicitly selected DeepSeek route, so this is the one case where an
explicitly required DeepSeek path may be substituted automatically. Every other
DeepSeek failure, block, or unavailability still fails closed and is reported.
The workflow never takes a payment or recharge action, keeps no machine-wide
balance cache or disabled-provider flag, and learns a balance failure only from
its own attempt's evidence.

On 2026-09-07 the owner was asked whether either skill may send the
task-required code, config, tests, and project delegation rules to the owner's
Claude account for Opus execution, including future skill invocations, and
replied "允许" (allowed). That standing authorization covers the same bounded
task-required content for the configured heavy worker binding, currently
claude-opus-5 at xhigh effort. Authentication uses the standard Claude Code
login that already exists on this host: the launcher preserves HOME, never
empties it, and performs no new credential-file discovery. This grant likewise does not disable platform
review, and a blocked required path must be reported and fail closed rather
than silently substituting another worker.

## Controller rules

Honor existing session workflow decisions, ordinary approvals, and all
unrelated project policy. This workflow replaces the redundant final
DeepSeek-verifier loop only for unified delegation; it does not weaken
deepseek-indep-review elsewhere.

The primary controller may run at most the configured number of direct workers
and only with disjoint write sets and no dependency edge. Serialize overlapping
or dependent work. Workers never create nested workers, sidebar tasks, browser
or web actions, MCP/app actions, or external messages. A failed or timed-out
worker may have changed its stage; inspect it before any newly approved,
rescoped recovery. Do not blindly replay it.

The configured controller and reviewer for each host must reference the same
highest binding. The executor cannot approve its own staged result. Requested
model and effort come from the configuration. Actual model and effort remain
unobserved unless runtime telemetry independently exposes them; never copy
requested values into actual fields. A Claude result envelope may report
`modelUsage` for several models, including internal Haiku accounting beside the
primary model. Only the requested primary model may be recorded as observed;
auxiliary entries are recorded separately and never named as the primary.

Editing the configuration cannot switch an active Codex or Claude session.
Before using configured highest-model control or planning, establish it through
a supported fresh explicit host launcher and record requested versus observed
values. If that binding cannot be established, fail closed instead of assuming
the active session changed. In particular, the Claude configuration does not
change Claude's global default model; its configured host-highest control and
review binding is used only by the fresh explicit command.

## Qualitative routing

Estimate execution work only: exclude planning, approval waits, and final
review. A bounded self-contained task below the configured local threshold
stays in the current flow. Work wholly below the configurable near-threshold
band routes to Terra. A high-confidence or plausible estimate in or at that
band, or a documented controller large-work assessment, routes to the
configured heavy primary worker, currently Opus. Only a genuinely
unclassifiable uncertain range spanning the threshold requires
one focused clarification instead of an arbitrary fallback.

The controller records the estimate range, confidence, large-work reason when
used, route, and selection reason. Do not treat the threshold as a stopwatch.
An explicit per-task user model selection overrides this heuristic, including an
explicit DeepSeek selection; if the required path is unavailable, fail closed
and report rather than silently using another worker. The only automatic
substitution of an explicitly required path is the authorized DeepSeek
insufficient-balance transition to Terra described below.

Automatic routing never selects a continuation edge. DeepSeek runs only from an
explicit user or controller selection, or as the configured continuation after a
genuine Opus capacity exhaustion. Terra is selected automatically for
below-threshold work, and it is also the configured terminal fallback for a
verified DeepSeek insufficient-balance failure.

## Staged execution

Use scripts/dispatch_worker.py for an Opus, Terra, or DeepSeek worker. Supply
only explicit input files, exact approved output paths, and a non-sensitive
brief. It copies the inputs into a fresh staging directory. The child must put
deliverable candidates under that stage's outputs directory. Only validated
output paths and hashes can enter review or application, and the child never
applies them to the source tree.

Before a worker can start, the dispatch command launches a fresh read-only
controller process through the selected `--controller-host`. It must assess
the proposed route and return an approved actionable plan whose exact request
manifest binds the configuration digest, controller host, route, workflow
selection, brief digest, staged-input manifest, approved outputs, and, for a
continuation, the hash-bound prior lineage. A
rejected or clarification result starts no worker; the parent must submit a
concrete revised request. The worker reads the persisted hash-bound controller
plan from `control/controller-plan.json` and cannot change its route or plan.

The controller also supplies its already-completed session decision on every
dispatch and review: --workflow-selection enabled or --workflow-selection
declined. This is session-scoped, never stored in delegation.toml, and has no
default. A missing or invalid value fails before launch. It is recorded in the
worker receipt, review package, reviewer outcome, and reviewed application
gate so a fresh child does not re-ask the workflow-selection question.

For Codex workers, the launcher always uses ignore-user-config, ephemeral mode,
a child-only provider definition when DeepSeek is selected, and an explicit
default-permissions profile. It does not use legacy sandbox or
sandbox-workspace settings, because those override the tested custom
permission profile. The profile grants root read access, the resolved stage
root write access, and disables child-tool network access. Native agents,
multi-agent features, apps, hooks, web search, and approval prompts are also
disabled through fixed child configuration. For a Codex worker the write
boundary is the stage root, not a pre-execution per-file write allow-list.

The Claude heavy worker uses a different, separately described boundary. It is
not an OS sandbox profile. The launcher passes `-p` with the configured model
and effort, `--safe-mode`, `--restricted`, `--no-chrome`,
`--no-session-persistence`, `--strict-mcp-config` with an empty inline MCP
configuration, `--tools Read,Glob,Grep,Write,Edit`, `--permission-mode dontAsk`,
`--output-format json`, and an `--allowedTools` rule list that confines
Read/Glob/Grep to the stage and permits Write/Edit only on the exact approved
output paths. There is no shell tool, no agent or MCP surface, no global
permission bypass, and no bare mode; HOME is preserved for the standard login
and is never replaced by an empty directory. Inherited user settings cannot
widen these rights, and the controller still validates exact output paths and
content hashes before application.

The Claude worker process starts with the stage root as its working directory,
because `--restricted` confines its file tools to that directory and inputs,
outputs, reference, and workspace/control are siblings inside it. Its prompt
reports that same working directory and still addresses every staged file by
absolute path. The Codex controller, reviewer, and worker working-directory
contracts are unchanged: a Codex worker runs in the stage workspace.

Root read access is not confidentiality isolation for either worker. Never put
credentials, local-secret contents, or irrelevant private data in a brief or
worker report, and do not use this flow when broad read access would be
unacceptable.

DeepSeek uses the configured direct Responses provider through the child Codex
command. The launcher passes only the configured environment-variable name;
it never puts a credential value in argv, a prompt, a receipt, or a log. The
selected provider may optionally name `credential_file` in the single TOML
source. When the configured environment variable is absent, the controller
reads only that exact file and extracts only the configured key with literal
dotenv parsing: no shell sourcing, eval, expansion, alternate-file search, or
value logging. Missing, unreadable, malformed, empty, or duplicate target keys
fail closed. The environment variable takes precedence, and Terra, Opus, plus
the fresh controller/reviewer processes never receive or read this fallback.
Standard Terra execution preserves the local CLI's supported authentication
environment while ignore-user-config keeps user policy settings out of the
child command.

A Claude worker receives only PATH, locale, TERM, HOME, and the ordinary OS
session keys USER, LOGNAME, SHELL, TMPDIR, SECURITYSESSIONID, and
__CF_USER_TEXT_ENCODING. That bounded set is required: with PATH, locale, TERM,
and HOME alone the CLI reported itself logged out, and adding these standard OS
keys restored the existing first-party login. No Codex, OpenAI, DeepSeek, or
other provider variable is forwarded, and no new credential file is discovered.

Representative dry run:

~~~bash
python3 scripts/dispatch_worker.py \
  --route opus \
  --controller-host codex \
  --workflow-selection declined \
  --source-root /absolute/source \
  --stage-root /private/tmp/unified-stage-example \
  --input docs/brief-input.md \
  --approved-output generated/result.md \
  --brief-file /absolute/non-sensitive-brief.md \
  --lease-dir /private/tmp/unified-controller-ledger \
  --dry-run
~~~

When direct workers run concurrently, every dispatch for that controller task
uses the same lease directory. The ledger is task-scoped and enforces the
configured cap; it is not a machine-wide registry.

A zero child exit code is insufficient. Completion also requires a structured
result naming only approved staged outputs, self-tests, and a no-nested-dispatch
marker. It may also report limitations as evidence strings. A Codex worker
writes that object through the launcher's last-message file; a Claude worker
returns it as its final message and this controller parses the result envelope,
including a fenced JSON report, and persists it. A Claude child is never asked
to write a control file. Missing or malformed
output, an output-path mismatch, a timeout, or
any nested-dispatch report fails closed.

## Configured continuation chain

Two configured `[routing]` edges exist, each with its own evidence rule and each
usable at most once in a dispatch:

1. **Heavy capacity exhaustion → exhaustion fallback (Opus → DeepSeek).** The
   heavy primary worker stopped with a genuine top-level CLI or provider
   capacity failure. Only three categories qualify: exhausted token quota, an
   exhausted usage or rate window, and an exhausted context window, read only
   from the failure envelope's own fields.
2. **Verified provider insufficient balance → terminal fallback (DeepSeek →
   Terra).** The configured provider route's own top-level Codex `turn.failed`
   or error envelope reports HTTP 402 Payment Required together with
   insufficient balance at the configured provider endpoint, or an equivalent
   structured verified insufficient-balance code. This edge applies to a
   DeepSeek continuation and to an explicitly selected DeepSeek route.

Everything else stops the dispatch: auth failure, permission denial, launch or
network error, a generic timeout, a malformed completion, an ordinary max-turn
limit, per-response output truncation, a rate/quota/context failure on the
provider route, a payment failure without insufficient-balance evidence, a
failure at a different endpoint, and any wording in a successful worker report,
tool output, process stderr, staged inputs, or prompts.

So a runtime chain is at most Opus → DeepSeek → Terra, or DeepSeek → Terra when
DeepSeek was selected directly. An ordinary successful worker stops the chain
immediately. Terra terminates it, a route is never used twice, and configuration
loading rejects cycles, repeated routes, reverse edges, an edge leaving the
terminal route, and a balance route without a configured provider.

Before each transition may start, the controller revalidates the failed receipt,
its approved controller plan and lineage binding, the staged input baseline, the
original source baseline, the verified partial output hashes, and *every*
earlier attempt in the chain rather than only the previous one. Each transition
is a full fresh dispatch: a new stage, its own fresh highest controller plan,
and the same original source root, input inventory, and approved outputs.

Partial candidate files from each earlier attempt are copied into
`reference/partial-outputs/<NN>-<route>`, so material from different providers
keeps an unambiguous origin and an earlier attempt's useful files survive when a
later provider fails before creating any output. The whole lineage with hashes is
described in `control/continuation.json` as unverified reference material; it is
never treated as completed work and never replayed. Every earlier stage, its
failed receipt, and its partial files are preserved for inspection.

Only a fresh approved plan starts each fallback worker. The chain reuses the same
controller ledger sequentially and never overlaps writers. A completed result
receives the normal fresh highest independent review and reviewed application,
using the final actually executed receipt and targeting the original source
root, never a staged snapshot. A skipped provider has no receipt and never looks
executed.

## Fresh review and reviewed application

After a completed worker, use scripts/review_worker.py with the configured
host. It creates a minimal review package containing the hash-bound candidate
outputs, controller-authored non-sensitive brief, explicitly staged input
baseline, controller receipt and plan, continuation lineage when present, and
receipt evidence. It launches a fresh read-only reviewer process
with a separate permission profile: root read access, no write access, no
child-tool network, no Chrome, no MCP servers, no native agents, and no
write/edit/shell tools for Claude review.

The review command receives the same explicit --workflow-selection value and
the same host as the controller receipt; either mismatch fails before reviewer
launch.

The reviewer must return its process outcome with the exact worker-receipt
digest, output manifest, controller-brief digest, staged-input manifest,
controller-receipt digest, and controller-plan digest.
The controller records its launcher, exit code, command/output digests, and
evidence. A hand-built in-process review object is not a valid approval receipt.

Only apply_reviewed_outputs may copy staged files to the source root. It
requires a completed worker receipt, an approved fresh-process reviewer
receipt bound to the same receipt digest, output manifest, input baseline, and
brief digest; unchanged controller-plan and staged hashes; the same requested
controller host, workflow selection, brief, input inventory, stage root, output
list, and continuation lineage; and a full preflight of every destination path
before it creates a
directory or copies a file. Symlinks, escapes, changed bytes, and extra outputs
stop application. `request_from_receipt` rebuilds that effective request from a
receipt so a continuation result reaches the original source tree.

## Validation and generated profiles

Run the local suite. When a valid configuration change requires compatibility
profile updates, the controller regenerates them from that one source; never
hand-edit model or effort values in multiple files. A manual maintenance check
uses an explicit project profile directory:

~~~bash
python3 -m unittest discover -s tests -v
python3 scripts/generate_profiles.py --output-dir /absolute/project/.codex/agents
python3 scripts/generate_profiles.py --output-dir /absolute/project/.codex/agents --check
~~~

Validate a JSON receipt against the same configuration:

~~~bash
python3 scripts/validate_receipt.py /absolute/receipt.json
~~~

The generated profile TOML files are compatibility artifacts. They do not grant
an already loaded runtime a new model or effort. Resolve a fresh binding and
complete independent review before treating a configuration change as active.
