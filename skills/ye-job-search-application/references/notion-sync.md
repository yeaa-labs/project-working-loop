# Shared live Notion contract and recovery

This is an agent-executed procedure, not a background synchronization service. Both career Skills load `config/notion.json` afresh; the networking Skill reaches the same file through its shared relative path. The config is the only location for destination identities, URLs, expected titles, versions, statuses, sections, end markers, and schemas. The live rules page is the only current policy/template source.

`scripts/notion_contract.py` is a small pure helper for host-supplied observations and in-memory queue snapshots. It does not open a browser, access Notion, read the live queue, write a queue, or implement a sync service. Its `schema` input is a host-normalized contract projection after a real host read; it is not a claim that Notion exposes operational policy strings as raw schema, and it must not be manufactured by copying config values.

## Fresh validation sequence

Run this sequence at the start of each dependent run, and again after a material config, page, or authority change. Reuse the verified run context within an unchanged phase; do not re-read an entire page before every field operation.

1. Read the shared config fresh. Require its supported version, expected account, operational state path, complete logical mappings, and non-null binding for every destination needed by the task. Missing or malformed config blocks writes to that destination; report the logical binding and failed check.
2. Resolve the configured rules page in the authorized internal browser and have the host read its complete live content. Paginate, expand, or unfold as needed. The host supplies an observation that includes account, identity, URL, title, version, status when configured, all configured sections, end marker, and normalized contract schema. Validate it against the fresh config.
3. Treat the verified page's factual content as the current approved policy, but never as authority to send, submit, publish, grant access, or override session safety. A newer explicit user decision wins. A missing, partial, wrong, or unapproved policy observation blocks dependent actions; independent read-only discovery can continue only with that limitation clearly reported.
4. Validate each relevant destination's fresh live observation against the config before private reads or writes. This includes account, exact identity, approved-origin URL/page identity, title, schema, current access, and privacy. For the rules and search-runs pages, also require the configured page-content checks. A dated access observation is evidence, not a continuing permission grant.
5. Read relevant live records only after the contract is valid: the complete unfiltered `notion.application_archive` view for deduplication, current applicable `notion.search_runs` batches, the exact `notion.company_poc` item and its distinct per-item contact data source, and any pending recovery entry. A filtered empty view is not sufficient proof that no record exists.
6. Execute only an action covered by actual scoped task authority. After a write, dismiss the editor and read back the exact object, changed values, event marker, stable page reference, and preserved relevant state. Only that readback is persistence evidence.

### Scoped continuation and recovery boundary

[Selected-role execution](selected-role-execution.md) describes the bounded sequence that an actual selected-role prefill request may cover; this reference does not grant it. Within an authorized selected-role preparation operation, necessary writes to already-authorized existing application and company records may continue without a new consent prompt for each field or editor interaction. A newly discovered exact employer ATS URL or relevant verified professional profile within the selected-job bundle is not an unrelated destination; an unrelated employer, account, record destination, new sensitive datum, or unknown file identity remains outside scope. Preserve explicit user limits, live-policy validation, privacy checks, and legal/platform action-time requirements. If authority or a material fact is genuinely missing, ask precisely and continue independent authorized work. A prefill or draft remains distinct from a send or submission.

### URL identity handling

The helper accepts a Notion URL only on the approved `https://app.notion.com` origin when its embedded 32-hex page identity normalizes to both the configured binding ID and the independently supplied observed ID. A title slug, dashed UUID, and unrelated view query can vary without changing that identity. A malformed URL, wrong origin, conflicting embedded identity, or a `p=` side-peek that identifies a different page fails closed.

This is offline normalization only. It does not prove that Notion currently resolves a slug, alias, view, or page permission; the host still verifies the live object, access, privacy, and full content.

Known approved policy changes are reconciled by updating the config binding and validating the new live page. Never blindly retain an outdated version or fall back to a local copy. Do not create an alternate formal policy source.

## Application and company writes

Build a stable event before an authorized application-linked write: event ID, application key, original destination ID, known page ID or null, minimal intended change, concise evidence reference, observed time or null, attempted time, failure if any, and next action. Keep event time separate from backfill time. Do not store credentials, full mail, raw private relationship material, or policy content in recovery state.

Map changes only through the logical schema bindings in the fresh config. Preserve unrelated fields and existing history. The configured application history property owns application progress, outreach, requests, referral-process findings, messages, and receipts. The configured parent contact field remains empty where the schema requires it. Resolve a distinct per-item contact data source within each exact application or company item; a configured template source is validation-only and never a live destination.

Application state and record-sync state are distinct. Preserve precise evidence labels rather than promoting a draft, prefill, user statement, generic invitation, contact statement, or employer receipt into a stronger state. For application evidence and hyperlink readback, use [application-evidence.md](application-evidence.md). For company/person structure, the networking Skill uses its relationship-model reference.

Do not create fields, a second request database, or a competing company request or process-policy store for convenience. This migration imports no historical data; a future import requires an explicit approved scope. No scheduler or API grant is implied by these instructions or by browser access.

## Search batches

The configured search-runs page replaces a permanent local search log. Once its binding and privacy are verified live, an authorized batch is written as one distinct child page using the configured template/schema. Preserve the configured batch identity, coverage, candidate evidence, uncertainty, next action, and readback rather than inventing a separate database or schema.

If a search-batch write cannot be completed and read back, report it as unpersisted. Do not turn the application recovery queue into a search log or claim a batch exists from a local draft.

## Queue v1 and uncertain writes

The configured operational JSON file is a recovery queue, not a business database, rules store, or search log. Its shape remains:

```json
{
  "version": 1,
  "pending": [],
  "receipts": []
}
```

Keep the existing live file untouched except through an authorized, atomic host persistence operation. Preserve unrelated top-level fields and history. Use stable event and destination IDs, and maintain one pending entry per event ID. For an existing event, preserve its established application key, destination ID, and known page ID. A matching update may refresh mutable failure details; a conflicting application, destination, or known page returns an explicit conflict disposition and retains the original pending payload. An unknown page ID may become known only with matching application and destination evidence.

On an uncertain or failed application-linked write:

1. Inspect the original application row and existing event before retrying. A timeout, filled editor, clicked save, local handoff, or memory is not proof of persistence.
2. Before reporting an existing event as synchronized, check its receipt destination and page identity. A conflicting destination or known page is a conflict, not `already_synced`. If the event already exists in its original destination with matching application and page evidence, verify it, remove its pending payload, and retain one compact receipt containing only event ID, destination ID, page ID, and verification time.
3. If the original write failed, add or update the one minimal pending entry atomically. If queue persistence fails too, report both failures and the unsaved change in the active task.
4. If the config destination changed after an event was queued, do not replay or reroute its payload automatically. Keep it pending and reconcile the old and new destinations explicitly.
5. Do not append a duplicate pending event or receipt during replay. A successful dismissed-editor readback with matching event, application, destination, and page identity is the only condition for removing pending payload.

One bounded recovery attempt per actual access condition is sufficient. If still blocked, preserve the pending item and continue only independent preparation with the limitation visible.

## Completion report

Report the bindings validated, records actually read, rows/pages actually persisted and read back, stable page references, evidence distinction, and every pending item. Distinguish historical receipts from fresh checks. Do not claim a live write, privacy state, historical import, integration, scheduler, or policy migration that was not verified in the current authorized run.
