# Safe-Lane — the 5 conditions (single-owner spec)

The broker auto-sends an action **iff ALL FIVE** conditions below hold. Any single
failure holds the action for out-of-band (Telegram) approval. `broker/safe_lane.py`
mirrors this prose as machine predicates — the two must stay in lock-step. This file
is in the write-blocked policy set; the assistant may not edit it.

P1a posture: **conservative — start empty, widen as trust builds.** With an empty
allowed-origins set, C4 fails by default, so almost nothing auto-sends. P1b makes the
allow-list / markers / origins data-driven.

| # | Condition | Auto-send requires (TRUE) | Holds when |
|---|---|---|---|
| C1 | Signal understood | action `type` is known AND payload is non-empty/well-formed | type unknown OR payload empty/malformed |
| C2 | Source of truth known | `recipient` on `allow-list.md` | recipient not on the allow-list (first-contact) |
| C3 | Operational not strategic | no marker from `strategic-markers.md` AND length ≤ operational cap | strategic marker hit OR over cap |
| C4 | Authority clear | `origin` is an approved principal-directed flow (P1a: near-empty ⇒ FALSE default) | authority ambiguous (P1a default) |
| C5 | Mistake recoverable | action type not on `irreversible-actions.md` | irreversible (force-push, external first-contact) |

Canonical examples:
- "reply 'got it, will do' to a known colleague's scheduling confirmation" ⇒ all 5 hold ⇒ **auto**.
- "email the board a status update" ⇒ fails C3 (strategic) + C5 (irreversible external) ⇒ **hold**, always.
