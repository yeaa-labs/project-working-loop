# Application and referral evidence

Read this when maintaining `notion.application_archive` or checking relevant career mail. These are logical records and evidence rules, not instructions to create databases, properties, integrations, or a local policy store. Resolve all destination and field bindings from the fresh shared config after validating the live rules contract.

## Display and hyperlink evidence

Resolve hyperlink destinations and display choices from the current validated live rules page. This reference retains no dated local hyperlink destination preference, template, or fallback policy.

- Store application progress, outreach history, referral requests, referral-process findings, messages, and receipts in the configured application-history property. Preserve dates, exact text where available, evidence labels, uncertainty, and next actions. Append or reconcile; do not erase useful earlier history.
- Keep the configured parent contact property empty where required. Resolve the actual per-application contact data source from the configured application item; do not write live people or events to a reusable template source.
- Use verified role/team facts. Leave an unconfirmed interaction date blank or explicitly unknown rather than substituting a draft date.

After the current live rules determine a verified hyperlink destination, edit the relevant Notion database cell, select its displayed text, open the link editor, enter the exact verified URL, wait for the matching web-link option, and select that option. Dismiss and read back the resulting href. Pressing Return too early can choose a recent Notion page instead of the web URL. For a job title, use the configured mother-table title-cell editor and retain the page-opening control. Do not navigate to LinkedIn merely to test a stored href.

## Map evidence into the existing application

Resolve the real application row and its current schema before changing it. Preserve current fields and history. Match employer plus requisition or canonical job URL, then inspect plausible aliases, title, team, and location before creating a record. A filtered or partial result is not enough for deduplication.

When a richer event does not fit the validated current schema, use the authorized configured history location. If that is unavailable or incompatible, follow [the shared sync procedure](notion-sync.md): retain a minimal actionable pending event in the configured recovery queue, report the failure, and reconcile it on the next eligible run. A free-form local note is not a sync receipt. Do not create fields, a second referral-request database, or request-status fields in `notion.company_poc` without separate authority.

Application summary evidence includes employer, requisition or canonical destination, job/team/location, application state, related PoCs, selected current route, latest evidence, and next action. Application state and referral state remain independent.

Each request-history event uses a stable request ID, application reference, PoC reference, purpose, created or sent time when known, request state, referral-confirmation state, evidence events, and next action when set. A company-scoped referral route may link applications but does not prove a referral for each role. Keep the history on its originating application and link later applications to it instead of inventing multiple submissions.

## Two separate states, evidence-led

| Dimension | Suggested logical values |
|---|---|
| Request | not_started, drafted, sent, willing_to_help, declined, needs_follow_up, cancelled |
| Referral confirmation | unconfirmed, contact_reports_submitted, official_referral_confirmed, failed, invalidated |

These are not a mandatory sequence. Official confirmation can precede a contact reply. Older evidence must not silently downgrade newer confirmation; retain a conflict and clarify its scope. No observed reply is not evidence of rejection or willingness.

| Evidence | Supports | Does not support |
|---|---|---|
| A contact expresses willingness | Willingness to help | A submitted referral |
| A contact reports a referral submission with attributable context | Contact-reported submission | Independent employer confirmation |
| A matched employer receipt or portal record | Official referral confirmation for its stated scope | Completed candidate application or interview |
| An employer application receipt matched to the role | Application receipt | Referral attribution |
| A draft in a mail or invitation composer | Draft prepared or saved | A message was sent |
| A user says they sent something | User-confirmed sending, with ambiguity recorded | An independent sent-folder receipt |

For each meaningful event, retain source/channel, timestamp when known, linked message/page, company/job/person match, concise relevant evidence, and confidence. Do not copy full unrelated mail, login data, or referral tokens into reports or external packets. A referral message can require candidate action; its arrival does not automatically submit an application.

## Relevant-mail checks

1. Resolve the intended personal mailbox from the fresh config and private runtime context, then compare it with the actual connector or browser identity. If it is another account, do not search it or silently switch accounts.
2. Search only known pending career threads and role/company/contact identifiers, using the last successful check plus a small overlap and message-ID deduplication. On a first run, use tracked request dates. Do not scan unrelated mail or treat unread state as the sole filter.
3. Read enough context to establish sender, request, company, and exact role. A subject or company keyword alone is insufficient. Leave unmatched or ambiguous evidence pending with its reason.
4. Write authorized updates through the shared procedure and read them back. Company knowledge may retain compact interaction metadata when useful, but detailed mail and per-job request states remain with the application.
5. Record check success/time and actual updates. If access fails, record failure rather than advancing the successful-check cursor. New actionable evidence or required user action merits notice; unchanged monitoring stays quiet.

Relevant-mail read access does not authorize a schedule, reply, referral request, application, or submission.
