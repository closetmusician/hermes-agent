# Hermes Chief-of-Staff: File-Level Attack Plan

**Date:** 2026-06-12
**Hermes version:** v0.14.0 local (v0.16.0 upstream, 50 commits behind)
**Goal:** Full PM assistant -- Teams/Outlook/Slack monitoring, JIRA, PR reviews, deploy monitoring, calendar/task triage, draft responses, approval gates

---

## Current State Summary

### What Exists (working or near-working)

| Capability | Status | Location |
|---|---|---|
| Telegram gateway | Working (daily use) | `gateway/platforms/telegram.py` |
| Slack gateway | Built-in, mature (126KB adapter) | `gateway/platforms/slack.py` |
| **Teams gateway** | **Plugin exists** (v1.0.0, Bot Framework SDK) | `plugins/platforms/teams/adapter.py` (47KB) |
| Teams meeting pipeline | Plugin, Graph API | `plugins/teams_pipeline/` (7 files) |
| MS Graph webhook adapter | Built-in, change notifications | `gateway/platforms/msgraph_webhook.py` |
| MS Graph auth (app-only) | Working client+token provider | `tools/microsoft_graph_{auth,client}.py` |
| Email gateway (IMAP/SMTP) | Working (your Yahoo setup) | `gateway/platforms/email.py` |
| Cron scheduler | Full crontab + natural language | `cron/{scheduler,jobs}.py` |
| MCP integration | Full stdio/HTTP/SSE support | `tools/mcp_tool.py` (143KB) |
| Approval system | Pattern + LLM smart-approve | `tools/approval.py` (60KB) |
| Memory (persistent) | File-backed MEMORY.md + USER.md | `tools/memory_tool.py` |
| Subagent delegation | ThreadPool, isolated context | `tools/delegate_tool.py` (116KB) |
| GitHub code review skill | Bundled | `skills/github/github-code-review/` |
| Auxiliary LLM routing | Per-task provider+model overrides | `agent/auxiliary_client.py` |

### What Doesn't Exist Yet

| Capability | Gap |
|---|---|
| Outlook mail reading via Graph API | Only IMAP gateway exists; no Graph-based mail |
| JIRA integration | Zero references in codebase |
| Calendar (M365/Google) | No calendar tools |
| Deploy monitoring (Vercel/AWS/etc.) | No deploy tools |
| PR review scheduling | Has GitHub skill but no automated polling |
| Draft response with voice/style | No comms style engine |
| Task triage intelligence | No prioritization logic |

### PM-OS Scripts Available for Wiring

Your `~/Code/pm_os/bin/` has battle-tested scripts that could be MCP tools:

| Script | What it does | Integration path |
|---|---|---|
| `outlook-read-mail.js` | Read Outlook via Graph API | MCP server wrapping this |
| `teams-read-chats.js` | Read Teams chats via Graph API | MCP server wrapping this |
| `teams-read-channels.js` | Read Teams channels | MCP server wrapping this |
| `teams-send-chat.js` | Send Teams messages | MCP server wrapping this |
| `draft-responses.js` | Audience-aware response drafting | MCP server wrapping this |
| `classify-messages.js` | Classify by urgency/audience | Could port logic to Hermes skill |
| `run-morning.js` | Full morning triage pipeline | Reference for Hermes cron job |
| `run-weekly.js` | Weekly status pipeline | Reference for Hermes cron job |
| `graph-list-crud.js` | SharePoint list CRUD | MCP server wrapping this |
| `graph-workbook.js` | Excel read/write | MCP server wrapping this |
| `glean-search.js` | Enterprise search | MCP server wrapping this |

---

## Phase 0: Upgrade to v0.16.0 (Day 1)

### Why First

v0.16.0 adds: desktop app, web admin panel, security hardening, uncapped delegation depth, better compression, MCP bare-command resolution. 50 commits of stability fixes. Your local fork is 60 commits ahead (email integration work) and 1 commit behind origin/main after the latest tags.

### Steps

