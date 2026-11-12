# Review findings

Reviewed branch `weekday2day5-pp` at `2c91cbd` against `week2day4-pp`.

## P2: Routine authentication events discard unsaved page state

- **Status:** Fixed
- **Location:** `dashboard/src/App.tsx:203–206`
- **Scenario:** A signed-in handler switches browser tabs or the session token refreshes while editing settings or writing a proposal rejection reason.
- **Cause:** Supabase emits `SIGNED_IN` when a tab regains focus and `TOKEN_REFRESHED` during normal sessions. The authentication callback clears `handler` on every event. The app then renders its loading screen, unmounting the current page and discarding unsaved values and open dialogs.
- **Fix:** Update the access token without clearing the handler for the same signed-in identity. Clear identity-dependent state when the user changes or signs out.
- **Verification:** Regression tests emit same-user `SIGNED_IN` and `TOKEN_REFRESHED` events while Settings has an edited approval limit and an open confirmation dialog. The same form and dialog remain mounted, and saving sends the refreshed token. Separate tests cover sign-out and switching identities. The original same-user regression tests failed before the fix.

## P2: Diff filtering hides legitimate changed lines

- **Status:** Fixed
- **Location:** `backend/configurations.py:25–26`
- **Scenario:** A configuration or recipe change contains lines beginning with `--` or `++`.
- **Cause:** The unified-diff filter discards every line beginning with `---` or `+++`, including content lines with a removal or addition marker. The approval screen can therefore display an incomplete change.
- **Evidence:** Calling `text_diff("Keep rule\n", "Keep rule\n++ new rule\n")` returns only the context line. Replacing `--- old restriction` with `--- new restriction` displays the addition but omits the removal.
- **Fix:** Skip the actual unified-diff file header positions rather than filtering all matching prefixes.
- **Verification:** Exact-output regression tests cover additions and removals beginning with `++`, `--`, `---` and `@@`, plus unchanged text. The `++`, `--` and horizontal-separator cases failed before the fix.
