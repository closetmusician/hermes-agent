# Interview Synthesis

**Date:** 2026-07-03

## Answers

| # | Question | Answer |
|---|---|---|
| 1 | Foundation | **Hybrid reset** — fresh checkout of latest upstream hermes; re-apply only the tiny genuinely-needed local delta; add the reference-repo control-plane pattern (declarative policy + skills + one state file) with send moved out-of-process. |
| 2 | Timeline | **Months of scope, but phased for fast incremental value** — the plan must be detailed enough for a months-long build, structured so each phase/milestone delivers something usable quickly. |
| 3 | Autonomy | **Propose-only + safe lane** — agent drafts everything; auto-sends only inside a recoverable, well-understood lane (5-condition auto-resolver); everything else waits for approval. |
| 4 | Channels | **Telegram + Teams/Outlook (Diligent work) + WhatsApp.** Yahoo personal email was **not** selected. |

## How the answers reshape the plan

### Reshape 1 — Center of gravity moves from Yahoo email to Teams/Outlook (the biggest change)
The last ~7 weeks of effort (47 commits, the entire email-send-guard saga) protected the **Yahoo personal email** send path. The user did **not** pick Yahoo as a priority channel. The user *did* pick **Teams/Outlook** — the corporate stack, the highest-impact channel, and the exact place the FOCI-token reliability incident happened.

**Consequence:** The remediation does **not** invest further in the Yahoo email-guard code. That work is treated as a sunk cost and a *lesson* (how not to build a boundary), not an asset to extend. The email-triage capability is re-pointed at Teams/Outlook via `pm_os`, which already has hardened tooling and a tool-registry. The one durable piece of the email work — the idea of an out-of-process, allow-listed send primitive — is generalized to *all* outbound channels, not Yahoo-specific.

> **Research-vs-user tradeoff (user wins):** the transcript/planning evidence would have pointed at "finish and verify the Yahoo `/approve-email` flow." The user's channel priorities override that; Yahoo send verification drops to optional. Tradeoff: any half-finished Yahoo approval code is left dormant, not deleted, until the general send primitive supersedes it.

### Reshape 2 — Hybrid reset is confirmed, so the fork is a *source*, not a *base*
The plan targets a clean upstream checkout. The 47-commit fork becomes a quarry: we cherry-pick *ideas* (the governance intent) and the *few* genuinely-needed local changes, not the god-file patches. This means PHASE 0 includes "establish the new base" rather than "keep stabilizing the old one."

### Reshape 3 — Months scope with milestone gating
Because the user wants months of depth but fast value, the 5 phases are each split into a **"thin usable slice" milestone** delivered early, followed by depth. Every phase names its milestone deliverable and the acceptance gate that proves value before moving on.

### Reshape 4 — Propose-only + safe lane is the reliability spine
The autonomy answer directly resolves the project's central contradiction (autonomy vs safety). The plan adopts clawchief's 5-condition auto-resolver as the *only* autonomy mechanism: everything not in the recoverable safe lane is propose-only. This is wired into the out-of-process send primitive, so it is *enforced by architecture*, not by prompt.

### Reshape 5 — WhatsApp is wanted, so it gets fixed, not disabled
The Phase 0 instinct to disable the WhatsApp zombie is overridden: the user wants WhatsApp. It gets *repaired* (bridge pairing) or *cleanly disabled-with-intent-to-fix*, not left as a permanent retry loop. Discord (not selected) gets disabled outright to kill its 5-minute error spam.

## Net effect on phase priorities

1. **Teams/Outlook is the primary integration target**, not Yahoo email.
2. **The out-of-process send primitive is the architectural keystone** and applies to Telegram + Teams/Outlook + WhatsApp uniformly.
3. **The fork is mined, not maintained.**
4. **Each phase ships a thin usable slice first.**
