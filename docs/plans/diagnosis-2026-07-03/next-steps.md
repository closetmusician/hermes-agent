# Next Steps — Start Here Monday

**Audience:** a Sonnet/Opus-tier session (or Yu-Kuan) executing the first concrete moves of the remediation plan. These are the **first 3 actions only** — Phase 0's opening. Each has exact commands, files to read, expected outcome, and a failure branch. Do them in order. Stop after action 3 and report.

> Do **not** skip ahead to building features or touching the email-send-guard plugin. The whole point of this sequence is to fix the base *before* building on it. If you feel pulled toward "just fix the email flow," re-read `diagnosis.md` §1 — that pull is the trap.

---

## Action 1 — Confirm ground truth: is the gateway actually up, and what's it doing?

**Why:** As of 2026-07-03 the gateway crashed 3× in the morning and has two zombie platforms (Discord, WhatsApp). Before changing anything, confirm current live state so you know what you're fixing.

**Commands:**
```bash
# Is the gateway process alive and how long?
launchctl list | grep hermes
ps aux | grep -E "hermes_cli.main gateway" | grep -v grep

# What platforms are connected vs erroring right now? (last 100 lines)
tail -100 ~/.hermes/logs/errors.log
grep -c "No bot token configured" ~/.hermes/logs/gateway.log   # Discord zombie count
grep -c "Reconnect whatsapp failed" ~/.hermes/logs/gateway.log # WhatsApp zombie count

# Any nonzero exits today? NOTE: exit_nonzero lives in gateway-exit-diag.log, NOT gateway.log
grep "exit_nonzero" ~/.hermes/logs/gateway-exit-diag.log | tail -5
```

**Files to read first:** `docs/plans/diagnosis-2026-07-03/evidence/phase0-state-snapshot.md` (the full state as of the diagnosis).

**Expected outcome:** You can state, in one sentence, whether the gateway is currently up, which platforms are connected, and which are zombie-retrying. You will almost certainly confirm Discord and WhatsApp are erroring on a loop.

**If it fails / surprises you:** If the gateway is *down* (no process), that's fine — Action 2 rebuilds the base anyway; just note it. If you see a *new* crash signature not in the snapshot (e.g. a Python traceback, not a DNS/network error), capture it verbatim and report before proceeding — it may change Phase 0.

---

## Action 2 — Stop the bleeding: disable Discord (and decide WhatsApp)

**Why:** Discord is enabled with no token, logging an error every 5 minutes forever, for a channel the user did not select. This is pure noise and wasted cycles. Killing it is safe and reversible.

**Commands / steps:**
```bash
# Find where platforms are configured (env + any cli-config)
grep -n "DISCORD" ~/Code/hermes/.env ~/.hermes/.env 2>/dev/null
```
- Comment out / blank `DISCORD_BOT_TOKEN` **and** whatever flag enables the Discord adapter (check `.env` and `cli-config.yaml` if present) so the adapter is **not created** at startup — not just failing to connect.
- **WhatsApp decision (the user wants it):** do **not** disable it silently. Either (a) run `hermes whatsapp` to pair the bridge and get it connecting, or (b) if pairing isn't possible right now, disable it *with a note* in `tasks.md` that it needs pairing. Do not leave it in the 300s retry loop.

**Positive example:** after this, a fresh 30-minute tail of `~/.hermes/logs/errors.log` shows **zero** Discord errors.
**Negative example:** leaving `DISCORD_BOT_TOKEN=` present-but-empty so the adapter still tries and logs "No bot token configured" every 5 minutes — that's not disabled, that's still a zombie.

**Expected outcome:** Restart the gateway (`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` or `hermes gateway run --replace`), wait 10 minutes, confirm Discord noise is gone and WhatsApp is either connecting or cleanly off.

**If it fails:** If blanking the token doesn't stop adapter creation, the enable flag is elsewhere — grep the config loader (`gateway/config.py`) for where the platform list is built, and disable at that list. If restarting the gateway triggers a *new* failure, capture it and stop — report before continuing.

---

## Action 3 — Scope the new base: measure the real delta before cloning

**Why:** The plan says "replace the fork with a clean upstream checkout and re-apply only the tiny genuinely-needed local delta." Before doing that (a bigger move that needs approval), *measure* that delta so the Phase 0.1 work is sized honestly and you don't accidentally lose something needed.

**Commands:**
```bash
cd ~/Code/hermes
git fetch origin
# What files does the fork actually change vs upstream, excluding the email-guard saga?
git diff --stat origin/main...HEAD | tail -40
# Which of the 47 commits are NOT email/guard churn? (candidates to port)
git log --oneline origin/main..HEAD | grep -viE "email|guard|approve|imap|smtp" 
# Confirm the plugins that are local additions
ls plugins/ | grep -E "control-room|email-send-guard|tool-registry-guard|teams_pipeline"
```

**Files to read:** `docs/plans/diagnosis-2026-07-03/comparison.md` (what the new base should adopt) and the DECISION MATRIX in `remediation-plan.md` (what to port vs abandon).

**Expected outcome:** A short list — probably very short — of local changes that are *not* email-guard churn and might be worth porting to the clean base. Most of the 47 commits should fall away as "abandon." Write this list into `tasks.md` as the input to Phase 0.1.

**If it fails / the delta is bigger than expected:** If you find substantial non-email local work you didn't anticipate (e.g. real Teams/`pm_os` integration, useful skills), **stop and surface it** — it changes the "port small delta" assumption and the user should weigh in before the clean checkout. Do not start the clean checkout in this session; Action 3 ends at *measuring and reporting*, because the checkout itself is a Phase 0.1 step that follows plan approval.

---

## Stop here

After Action 3, you will have: (1) confirmed live state, (2) killed the Discord zombie and resolved WhatsApp's status, (3) a measured, honest list of what the clean base actually needs to carry over. Report those three outcomes and wait — the clean-checkout (Phase 0.1) and everything after is the approved plan's territory, not this bootstrap sequence.