```bash
# 1. Stash your local email work
cd ~/Code/hermes
git stash push -m "email-integration-wip"

# 2. Fetch upstream
git fetch origin --tags

# 3. Create upgrade branch
git checkout -b upgrade/v0.16.0

# 4. Rebase onto v0.16.0
git rebase v2026.6.5

# 5. Resolve conflicts (expected in):
#    - pyproject.toml (version, setuptools>=77 requirement)
#    - .env.example (your email config additions)
#    - gateway/platforms/email.py (your email send guard work)
#    - tools/approval.py (your email send guard additions)
#    - plugins/ (your email-send-guard and control-room additions)
#
# 6. After rebase:
pip install -e ".[dev]"
python -m pytest tests/ -x --timeout=120

# 7. Pop email work
git stash pop
```

### Breaking Changes (v0.14.0 -> v0.16.0)

- `setuptools>=77` now required (`pyproject.toml` build-system)
- Docker `--insecure` is explicit env var, not inferred from bind host
- Default skill set trimmed (spotify, linear, etc. moved to optional)
- `HERMES_DASHBOARD_INSECURE=1` env var needed for non-loopback dashboard access

### Files to Check Post-Upgrade

| File | Why |
|---|---|
| `pyproject.toml` | Version bump, setuptools floor change |
| `gateway/config.py` | Platform enum may have new members |
| `agent/conversation_loop.py` | Compression changes |
| `tools/delegate_tool.py` | Uncapped spawn depth |
| `web/` | Entire admin panel is new |

---

## Phase 1: Teams + Slack Gateway (Days 2-4)

### 1A. Activate Teams Platform Plugin

The Teams adapter already exists at `plugins/platforms/teams/adapter.py`. It uses `microsoft-teams-apps` SDK with Bot Framework.

**Files to modify:**

1. **`~/.hermes/.env`** -- Add Teams credentials:
```bash
TEAMS_CLIENT_ID=<your-azure-ad-app-client-id>
TEAMS_CLIENT_SECRET=<your-azure-ad-client-secret>
TEAMS_TENANT_ID=<your-diligent-tenant-id>
TEAMS_ALLOWED_USERS=<your-aad-object-id>
TEAMS_HOME_CHANNEL=<default-chat-id-for-cron>
TEAMS_PORT=3978
```

2. **`~/.hermes/config.yaml`** -- Enable Teams platform:
```yaml
platforms:
  teams:
    enabled: true
    extra:
      port: 3978
```

3. **Azure AD App Registration** (external):
   - Register a Bot in Azure Bot Framework
   - Add messaging permissions
   - Set webhook endpoint to `https://<your-host>:3978/api/messages`
   - Requires public HTTPS endpoint (ngrok for dev, Cloudflare Tunnel for prod)

4. **Install dependency:**
```bash
pip install microsoft-teams-apps aiohttp
```

**Testing checklist:**
- [ ] `hermes gateway status` shows Teams as connected
- [ ] Send a DM to the bot in Teams, get a response
- [ ] Cron job delivers to `TEAMS_HOME_CHANNEL`
- [ ] Approval prompts render as Adaptive Cards

### 1B. Activate Slack Gateway

Already built-in. Just configure:

**`~/.hermes/.env`:**
```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_ALLOWED_USERS=<your-slack-user-id>
```

**Testing checklist:**
- [ ] Socket Mode connects
- [ ] DM responses work
- [ ] Thread support works
- [ ] Slash commands work

### 1C. Teams Meeting Pipeline (Already Built)

The `plugins/teams_pipeline/` plugin handles meeting transcription and summarization. Uses the same `MSGRAPH_*` credentials. Enable via:

```yaml
# config.yaml
platforms:
  msgraph_webhook:
    enabled: true
    extra:
      port: 8646
      webhook_path: /msgraph/webhook
      accepted_resources:
        - communications/callRecords
```

**Testing checklist:**
- [ ] `hermes teams-pipeline validate` passes
- [ ] `hermes teams-pipeline token-health` shows valid token
- [ ] Meeting notification triggers pipeline
- [ ] Summary is delivered to Teams

