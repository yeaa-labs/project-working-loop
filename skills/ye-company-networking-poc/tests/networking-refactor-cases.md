# Networking refactor behavioral cases

Run these as blind synthetic tests. Do not use live accounts, browsers, private records, or sends.

## Stakeholder selection

1. A 120-person company has a likely product head, assigned recruiter, exact peer, generic executive, and junior alum. Expect a 4–8 research plan, 1–2 initial activations, evidence grades, no automatic junior referral ask, and no quota-filling.
2. A 600-person company has a warm introducer to the likely manager, a relevant recruiter, and three adjacent leaders. Expect the warm route to suppress parallel cold outreach and a normal ceiling of 3–4.
3. A 20,000-person company has a broad CTO, exact-unit director, product recruiter for the unit, exact peer alum, and a `Hiring`-tagged person with unknown scope. Expect explicit team hypotheses, no CTO outreach, and the tag treated as a signal rather than ownership proof.
4. The user explicitly requests four named recipients. Expect all four in the delivery inventory even if only 1–2 are initially activated.

## Message design and state

5. Candidate evidence includes one directly owned workflow result, one platform-scale fact without acquisition ownership, and one team result. Expect one primary signal, at most one supporting signal, and preserved attribution.
6. The application changes from `considering` to `applying` to `applied` while old research returns late. Expect stale state wording to be rejected without discarding fresh person/team facts.
7. Test a confirmed HM, likely HM, assigned recruiter, same-level alum offering referral review, senior router, exact peer, and a `Hiring`-tagged contact. Expect one distinct intended outcome and CTA for each.

## Delivery and limits

8. A composer shows 200, a second shows 500, and a third has no initialized limit. Expect the first two to use their live limits and the third to remain unverified; never apply a universal 300 cap.
9. Use emoji, combining characters, and line breaks. Expect code-point and UTF-16 counts plus exact visible-text comparison.
10. Opening recipient P2 destroys P1's singleton modal. Expect P1 to lose current readiness and remain accounted for.
11. The user edits a filled note. Expect a fresh readback; never overwrite the user's edit from stale draft state.

## Completion audit scope

12. The DeepSeek career audit must contain exactly two checks: complete application prefill/correct resume/Submit untouched, and every requested invitation visible/recipient-specific/exact text within live limit/Send untouched. Stakeholder quality, liveness, deduplication, and Notion sync stay local.
