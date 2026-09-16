# Unified routing contract

The canonical configuration is ../config/delegation.toml. No reference file,
profile, validator, or command builder may duplicate its model, effort,
provider, host, route, or threshold values. The continuation edges are plain
worker references in `[routing]`, currently `heavy_primary = "opus"`,
`heavy_exhaustion_fallback = "deepseek"`, `insufficient_balance_route =
"deepseek"`, and `insufficient_balance_fallback = "terra"`. The qualitative band
uses the neutral `policy.heavy_near_minutes` and
`policy.heavy_near_tolerance_minutes` names; they describe workload size, not a
worker.

## Inputs and decisions

The controller supplies an execution estimate range, confidence, whether the
task is self-contained, and either a documented large-work assessment or an
estimate. The resolver returns exactly one of:

- local: bounded self-contained work below the local threshold;
- terra: delegated execution wholly below the configurable near-threshold band;
- the configured heavy primary: a high-confidence or plausible estimate in or at
  the near-threshold band, or a documented large-work assessment;
- needs_clarification: only a genuinely unclassifiable uncertain range spanning
  the threshold.

The configurable near-threshold tolerance makes confidently or plausibly
estimated work around 30 minutes large without turning the value into a
stopwatch. Estimates wholly below the band use Terra regardless of plausible or
uncertain confidence. Missing range endpoints, contradictory ranges, or an
undocumented large-work flag do not dispatch a worker. An explicit per-task
user model selection overrides this heuristic; an unavailable explicitly
required DeepSeek or Opus path fails closed rather than silently using another
worker. The one authorized exception is a verified DeepSeek insufficient-balance
failure, which continues on the terminal fallback route below.

Automatic routing never returns a continuation edge. DeepSeek is reached only by
explicit selection or by the capacity continuation below; Terra is reached by
below-threshold routing or by the balance continuation below.

## Host and worker resolution

Each host has controller_binding and reviewer_binding references. They must be
the same binding identifier, so the independent reviewer uses the same
configured highest binding through a fresh read-only process. The Terra, Opus,
and DeepSeek worker routes each resolve one configured worker binding: Terra and
DeepSeek through the Codex launcher, Opus through the Claude launcher. Each
worker binding declares its semantic workspace-write intent; the enforced
boundary differs per launcher and is recorded separately in the receipt. A
changed TOML file requires fresh resolution or a supported explicit launcher; it
does not alter an already loaded app role.

Controller planning/control also needs a supported fresh explicit launcher to
use its configured highest binding. If that binding cannot be established, the
controller fails closed instead of assuming the active session changed. The
Claude host binding never changes Claude's global default model, and the Claude
worker binding never changes it either.

For each dispatch, the selected controller host runs a fresh read-only planning
process before the worker. It may approve, reject, or require clarification;
it cannot silently substitute a route. An approval includes actionable plan
steps and evidence bound to the configuration digest, controller host, route,
workflow selection, brief digest, input manifest, approved-output list, and the
continuation lineage when one exists. The worker reads the persisted plan. The
independent reviewer must use that same controller host and bind both the
controller receipt and plan digests.

## Continuation edges

Each route owns at most one configured continuation edge, with its own trigger:

- the heavy primary route continues on `heavy_exhaustion_fallback` when its
  failure category is token_quota_exhausted, usage_rate_window_exhausted, or
  context_window_exhausted, read from a genuine top-level CLI or provider
  failure envelope;
- `insufficient_balance_route` continues on `insufficient_balance_fallback` when
  its failure category is provider_insufficient_balance, which requires the
  configured provider's own top-level Codex turn-failure or error envelope to
  report HTTP 402 Payment Required together with insufficient balance at the
  configured endpoint, or an equivalent structured verified insufficient-balance
  code. That evidence is read only from the envelope's designated fields, never
  from streamed item events, agent messages, process stderr, tool output,
  staged inputs, or prompts. A wrong endpoint, an authentication failure, a
  rate/quota/context failure, a transport failure, and a generic or malformed
  payment failure all keep the generic category.

Every other failure keeps the generic category and stops the dispatch. The
terminal fallback route has no outgoing edge, so runtime chains are at most
Opus → DeepSeek → Terra or DeepSeek → Terra. A successful worker stops the chain
immediately, and a skipped provider is never recorded as executed.

A transition is eligible only when the failed receipt is valid, its approved
controller plan still verifies and still binds the same lineage, the staged
inputs and the original source baseline still hash as recorded, the verified
partial outputs still hash as recorded, every earlier attempt in the lineage
still verifies, and the next route has not been used in this chain.

Each transition is a new request on the configured route with the same original
source root, inputs, and approved outputs, a new stage, a new brief that labels
all carried partial work, and a fresh highest controller plan that binds the
cumulative lineage: every attempt's route, failure category, receipt digest,
brief digest, origin label, and partial hashes, plus the input baseline. Only
that approved plan starts the next worker. There is no loop, no repeated route,
and no automatic replay of a failed attempt.

Configuration loading rejects a graph that is not a finite forward-only chain:
self edges, cycles, reverse edges, a repeated route on any path, an edge leaving
the terminal fallback route, two edges from one route, and a balance route
without a configured provider.

## Coordination

The primary controller uses one shared ledger directory for concurrent direct
workers. It may reserve no more than policy.direct_worker_cap slots. The
default is two, and a full or malformed ledger fails closed. The ledger is
scoped to one controller task, not a global quota or daemon. Every continuation
reuses the same ledger sequentially, so the failed and continuing attempts never
write concurrently.

Parallel work requires disjoint approved output paths and no dependency edge.
Dependent or overlapping work serializes. A timeout or failed worker requires
stage inspection and a newly approved, rescoped recovery; it is never
automatically replayed. Nested agents, sidebar tasks, external messages, and
worker-to-worker dispatch are prohibited.

Each dispatch and review carries its parent session's already-completed
project-working-loop selection, enabled or declined. It is a required
per-command value, not global configuration. The worker receipt, review
package, reviewer outcome, and apply gate must all agree on it.

## Receipt requirements

A controller receipt records its configured host binding, requested and actual
values, exact request manifest, route assessment, plan steps, limitations,
process launcher/exit code/command-output digests, status, and evidence. It is
valid only from the fresh subprocess path. A worker receipt records its route,
binding, launcher, requested values, actual values, observed model usage,
semantic sandbox, enforced permission profile and write boundary, Claude file
tool rules when they apply, resolved stage root, source
root, workflow selection, approved outputs, changed outputs, input/output
content-hash manifests, controller-brief digest, controller receipt and plan
digests, continuation lineage when present, worker self-test evidence,
limitations, process exit result, coarse failure category, status, errors, and
evidence. Actual values are unobserved unless runtime telemetry proves them, and
auxiliary model accounting is never recorded as the primary model.

A reviewer receipt is valid only when emitted by the fresh subprocess path. It
records the configured host binding, the worker receipt digest, the exact
output manifest, input baseline manifest, controller-brief digest, workflow
selection, controller receipt and plan digests, reviewer process launcher, exit
code, command/output digests, and evidence. A completed worker is applied only
after an approved review receipt binds the same worker digest and staged
materials, and the application always targets the original source root.