---

## Phase 2: Outlook + Calendar via MCP (Days 5-8)

### Strategy: Wrap PM-OS Scripts as MCP Servers

Rather than rebuilding Microsoft Graph integrations from scratch, wrap existing pm_os scripts as MCP servers. Hermes has full MCP support via `tools/mcp_tool.py`.

### 2A. Outlook Mail MCP Server

**New file:** `~/.hermes/mcp-servers/outlook-mail/server.js`

```javascript
// Thin MCP server wrapping pm_os outlook-read-mail.js + outlook-send-mail.js (draft only)
// Tools: read_inbox, read_mail, search_mail, list_folders
// Uses pm_os FOCI token infrastructure
```

**Wire into Hermes config:**

**`~/.hermes/config.yaml`:**
```yaml
mcp_servers:
  outlook-mail:
    command: "node"
    args: ["~/.hermes/mcp-servers/outlook-mail/server.js"]
    env:
      PM_OS_PATH: "~/Code/pm_os"
    timeout: 120
```

**Implementation approach:**
- Create a FastMCP (or bare @modelcontextprotocol/sdk) server that shells out to `node ~/Code/pm_os/bin/outlook-read-mail.js`
- Parse its JSON output and return structured tool results
- Read-only initially; draft-only sends go through approval
- ~200 lines for the MCP wrapper

**Files to create:**
| File | Purpose |
|---|---|
| `~/.hermes/mcp-servers/outlook-mail/server.js` | MCP server wrapper |
| `~/.hermes/mcp-servers/outlook-mail/package.json` | Dependencies |

### 2B. Teams Chat MCP Server

**New file:** `~/.hermes/mcp-servers/teams-chat/server.js`

Tools: `read_chats`, `read_channels`, `search_messages`, `send_chat`

Same pattern -- wrap `teams-read-chats.js`, `teams-read-channels.js`, `teams-send-chat.js`.

```yaml
mcp_servers:
  teams-chat:
    command: "node"
    args: ["~/.hermes/mcp-servers/teams-chat/server.js"]
    env:
      PM_OS_PATH: "~/Code/pm_os"
    timeout: 180
```

### 2C. Calendar MCP Server

No pm_os calendar scripts exist. Options:

1. **Use the Claude.ai Google Calendar MCP** (already installed in Claude Code -- `mcp__claude_ai_Google_Calendar`)
2. **Build M365 Calendar MCP** using MS Graph `/me/calendarView` endpoints
3. **Use Composio's Microsoft Calendar MCP** (community, pre-built)

**Recommended: Build a thin M365 Calendar MCP** using the existing `tools/microsoft_graph_client.py` patterns.

**New file:** `~/.hermes/mcp-servers/m365-calendar/server.js`

Tools: `list_events_today`, `list_events_range`, `create_event`, `accept_event`, `decline_event`, `get_free_busy`

### 2D. Glean Enterprise Search MCP Server

**New file:** `~/.hermes/mcp-servers/glean/server.js`

Wrap `~/Code/pm_os/bin/glean-search.js`.

Tools: `search`, `get_document`

```yaml
mcp_servers:
  glean:
    command: "node"
    args: ["~/.hermes/mcp-servers/glean/server.js"]
    env:
      PM_OS_PATH: "~/Code/pm_os"
    timeout: 60
```

**Testing checklist (Phase 2):**
- [ ] `hermes` shows Outlook, Teams Chat, Calendar, Glean tools in tool list
- [ ] "Read my latest emails" works via Outlook MCP
- [ ] "What meetings do I have today?" works via Calendar MCP
- [ ] "Search for <topic> on Glean" works
- [ ] Teams chat read/send works

---

## Phase 3: JIRA Integration (Days 9-11)

### Strategy: Atlassian MCP or Direct REST

Two paths:

**Option A (faster): Install community JIRA MCP**
```bash
hermes mcp install jira  # if available in MCP catalog
```

