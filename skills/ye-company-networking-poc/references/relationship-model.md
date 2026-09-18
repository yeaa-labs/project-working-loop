# Durable company PoCs and changing relationships

Read this when updating company, person, affiliation, or relationship knowledge. Map the model into existing authorized storage; it does not require separate databases for every entity. The fresh shared config owns destination/schema bindings. The fully read and validated live rules page owns current policy and scoped relationship reuse; it is not evidence of a person's relationship or permission.

## Stable identities, many affiliations

**Person:** stable local or Notion reference, name, verified returned profile link, and sourced professional contact channel when needed. Do not merge people by similar names or create copies of one person for each team.

**Affiliation:** person reference, company, business unit/team (unknown allowed), role, current/former/unknown status, effective dates when known, observation date, and source. One company has many teams; one team has many people; one person can have several relevant affiliations. A transfer preserves prior affiliation as history. An external introducer stays at their actual employer while remaining linkable into another company's introduction view.

**Introduction path:** target company/team, target person, intermediary person, observed relationship/path source/date, and coverage. Multiple intermediaries can lead to a target and multiple targets can share an intermediary. A person may appear in employee and introduction views without duplicated identity. “None visible” is not “no mutual connections exist.”

Current employee/direct-contact and mutual-path views share one company entry. De-duplicate within each view and state whether coverage is a profile, selected team, or broader company search. Do not claim a company-wide total from a few target profiles. An invitation sent is not evidence of an accepted invitation or first-degree connection.

## Independent relationship attributes

| Attribute | Source and interpretation |
|---|---|
| LinkedIn degree | Actual observed degree or unknown, dated; graph proximity is not familiarity |
| Familiarity | User-confirmed wording; preserve its nuance rather than inferring from activity |
| Contact/use preference | User-confirmed priority and allowed company/team/purpose/channel; do not extrapolate one draft's mention permission |
| Availability | Evidence that a contact can or cannot help now; never infer it from silence alone |
| Last interaction | Relevant observed or user-confirmed interaction time/source; an application event may supply a pointer without copying full mail |
| Freshness | Last check and whether current knowledge may be stale; independent of closeness |

Record the user's relationship to a person separately from evidence that person knows the target. A visible mutual does not prove how well two people know each other or that an introduction is appropriate.

## Opportunity-recipient-action records

Durable identity and relationship knowledge may be reused, but each active networking decision is scoped to one opportunity. Keep a current operational record with:

- exact employer, role, requisition or canonical job URL, and application revision;
- verified recipient identity and current affiliation;
- recipient category and opportunity-specific evidence grade (`E1`–`E4`);
- intended outcome and one justified next action;
- application state (`considering`, `applying`, `applied`, or `unknown`);
- relationship/action state such as `not_contacted`, `invite_pending`, `connected`, `willing_to_help`, `introduction_offered`, `contact_reports_referred`, `official_referral_confirmed`, `declined`, or `unknown`;
- source, observation date, uncertainty, duplication check, and stop condition.

Do not promote a relationship state from silence, acceptance, or optimistic wording. `connected` does not mean familiar or willing. `introduction_offered` does not mean completed. A contact-reported referral does not become employer-confirmed without matching evidence. A newer application revision supersedes stale message wording but does not erase still-current person or affiliation evidence.

## Update current values and append history

Each change records person/path reference, field, previous/new value, event date when known, observation date, source, and confirmation basis. Keep an accessible current view plus append-only history; do not erase conflicting evidence. A new user statement can supersede an older familiarity or preference value while retaining history.

Examples with synthetic people:

- Northstar / Compute has A and B; A also knows Retail. Store A once, retain both affiliations/use scopes, and choose by selected team and user preference.
- C becomes first-degree after an invite. Update the observed degree but retain familiarity as unknown until user evidence supports a change. No permission to mention a mutual follows automatically.
- D was described as close months ago. Old evidence may require a freshness note, not a downgrade. If the user later reports distance, append that change and revise the current familiarity.
- E reports a referral for job X. The application owns the request and receipt. Company knowledge may retain a dated interaction pointer, but it does not prove willingness for job Y or a closer relationship.

Re-check only facts material to the current contact choice within authorized browsing scope. Do not impose a recurring full-network scan or repeat a familiarity question solely because a new role appeared. Reflect user-supplied changes promptly without reopening LinkedIn to verify subjective closeness.

## Storage and application ownership

Write durable company knowledge only to the exact authorized `notion.company_poc` item and its distinct per-item contact data source after fresh contract validation and dismissed-editor readback. The configured template source is validation-only; never use it as a live destination. This migration imports no historical relationship data; any future import requires an explicit approved scope. Do not recreate omitted schema fields without separate authority.

Company knowledge can store durable PoC identity, affiliation, relationship, and compact interaction metadata only. It must not own application-specific referral-process findings, sent/requested/accepted referral states, employer receipts, per-job outreach history, or request reminders. Those belong to `notion.application_archive` and its configured history location. Do not create or use an unconfigured company process-policy store.

When company work yields an application-linked event, the current host consumes it directly through the shared sync procedure before ending the turn. No recursive Skill or agent invocation is needed. A company-only invocation does not create an application. If the application destination cannot be validated, retain only a minimal actionable pending event when the configured recovery mechanism is available and report the exact blocker.

Mail-derived application events can contribute compact last-interaction metadata. Do not classify acquaintance, friendship, willingness, or permission from response rate, message sentiment, or elapsed time. Never expose raw private relationship history in external review material.
