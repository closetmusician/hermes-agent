# Phase 0 — Live Repo State Snapshot (2026-07-03, ~11:15 BST)

All facts below verified against the live machine at snapshot time unless marked `[UNVERIFIED]`.

## Git

| Item | Value |
|---|---|
| Branch | `feat/governance-plugins`, tracks `fork/feat/governance-plugins` (closetmusician/hermes-agent) |
| Upstream remote | `origin` = NousResearch/hermes-agent |
| Delta vs origin/main | **61 ahead / ~5,000 behind** (5,010 total / 4,085 first-parent, as of 2026-07-03; re-verify) |
| v0.17.0 tag | **Does not exist.** Upstream uses date tags; latest fetched: `v2026.7.1`. The "v0.17.0" naming in planning docs refers to an upstream version label, not a git tag. |
| Local base version | Hermes Agent **v0.14.0 (2026.5.16)** per `hermes --version` |
| Working tree | Clean of modifications; 9 untracked items (planning docs, `CLAUDE.md.proposed`, `hermes-evolution/`, `.claude/worktrees/`, `.claude/settings-fix-note.md`) |
| Recent commit themes | Last ~20 commits are entirely email-guard fixes, gateway slash-command dispatch, IMAP IDLE backoff, mandatory-plugin security enforcement, worktree-agent merges |

## Python environment

| Item | Value |
|---|---|
| CLI | `/opt/homebrew/bin/hermes` — **works**, reports v0.14.0, project `/Users/yklin/Code/hermes`, Python 3.11.15 |
| venv | `venv/` (created 2026-05-18); `venv/bin/python -c "import yaml"` → OK. The historical `ModuleNotFoundError: yaml` is **fixed**. |
| System python3 | `import hermes` fails (expected — package lives in venv) |

## Gateway

| Item | Value |
|---|---|
| launchd | `ai.hermes.gateway` loaded, `RunAtLoad` + `KeepAlive`, runs `venv/bin/python -m hermes_cli.main gateway run --replace`, `HERMES_HOME=~/.hermes` |
| Current process | PID 3251, started 09:55 UTC today, up ~21 min at check, rss=160MB, stable memory-monitor ticks |
| **Crashes TODAY** | 3 nonzero exits on 2026-07-03: 06:32, 06:47, 06:50 UTC (`asyncio.run.returned success:false` → `gateway.exit_nonzero`), each restarted by KeepAlive |
| Crash context | 07:49–07:50 UTC: DNS resolution dead (`[Errno 8] nodename nor servname provided`) — Telegram primary + fallback-IP SSL handshake fail, WhatsApp timeout, email IMAP fail, Discord no token. Gateway logged "started with no connected platforms — 4 queued for retry", then received SIGTERM. **loadavg_1m=23.79** at shutdown — the machine itself was under extreme load. |
| Platform status (now) | Telegram: connected `[UNVERIFIED — no recent error, assumed up]`. Discord: **permanent retry loop, no bot token configured** (configured-but-unconfigured zombie). WhatsApp: **permanent retry loop**, bridge.js found but connect times out every 300s. Email: IMAP connected `[UNVERIFIED]`. |

## Plugins (repo `plugins/`)

Governance (local additions): `control-room/`, `email-send-guard/`, `tool-registry-guard/`, `teams_pipeline/`.
Upstream/other: browser, context_engine, disk-cleanup, example-dashboard, google_meet, hermes-achievements, image_gen, kanban, memory, model-providers, observability, platforms, spotify, video_gen, web.
State dirs exist in `~/.hermes/` for control-room, email-send-guard, tool-registry-guard → plugins are active. `[UNVERIFIED: whether all three load cleanly at gateway start]`

## Cron

3 jobs defined in `~/.hermes/cron/jobs.json`, all enabled, all firing (session files present through today):

| Job | Schedule | Note |
|---|---|---|
| `morning-briefing` | `0 8 * * 1-5` | Chief-of-staff triage; shells out to `~/Code/pm_os/bin/run-morning.js` |
| `weekly-status` | `0 16 * * 5` | |
| `pr-monitor` | `0 */4 * * 1-5` | Ran today 04:00 |

## Config

- `.env` (repo): OpenRouter key, Telegram (bot token + allowed users), Discord token line present **but adapter says "No bot token configured"** — token empty or not exported `[UNVERIFIED which]`, WhatsApp enabled, Yahoo email (IMAP/SMTP + poll interval + allowed users).
- `cli-config.yaml.example` present (57KB); no `cli-config.yaml` in repo root; no `SOUL.md`.
- `AGENTS.md` 53KB (2026-05-24). `CLAUDE.md.proposed` awaiting approval (2026-07-03).
- Logs: `~/.hermes/logs/` — gateway.log 2.2MB, agent.log 3.1MB, gateway.error.log 3.5MB, errors.log 1.8MB + rotated, plus `gateway-exit-diag.log` and `gateway-shutdown-diag.log` (instrumentation added during crash debugging; shutdown diags show SIGTERM under launchd with parent_pid=1).

## Immediate observations feeding the diagnosis

1. Gateway instability is **not historical — it crashed 3× this morning**, correlated with machine-level DNS outage and loadavg ~24.
2. Discord is a misconfigured zombie platform: enabled with no token, generating an error every 5 minutes forever.
3. WhatsApp is in the same permanent-retry state.
4. The email-guard work (last 13 commits) is the most recent battlefield; branch has never been merged anywhere.
5. Fork divergence is worse than planning docs state: ~5,000 commits behind upstream main (docs said "50+").