**Option B (more control): Wrap pm_os Atlassian scripts**

Your pm_os already has Atlassian REST via `pm-jira` skill (uses curl). Build an MCP wrapper.

**New file:** `~/.hermes/mcp-servers/jira/server.js`

Tools: `search_issues`, `get_issue`, `add_comment`, `transition_issue`, `create_issue`, `list_my_issues`, `get_sprint`

**`~/.hermes/config.yaml`:**
```yaml
mcp_servers:
  jira:
    command: "node"
    args: ["~/.hermes/mcp-servers/jira/server.js"]
    env:
      JIRA_BASE_URL: "https://diligent.atlassian.net"
      JIRA_EMAIL: "yklin@diligent.com"
      JIRA_API_TOKEN: "<your-token>"
    timeout: 120
```

### JIRA Skill for Hermes

**New file:** `~/.hermes/skills/jira-triage/SKILL.md`

This skill tells Hermes HOW to use the JIRA tools:
- Morning triage: fetch assigned issues, classify by priority
- Update tickets with implementation details
- Create tickets from conversation
- Weekly sprint status

**Testing checklist:**
- [ ] "Show my JIRA tickets" returns real data
- [ ] "Update PROJ-123 with <details>" adds comment
- [ ] "Create a ticket for <description>" works
- [ ] Cron job for daily JIRA digest works

---

## Phase 4: Morning Triage Cron Job (Days 12-14)

### Strategy: Hermes Native Cron + Skill

Build a `chief-of-staff-morning` skill that runs as a scheduled cron job.

**New file:** `~/.hermes/skills/chief-of-staff-morning/SKILL.md`

```markdown
---
name: chief-of-staff-morning
description: "Daily morning triage -- scan Teams, Outlook, JIRA, calendar. Classify, prioritize, draft responses."
version: 1.0.0
---

# Chief of Staff Morning Triage

1. Read today's calendar (Calendar MCP)
2. Scan unread Outlook emails since last triage (Outlook MCP)
3. Scan Teams mentions/DMs (Teams Chat MCP)
4. Scan JIRA assignments and @mentions (JIRA MCP)
5. Classify each item: ACTION_REQUIRED / FYI / NOISE
6. Prioritize by: sender seniority + urgency + age
7. Draft responses for ACTION_REQUIRED items
8. Deliver summary via Teams (or Telegram)
```

**Create the cron job:**
```bash
hermes cronjob create \
  --prompt "Run morning triage" \
  --skill chief-of-staff-morning \
  --schedule "0 7 * * 1-5" \
  --deliver telegram \
  --name "morning-triage"
```

### Weekly Summary Cron

**New file:** `~/.hermes/skills/chief-of-staff-weekly/SKILL.md`

Similar pattern. Runs Friday afternoon. Summarizes the week's comms, outstanding items, decisions made.

```bash
hermes cronjob create \
  --prompt "Run weekly summary" \
  --skill chief-of-staff-weekly \
  --schedule "0 16 * * 5" \
  --deliver telegram \
  --name "weekly-summary"
```

**Testing checklist:**
- [ ] Manual trigger: `hermes cronjob trigger morning-triage`
- [ ] Output includes calendar, emails, Teams, JIRA items
- [ ] Items are classified and prioritized
- [ ] Draft responses are reasonable
- [ ] Delivery to Telegram/Teams works
- [ ] Scheduled execution fires on time

---

## Phase 5: PR Review + Deploy Monitoring (Days 15-18)

### 5A. Automated PR Review

The `github-code-review` skill exists. Wire it to a cron job.

**New file:** `~/.hermes/skills/pr-monitor/SKILL.md`

```markdown
1. List open PRs assigned to me (gh pr list --assignee @me)
2. For each PR not yet reviewed:
   a. Read the diff
   b. Run github-code-review skill
   c. Post review findings to Teams
3. List PRs I authored waiting for review -- nudge if stale
```

```bash
hermes cronjob create \
  --prompt "Check for PRs needing review" \
  --skill pr-monitor \
  --schedule "0 9,14 * * 1-5" \
  --deliver teams \
  --name "pr-review-check"
```

