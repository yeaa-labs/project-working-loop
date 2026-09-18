# LinkedIn invitation delivery contract

Use this for unsent LinkedIn invitation composers. It governs evidence and state, not permission: the user owns `Send` unless they later authorize that exact action.

## State machine

Track each requested recipient independently:

`discovered → researched → selected → drafted → composer_loading → composer_ready → filled → read_back_verified → retained → user_sent`

Terminal or side states:

- `blocked` — a specific load, identity, login/CAPTCHA, risk, control, limit, or input failure prevents the next state;
- `abandoned` — the user removes the recipient or the route is intentionally closed.

Never skip from `drafted` to `read_back_verified`. A URL, successful input call, screenshot from an earlier state, or previously filled modal is not a currently retained deliverable.

## Composer readiness

Before typing, require all of the following visible together:

- correct recipient name and returned profile/vanity identity;
- editable, enabled invitation-note field;
- initialized live counter or another visible, inspectable live limit basis;
- final Cancel and Send controls.

A shell, spinner, generic page title, stale recipient, missing field, or uninitialized limit is `composer_loading`, not ready. Re-observe the state change; do not replace state observation with an arbitrary sleep.

## Live limit basis

There is no universal 300-character rule. Use the initialized composer observed for this recipient and surface.

Accepted bases are:

- a live initialized counter;
- an inspectable `maxlength` on the actual editable control;
- a current documented limit for the exact surface when the UI makes that basis verifiable.

If the limit or its counting basis is unknown, the invitation remains unverified. Do not invent a fallback cap. Email, InMail, accepted-connection messages, and invitation notes are different surfaces and must not share an assumed limit.

## Deterministic text check

After the final writing checks and edits, run:

```bash
python3 scripts/check_invitation.py < invitation-check.json
```

Input JSON:

```json
{
  "draft_text": "exact intended textarea value",
  "visible_text": "exact value read back from the textarea",
  "live_limit": {
    "basis": "counter",
    "limit": 200,
    "used": 143,
    "unit": "code_points"
  }
}
```

`basis` is `counter`, `maxlength`, or `documented_surface`. `unit` is `code_points`, `utf16_units`, or `unknown`; when the surface exposes only an observed count, include `used`. The helper reports Unicode code points, UTF-16 units, exact visible-text equality, effective count, verified live limit, and contradiction issues.

Exit codes:

- `0`: no mismatch, overflow, or limit-basis contradiction;
- `1`: unverified basis, mismatch, overflow, or observed-count contradiction;
- `2`: invalid JSON/schema.

The helper uses Python's standard library only, reads stdin, writes JSON stdout, has no browser/network/storage access, and does not echo private message text in errors.

## Fill, read back, and retain

1. Fill only after `composer_ready`.
2. Wait for the UI to stabilize.
3. Read back the exact recipient identity, exact textarea value including spaces and line breaks, current live count/limit, and visible untouched Send control.
4. Run the helper against the exact intended and observed values.
5. Mark `read_back_verified` only on exit `0` and matching recipient evidence.
6. Apply the documented current-turn retention/deliverable mark, then record `retained`.
7. Reconcile every expected recipient after later browser work. A user edit, navigation, reload, content loss, modal replacement, or lost retention invalidates the earlier readback.

LinkedIn may expose a singleton modal. Opening another recipient can destroy the first composer. Keep the expected inventory independent of visible tab count. If a requested composer cannot coexist or be retained, classify it accurately; do not silently shrink the set or claim an old screenshot is current delivery.

Use only the bounded input recovery allowed by the invoking skill. Never click Send to test state.
