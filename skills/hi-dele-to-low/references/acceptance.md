# Candidate acceptance checklist

This candidate is a local, uninstalled maintenance artifact. It becomes
installable only after a fresh independent review of the final candidate,
passing local checks, a hash-pinned installation preview, and installed-file
read-back by the parent controller.

## Required local evidence

- TOML configuration loads and rejects unsafe independent-review settings.
- Controller and reviewer resolve the same host binding by reference.
- A changed configuration does not claim to switch an active host session;
  supported fresh binding or fail-closed behavior is required.
- A fresh read-only controller process assesses the requested route and emits
  an approved actionable plan bound to the request before a worker can start.
  A rejected plan starts no worker and cannot silently choose another route.
- A config-only model/effort/provider/route edit changes fresh resolution,
  command generation, receipt validation, and generated profile output without
  edits elsewhere. Repointing `routing.heavy_primary` changes automatic heavy
  routing, repointing `routing.insufficient_balance_fallback` changes the
  balance edge, and an unknown or self-referential route reference fails to
  load.
- The configured continuation graph is a finite forward-only chain. Walking the
  edges from any route terminates at Terra without repeating a route, and a
  cycle, reverse edge, self edge, second edge from one route, edge leaving the
  terminal route, or balance route without a configured provider fails to load.
- Bounded self-contained work stays local; delegated work wholly below the
  near-threshold band routes to Terra even when confidence is plausible or
  uncertain; high-confidence or plausible work in or at the band routes to the
  configured heavy primary, currently Opus; only an unclassifiable uncertain
  range spanning the boundary asks for clarification. Automatic routing never
  returns the exhaustion fallback.
- The generated Codex worker command has only the explicit custom permission
  profile, no legacy sandbox override, disabled native agent/app/web surfaces,
  and no credential value in argv, receipt, brief, or log.
- The generated Claude worker command uses only the verified restricted flags,
  confines Read/Glob/Grep to the stage, and grants Write/Edit exactly on the
  approved output paths with doubled-slash absolute rules. It contains no shell
  tool, no global permission bypass, no bare mode, and no last-message file.
- The Claude worker process actually starts in the stage root, its prompt
  reports that same working directory with absolute staged paths, and the Codex
  controller/reviewer/worker working-directory contracts are unchanged.
- The Claude child environment is exactly PATH, locale, TERM, HOME, and the
  ordinary OS session keys USER, LOGNAME, SHELL, TMPDIR, SECURITYSESSIONID, and
  __CF_USER_TEXT_ENCODING, verified with synthetic values. It excludes Codex,
  OpenAI, DeepSeek, CLAUDE*/ANTHROPIC*, and unrelated variables, and no
  credential value reaches a receipt.
- A Claude result envelope is parsed by the controller, including a fenced JSON
  report, and the structured result is persisted by the parent rather than by
  the child. Requested and observed values stay separate, and internal Haiku
  accounting is never reported as the primary model.
- Environment credentials take precedence over the optional configured
  credential file; synthetic temporary-file tests verify fallback parsing,
  literal shell-like input without execution, unrelated-variable exclusion,
  sanitized failure codes, and that Terra, Opus, and controller/reviewer never
  read or receive the fallback.
- Staging rejects escapes and symlinks, accepts only explicit inputs, and
  leaves source unchanged until reviewed application.
- The direct-worker cap rejects an over-cap shared ledger reservation.
- Zero exit without structured completion, launch failure, and timeout do not
  claim completion, and each keeps the generic failure category.
- Each allowed capacity failure envelope (token quota, usage/rate window,
  context window) causes exactly one fresh approved continuation on the
  configured fallback route, with exactly one fresh controller plan per attempt.
  Excluded errors and deceptive successful prose cause none.
- A verified DeepSeek insufficient-balance envelope (top-level Codex
  turn-failure or error envelope, HTTP 402 Payment Required with insufficient
  balance at the configured endpoint, or an equivalent structured code) causes
  exactly one continuation on the terminal fallback route, both for an
  explicitly selected DeepSeek route and after an Opus exhaustion. A wrong
  endpoint, a generic 402, balance wording without the verified status, an
  authentication, rate, quota, context, or transport failure, a successful
  agent message repeating the same words, and unstructured process text all
  cause none.
- The full Opus exhaustion → DeepSeek balance failure → Terra chain runs one
  fresh approved controller plan per attempt, completes on Terra, is reviewed
  freshly, and is applied to the original source root. A skipped provider has no
  receipt, and the effective receipt is the final actually executed one.
- A failed attempt's verified partial outputs survive in its original stage and
  are carried forward only as labelled unverified reference material in a
  per-attempt origin directory, including when a later provider fails before
  creating any output of its own.
- Tampered staged inputs, tampered source baseline, tampered partial hashes,
  tampered continuation lineage, a tampered earlier attempt's partial file,
  brief, or staged input, a repeated route, a tampered controller plan or
  receipt digest, and a tampered reviewer digest each block the continuation or
  the application.
- Review packages and receipts bind output, brief/input baseline, controller
  receipt, controller-plan, and continuation content hashes. Byte changes after
  review and source-destination symlink paths block application before any
  source directory or file is created.
- The reviewer host matches the controller host, and reviewed application
  rejects changed controller host, workflow, brief, input inventory, plan,
  continuation lineage, or output identities.
- A synthetic end-to-end exhaustion, fallback completion, independent review,
  and reviewed application writes to the original source root, never to a
  staged input snapshot.
- Both explicit per-session workflow selections reach worker and reviewer
  prompts; a missing selection fails before launch and cannot be accepted by
  reviewed application.
- Generated compatibility profiles are current and parse as TOML.

## Honest limits

The Codex custom permission profile is verified as a write boundary for the
resolved stage root on the local Codex CLI version used during candidate review.
It has broad root read access and therefore is not a confidentiality boundary.
The Claude worker boundary is a different mechanism with its own limits: it is
the restricted file-tool set plus exact approved-output permission rules, it is
not an OS sandbox, and it is described separately in code, receipts, and docs.
Native tool settings and the prompt prohibit nested dispatch, but a hostile
process is not treated as a complete security boundary; the permission profile
or restricted tool set, staging, receipts, and the review gate provide the
concrete enforced controls.

Capacity classification reads only a structured top-level failure envelope. A
capacity failure that produces no such envelope is deliberately treated as a
generic failure and stops the dispatch rather than guessing from free text.

Provider insufficient-balance classification is deliberately narrower still. It
reads only JSON failure envelopes on the child's stdout stream, requires the
configured endpoint plus both HTTP 402 evidence and insufficient-balance
evidence, and therefore treats a real balance failure reported only on stderr,
only in prose, or without the 402 status as a generic failure that stops the
dispatch. No payment, recharge, provider-disabling flag, or cached balance state
exists anywhere in this workflow; each dispatch learns a balance failure only
from its own attempt.

No provider/API call, real credential-file read, live model telemetry, or
installation is part of candidate validation. Credential-fallback tests use
only synthetic temporary files, and continuation tests use synthetic result
envelopes rather than a launched model. Actual model and effort remain
unobserved unless a real runtime exposes them; the Claude effort is not exposed
by any observed envelope field today.