### 5B. Deploy Monitoring

**New file:** `~/.hermes/mcp-servers/deploy-monitor/server.js`

Tools: `list_deployments`, `get_deployment_status`, `list_recent_deploys`

Implementation depends on deployment platform (Vercel, AWS, Azure DevOps). Start with the one you use.

### 5C. PR Review with Approval Gate

Hermes's approval system already supports this. For PR review comments that actually post to GitHub:

```yaml
# config.yaml
approvals:
  mode: smart  # LLM-assisted auto-approve for safe operations
```

The existing `tools/approval.py` handles:
- `manual` -- always ask (current setting)
- `smart` -- LLM assesses risk, auto-approves safe commands
- `off` / YOLO -- auto-approve everything

For PR review actions specifically, keep `manual` mode but add the GitHub PR comment tool to `command_allowlist` in config.yaml for auto-approval of read-only operations.

---

## Phase 6: Model Routing Configuration (Ongoing)

### Current Model Architecture

Hermes uses a **single main model** with **per-task auxiliary overrides**:

```yaml
# ~/.hermes/config.yaml
model:
  default: deepseek/deepseek-v4-flash  # Main agent model
  provider: openrouter

auxiliary:
  vision:
    provider: openrouter
    model: deepseek/deepseek-v4-flash:free
  web_extract:
    provider: openrouter
    model: <model>
  compression:
    summary_model: deepseek/deepseek-v4-flash:free
  curator:
    provider: openrouter
    model: <model>
  approval:
    # Smart-approve uses call_llm with task="approval"
    # Falls through auxiliary resolution chain
```

### Recommended Model Routing

```yaml
model:
  default: anthropic/claude-sonnet-4  # Main agent: good enough for most tasks
  provider: openrouter

auxiliary:
  # Cheap tasks -- DeepSeek Flash (free or near-free)
  vision:
    provider: openrouter
    model: deepseek/deepseek-v4-flash:free
  web_extract:
    provider: openrouter
    model: deepseek/deepseek-v4-flash:free
  compression:
    summary_model: deepseek/deepseek-v4-flash:free
  approval:
    provider: openrouter
    model: deepseek/deepseek-v4-flash:free
  curator:
    provider: openrouter
    model: anthropic/claude-sonnet-4
```

### When to Use Expensive Models (Fable/Opus Moments)

| Task | Model | Why | Cost/MTok |
|---|---|---|---|
| Morning triage classification | Sonnet 4 | Good enough -- classify/prioritize is straightforward | $3/$15 |
| Draft email responses | Sonnet 4 | Adequate for business writing | $3/$15 |
| PR code review (complex) | **Opus 4** | Needs deep code understanding, architectural reasoning | $15/$75 |
| JIRA ticket decomposition | Sonnet 4 | Template-driven, Sonnet handles it | $3/$15 |
| Approval risk assessment | Flash (free) | Binary classification, trivial | ~$0 |
| Context compression | Flash (free) | Summarization is a commodity task | ~$0 |
| Weekly synthesis/strategic summary | **Opus 4** | Connects dots across sources, requires judgment | $15/$75 |
| Deploy incident triage | **Opus 4** | Root cause analysis is genuinely hard | $15/$75 |
| Calendar conflict resolution | Sonnet 4 | Structured decision, Sonnet sufficient | $3/$15 |
| Routine Teams message reading | Flash (free) | Extraction, not reasoning | ~$0 |

**Hermes does NOT support per-tool or per-skill model routing natively.** The main agent model is used for all tool-calling turns. Auxiliary overrides only apply to specific background tasks (compression, vision, etc.).

**Workaround for model routing:** Use the delegate tool. Each subagent can be spawned with a different model. Create a "triage" skill that delegates expensive analysis to Opus subagents while the main agent runs on Sonnet.

### Config for subagent model override:

The delegate tool picks up the main model by default. To override per-delegation:

