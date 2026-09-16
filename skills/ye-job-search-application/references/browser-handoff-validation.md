# Controlled browser-handoff validation

This is a host-run regression check for the synthetic [review workspace fixture](../tests/fixtures/review-workspace.html). It does not authorize browser work, live outreach, applications, submissions, record writes, or use of real personal data. Use the internal browser only; do not open Chrome unless the user explicitly asks.

## 1. Static candidate checks

Before opening the fixture, the host should:

1. Confirm that only the five approved candidate paths changed, and perform the exact source/candidate byte readback required by the controller.
2. Check the two Skill files with the host's configured Skill-format checks. Resolve the known relative links from both entrypoints to `selected-role-execution.md`, then from that procedure to this validation file.
3. Inspect the fixture as a self-contained file. It must have no remote assets, network APIs, storage persistence, real submission behavior, or test personal information; it must expose the specified application, eight invitation, and research query modes with semantic labels.
4. Read the current runtime browser documentation before the controlled run. Record the documented setup and actual results for tab visibility, in-place binding, `markDeliverable()`, and `markHandoff()` rather than assuming a version-specific API.

Static inspection cannot prove browser behavior, retention, a live platform, or a later-turn result.

## 2. Application form: research, reset, and restoration

Use only a dummy name, dummy email, and a dummy test-resume file supplied by the host. Do not use a real resume or personal account data.

1. Open `review-workspace.html?mode=application` in a visible controlled tab. Start an inventory row with the actual returned URL, exact browser/tab ID, application-form component type, expected state, and pending user action.
2. Fill Full name, Email, Resume file, and Work authorization. Confirm the fixture's visible filled-value status and visible attachment filename. The disabled Submit application control must remain untouched.
3. Use the current documented visibility and deliverable-mark mechanism. Capture evidence of the actual visibility and `markDeliverable()` call in the inventory; do not infer success from an intended call.
4. Open `review-workspace.html?mode=research` in a separate disposable research tab. Do not reuse, reload, or navigate the application tab. Return by the exact application tab/handle, not a same-URL `goto`, and read its field values and filename in place.
5. Deliberately press **Reset test form** only as a controlled fixture-loss simulation. Verify all values, including the file, are cleared and that the application is no longer ready. Never use this destructive test step on a pending live review page.
6. Restore the same host-approved dummy input in the same fixture form, then freshly read back all fields and the filename. Reapply the current-turn deliverable mark and record the new call evidence.

## 3. Eight invitations: detect five, then recover only in the fixture

1. Open eight distinct visible fixture tabs with `?mode=invite&person=Contact1` through `Contact8`. Add all eight identities to the delivery inventory before filling them.
2. For each tab, verify the distinct heading and identity, enter a dummy invitation note, and confirm the live character count from the final textarea value. Keep every Send invitation control disabled and untouched. Record actual returned URL, tab ID, observed text, count, visibility result, and current deliverable mark for each identity.
3. As an explicit controlled-loss simulation, have the host make only `Contact6`, `Contact7`, and `Contact8` unavailable in this synthetic test. Do not describe this as normal cleanup, and never use it to test a live LinkedIn page.
4. Reconcile the locked eight-identity inventory. It must report five accessible contacts and three individually `missing/unverified` (or another evidence-backed state); five must not be reported as a complete set. Retain the absent identities and their last evidence.
5. With explicit host approval for this dummy fixture recovery only, open fresh synthetic tabs for each absent contact, refill the same dummy notes, read them back, make them visible through the documented mechanism, and apply fresh current-turn deliverable marks. The new tab IDs and URLs are new observed evidence, not proof the original tabs survived.

This fixture-only recovery does not permit reopening, retrying, or bypassing a LinkedIn stop event in a live workflow.

## 4. Later-turn retention observation

End an actual controlled browser turn after marked pages exist. In a later turn, list or bind only the original documented tab handles; do not use same-URL navigation to find them.

- For each expected item, record whether its tab is present, accessible, visible, and still contains the freshly read-back values.
- Reapply the appropriate current-turn mark after that readback and before the later turn's final response; the latest mark is the relevant mark.
- If a tab is absent or a mark/visibility operation is unavailable, report the actual limitation and do not claim persistence.

A single-turn run cannot demonstrate end-of-turn survival. Later-turn observation is required to make that narrow claim.

## 5. Evidence and claim boundary

Report the static results separately from controlled browser observations. Include the inventory identities, actual returned URLs, tab IDs, observed field/text/filename state, mark and visibility call results, and individual eight-to-five accounting. Keep browser readiness separate from any Notion record-sync result.

- **Candidate implemented:** the staged artifacts meet static inspection.
- **Reviewed installed:** the host approved and installed the exact reviewed candidate.
- **Live operational behavior proved:** only behavior observed through the documented runtime and authorized environment, limited to the evidence captured.

This fixture does not validate real ATS, LinkedIn, or Terra behavior; no live-platform validation, sending, submission, or Notion write is included in this task.