```python
# In the skill instructions, tell the agent:
# "For complex PR reviews, delegate with model override:
# delegate_task(goal='Review this PR diff', model='anthropic/claude-opus-4')"
```

The `delegate_tool.py` supports model override via the curator's `_ReviewRuntimeBinding` pattern, which reads `auxiliary.<task>.{provider,model}` from config.

---

## Phase 7: Approval Gates for PM Actions (Days 19-20)

### Existing Approval Architecture

`tools/approval.py` has three layers:

1. **Hardline blocklist** -- unconditional blocks (rm -rf /, fork bombs, shutdown)
2. **Dangerous patterns** -- require approval (rm -rf, DROP TABLE, git push --force)
3. **Smart approve** -- auxiliary LLM assesses risk, auto-approves safe commands

### Adding PM-Specific Approval Rules

For chief-of-staff actions, add custom approval patterns:

**File to modify:** `~/.hermes/config.yaml`

```yaml
approvals:
  mode: smart  # Enable LLM-assisted auto-approve

command_allowlist:
  # Auto-approve read-only operations
  - "outlook read"
  - "teams read"
  - "jira search"
  - "calendar list"
  - "gh pr list"
```

For **write operations** (send email, post comment, transition JIRA), keep them behind manual approval. The gateway handles this: when Hermes wants to send an email, the approval prompt appears in Telegram/Teams as an inline button.

### Approval Flow

```
Agent wants to send email
  → approval.py detects "send email" pattern
  → Sends approval prompt to gateway (Teams Adaptive Card or Telegram inline keyboard)
  → User taps Approve/Deny
  → Agent proceeds or aborts
```

No code changes needed -- this is how the existing system works. Just configure the patterns.

---

## Phase 8: Self-Evolution Integration (Days 21-25)

### Current State of hermes-evolution

- **Phase 1 (skill evolution) is functional** -- DSPy 3.2.1 + GEPA installed
- 8 commits, last activity was switching to DeepSeek models
- Phase 1 validation report exists (PDF)
- Dataset builders, fitness functions, constraint validators all implemented

### Evolution Targets for Chief-of-Staff

Once the chief-of-staff skills are built (Phases 4-5), evolve them:

```bash
cd ~/Code/hermes-evolution

# Evolve the morning triage skill
python -m evolution.skills.evolve_skill \
  --skill chief-of-staff-morning \
  --iterations 10 \
  --eval-source synthetic \
  --optimizer-model deepseek/deepseek-v4-pro \
  --eval-model deepseek/deepseek-v4-flash:free

# Evolve the PR review skill
python -m evolution.skills.evolve_skill \
  --skill pr-monitor \
  --iterations 10 \
  --eval-source sessiondb  # Use real usage data
```

### Metrics to Track

- Triage accuracy (% correctly classified as ACTION/FYI/NOISE)
- Draft response quality (LLM-as-judge score)
- False positive rate on approval bypass
- Time-to-first-response on critical items

---

## Architecture Diagram

```
                    ┌──────────────────────────────────────────┐
                    │          Hermes Gateway                  │
                    │                                          │
  ┌─────────┐      │  ┌──────────┐  ┌────────┐  ┌─────────┐ │
  │ Teams   ├──────┼──┤ Teams    │  │ Slack  │  │Telegram │ │
  │ (Bot FW)│      │  │ Adapter  │  │Adapter │  │ Adapter │ │
  └─────────┘      │  └────┬─────┘  └───┬────┘  └────┬────┘ │
                    │       │            │            │       │
                    │       └────────────┼────────────┘       │
                    │                    │                     │
                    │            ┌───────▼────────┐           │
                    │            │   AIAgent      │           │
                    │            │  (Sonnet 4)    │           │
                    │            └───────┬────────┘           │
                    │                    │                     │
                    │    ┌───────────────┼──────────────┐     │
                    │    │               │              │     │
                    │ ┌──▼───┐  ┌───────▼──────┐ ┌────▼───┐ │
                    │ │ MCP  │  │   Built-in   │ │Delegate│ │
                    │ │Tools │  │   Tools      │ │ Tool   │ │
                    │ └──┬───┘  └──────────────┘ └────┬───┘ │
                    │    │                            │     │
                    └────┼────────────────────────────┼─────┘
                         │                            │
          ┌──────────────┼──────────────┐    ┌───────▼────────┐
          │              │              │    │ Subagent       │
    ┌─────▼───┐  ┌──────▼──┐  ┌───────▼┐   │ (Opus 4 for   │
    │Outlook  │  │Teams    │  │Calendar│   │  complex PR    │
    │Mail MCP │  │Chat MCP │  │MCP     │   │  review)       │
    └────┬────┘  └────┬────┘  └───┬────┘   └────────────────┘
         │            │           │
    ┌────▼────┐  ┌────▼────┐  ┌──▼────────┐
    │pm_os    │  │pm_os    │  │MS Graph   │
    │outlook- │  │teams-   │  │/calendarV │
    │read-    │  │read-    │  │           │
    │mail.js  │  │chats.js │  └───────────┘
    └─────────┘  └─────────┘

    ┌─────────────────────────────────────────┐
    │           Cron Scheduler                 │
    │                                          │
    │  07:00 M-F  chief-of-staff-morning      │
    │  09:00,14:00 M-F  pr-monitor            │
    │  16:00 Fri  chief-of-staff-weekly       │
    └─────────────────────────────────────────┘
```

---

## Execution Order and Dependencies

```
Phase 0: Upgrade v0.14.0 → v0.16.0         [Day 1]
    │
    ├── Phase 1A: Activate Teams gateway     [Day 2-3]
    ├── Phase 1B: Activate Slack gateway     [Day 2]
    └── Phase 1C: Teams meeting pipeline     [Day 3-4]
         │
         ├── Phase 2A: Outlook Mail MCP      [Day 5-6]
         ├── Phase 2B: Teams Chat MCP        [Day 6-7]
         ├── Phase 2C: Calendar MCP          [Day 7-8]
         └── Phase 2D: Glean MCP             [Day 8]
              │
              ├── Phase 3: JIRA MCP          [Day 9-11]
              │
              └── Phase 4: Morning triage    [Day 12-14]
                   │
                   ├── Phase 5A: PR monitor  [Day 15-16]
                   ├── Phase 5B: Deploy mon  [Day 17-18]
                   │
                   ├── Phase 6: Model routing [Ongoing]
                   ├── Phase 7: Approval gates [Day 19-20]
                   └── Phase 8: Self-evolve   [Day 21-25]
```

---

## Risk Assessment

### High Risk

1. **Azure AD App Registration complexity** -- Teams Bot Framework requires public HTTPS endpoint, app registration, admin consent for Graph permissions. This is the hardest single prerequisite. The msgraph_webhook adapter already needs this but for different permissions (CallRecords vs Chat.ReadWrite).

2. **FOCI token dependency** -- PM-OS scripts rely on `get-foci-token.js` for delegated user tokens. Hermes's built-in `microsoft_graph_auth.py` uses app-only tokens (client credentials flow). You may need both: app-only for webhook notifications, delegated for reading user's mailbox/calendar.

3. **Upgrade rebase conflicts** -- 60 commits ahead, 50 commits diverged. Your email integration work touches `tools/approval.py`, `gateway/platforms/email.py`, and `plugins/` -- all areas with upstream changes.

### Medium Risk

4. **MCP server reliability** -- Wrapping shell scripts as MCP servers adds a process boundary. Timeouts, crashes, and token expiration all need handling. Hermes's MCP tool has retry and reconnection logic but you'll need to test it.

5. **Cron approval deadlock** -- Config currently says `approvals.cron_mode: deny`. This blocks ALL dangerous commands in cron jobs. Chief-of-staff cron jobs that invoke MCP tools calling Graph API may trip false-positive patterns. Change to `cron_mode: smart` or carefully allowlist.

### Low Risk

6. **Slack integration** -- Built-in, mature, well-tested. Just credential setup.
7. **Memory persistence** -- File-backed, works across sessions. May need to add chief-of-staff context to MEMORY.md.
8. **Self-evolution** -- Additive improvement, no risk to working system.

---

## Files Created/Modified Summary

### New Files

| File | Phase | Description |
|---|---|---|
| `~/.hermes/mcp-servers/outlook-mail/server.js` | 2A | Outlook MCP server |
| `~/.hermes/mcp-servers/outlook-mail/package.json` | 2A | Dependencies |
| `~/.hermes/mcp-servers/teams-chat/server.js` | 2B | Teams Chat MCP server |
| `~/.hermes/mcp-servers/teams-chat/package.json` | 2B | Dependencies |
| `~/.hermes/mcp-servers/m365-calendar/server.js` | 2C | Calendar MCP server |
| `~/.hermes/mcp-servers/m365-calendar/package.json` | 2C | Dependencies |
| `~/.hermes/mcp-servers/glean/server.js` | 2D | Glean search MCP server |
| `~/.hermes/mcp-servers/glean/package.json` | 2D | Dependencies |
| `~/.hermes/mcp-servers/jira/server.js` | 3 | JIRA MCP server |
| `~/.hermes/mcp-servers/jira/package.json` | 3 | Dependencies |
| `~/.hermes/skills/chief-of-staff-morning/SKILL.md` | 4 | Morning triage skill |
| `~/.hermes/skills/chief-of-staff-weekly/SKILL.md` | 4 | Weekly summary skill |
| `~/.hermes/skills/pr-monitor/SKILL.md` | 5A | PR review monitor skill |
| `~/.hermes/mcp-servers/deploy-monitor/server.js` | 5B | Deploy monitoring MCP |

### Modified Files

| File | Phase | Change |
|---|---|---|
| `~/.hermes/.env` | 0-3 | Add Teams, Slack, MSGRAPH, JIRA credentials |
| `~/.hermes/config.yaml` | 1-7 | Platforms, MCP servers, model routing, approvals |
| `~/Code/hermes/pyproject.toml` | 0 | Version bump during upgrade |

---

## Token Economics Estimate

### Monthly Cost at Steady State

| Task | Frequency | Model | Tokens/run | Monthly cost |
|---|---|---|---|---|
| Morning triage | 22x/month | Sonnet 4 | ~50K in + 10K out | ~$4.60 |
| Weekly summary | 4x/month | Opus 4 | ~100K in + 20K out | ~$12.00 |
| PR review | 44x/month | Sonnet/Opus mix | ~30K avg | ~$8.00 |
| Ad-hoc queries | ~60x/month | Sonnet 4 | ~20K avg | ~$6.00 |
| Approval assessment | ~100x/month | Flash (free) | ~2K avg | ~$0 |
| Compression | ~30x/month | Flash (free) | ~10K avg | ~$0 |
| **Total** | | | | **~$30-40/month** |

This is dominated by Sonnet 4 as the main agent and Opus 4 for the genuinely hard tasks (weekly synthesis, complex PR reviews). Flash handles all the commodity work for free.

---

## Decision Points Requiring User Input

1. **Azure AD app registration** -- Need tenant admin consent for Graph API permissions (Mail.Read, Calendars.Read, Chat.Read, etc.). Does your Diligent tenant allow this, or do you need IT approval?

2. **Public endpoint for Teams Bot Framework** -- Need HTTPS webhook. Cloudflare Tunnel? ngrok? VPS? Existing infrastructure?

3. **FOCI vs app-only tokens** -- PM-OS uses FOCI (delegated user tokens from desktop Office). Hermes uses app-only (client credentials). Do you want to migrate PM-OS scripts to app-only, or add FOCI support to Hermes?

4. **Deploy monitoring platform** -- Vercel? Azure DevOps? AWS? GitHub Actions? Need to know before building the MCP server.

5. **Upgrade strategy** -- Rebase 60 commits onto v0.16.0, or fresh clone + cherry-pick email work?
