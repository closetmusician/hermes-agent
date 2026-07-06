<!-- ABOUTME: pmos-capability-inventory.md - Complete capability inventory of ~/Code/pm_os.
     ABOUTME: Maps every bin/ tool, pipeline, auth model, tool-registry conventions, and skills.
     ABOUTME: Source of truth for the chief-of-staff capability completeness matrix.
     ABOUTME: Generated 2026-07-03 by Research Track A from read-only source inspection.
     ABOUTME: Audience: orchestrator agent planning a chief-of-staff assistant harness. -->

# PM-OS Capability Inventory
> Generated: 2026-07-03  
> Source: Read-only inspection of ~/Code/pm_os (README, CLAUDE.md, all SKILL.md files, all bin/ ABOUTME headers, config, lib, state)

---

## 1. Complete `bin/` Tool Inventory

49 files total. Grouped by service area.

### 1.1 Auth / Token Management (9 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `ensure-tokens.js` | Autonomous token lifecycle manager for scheduled runs | Checks freshness; refreshes FOCI non-interactively; extracts Chat via headless browser; opens headed browser for MFA if needed | Reads `~/.secrets/pm-os/*.json.enc`; `~/.age/pm-os.key` for decryption; `agent-browser` for Chat token extraction | **Y** — writes encrypted token store; sends macOS notifications |
| `foci-device-login.js` | Device Code Flow OAuth for 90-day rolling FOCI tokens | `--status` (read-only health), `--refresh` (rotate RT), `--skype-exchange` (chain FOCI→authsvc for skypeToken), `--office-exchange` (cross-pollinate Office Desktop client) | `login.microsoftonline.com` reachable; `~/.age/pm-os.key` | **Y** — writes/rotates `~/.secrets/pm-os/foci-token.json.enc` and `foci-office-token.json.enc`; **DESTRUCTIVE**: each exchange invalidates prior RT |
| `get-foci-token.js` | Scope-to-token router; outputs `access_token` to stdout | `--scope <scope>` or `--for sharepoint\|chat-read\|directory\|files\|mail` | Encrypted token store present and fresh | **Y (audit only)** — writes `~/.pm-os/audit-log.jsonl`; hard-denies `Sites.FullControl.All` |
| `check-token-health.js` | Health check for Chat + FOCI tokens | `--json`, `--quiet`; exit 0/1/2 | Only reads token store | **N** — read-only |
| `extract-teams-token.js` | Extracts Graph API bearer token from Teams MSAL localStorage | `--json` | Active Teams SSO session in agent-browser pm-os profile | **Y** — writes `~/.secrets/pm-os/teams-token.json.enc` |
| `extract-teams-chat-token.js` | Obtains skypeToken via FOCI exchange (zero-browser) or browser fallback | (standalone) | FOCI token or active Teams SSO | **Y** — writes `~/.secrets/pm-os/teams-chat-token.json.enc` |
| `extract-teams-token-leveldb.js` | Extracts Teams tokens from desktop app Cookies DB (no browser needed) | (standalone) | Teams desktop app running; macOS Keychain Safe Storage access | **Y** — writes `~/.secrets/pm-os/teams-chat-token.json.enc` |
| `extract-outlook-token.js` | Extracts Outlook REST API bearer token from OWA MSAL | `--json` | Active OWA SSO session | **Y** — writes `~/.secrets/pm-os/outlook-token.json.enc` |
| `extract-all-tokens.js` | Extracts Skype/Chat token from Teams localStorage; chains to authsvc | `--json` | Active Teams SSO session | **Y** — writes `teams-chat-token` to encrypted store |

### 1.2 Teams Read (2 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `teams-read-chats.js` | Reads Teams 1:1 and group chat messages via native skype API | `--with <email>`, `--chat-id <id>`, `--list-chats`, `--scan-recent`, `--since Nd`, `--json` | `~/.secrets/pm-os/teams-chat-token.json.enc` (skypeToken) | **N** — read-only |
| `teams-read-channels.js` | Reads Teams channel messages via Skype chatService API | `--team`, `--channel`, `--since`, `--top`, `--json`, `--probe` | skypeToken + Graph API token; falls back to Graph API beta | **N** — read-only |

### 1.3 Teams Write (1 tool)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `teams-send-chat.js` | Sends Teams 1:1 chat message via Microsoft Graph API | `--to <email>`, `--body <text>`, `--chat-id <id>` | FOCI token (`foci-token.json.enc`) with `ChatMessage.Send` scope | **Y** — sends Teams message |

### 1.4 Outlook (2 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `outlook-read-mail.js` | Reads Outlook email via Graph API | `--list`, `--search <q>`, `--from <email>`, `--read <id>`, `--folder`, `--conversation-id`, `--since`, `--unread`, `--json` | FOCI token with `Mail.ReadWrite` scope | **N** — read-only |
| `outlook-send-mail.js` | Sends, replies-all, or drafts emails via Graph API | `--to`, `--subject`, `--body`, `--reply-to-subject`, `--draft`, `--content-type HTML` | FOCI token with `Mail.Send` + `Mail.ReadWrite` | **Y** — sends or drafts in Outlook |

### 1.5 SharePoint / OneDrive / Office Files (10 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `graph-workbook.js` | Excel read/write/add-row/list-sheets/used-range via Graph Workbook API | `--url`, `--action read\|write\|add-row\|list-sheets\|list-tables\|used-range`, `--sheet`, `--range`, `--values`, `--table`, `--confirm` | FOCI token (`Files.ReadWrite.All`) | **Y** — writes Excel cells; requires `--confirm`; audit logged |
| `graph-file-ops.js` | Download/upload/checkout/checkin SharePoint/OneDrive files | `--url`, `--output`, `--file`, `--dest`, `--etag`, `--conflict`; actions: download, upload, checkout, checkin | FOCI token (`Files.ReadWrite.All`) | **Y** — writes files to SharePoint; requires `--confirm`; audit logged |
| `graph-list-crud.js` | SharePoint List CRUD | `--site`, `--list`, `--filter`, `--fields`, `--item-id`; actions: find-site, list-lists, read, create, update, delete | FOCI token (`Sites.ReadWrite.All`) | **Y** — mutates SharePoint lists; requires `--confirm`; audit logged |
| `graph-org-chart.js` | Org chart queries via Microsoft Graph Directory | `--query manager\|reports\|profile\|chain`, `--user <email>`, `--json` | Office Desktop FOCI token (`Directory.Read.All`) | **N** — read-only |
| `sp-checkout.js` | SharePoint REST checkout/checkin with version control | `--url`, `--version major\|minor\|overwrite`, `--comment`; actions: checkout, checkin, undo, status | FOCI token (SP-scoped) | **Y** — locks/unlocks SharePoint files; audit logged |
| `office-bridge-command.js` | Posts Office.js bridge commands to local PM-OS taskpane queue | Batched command JSON to Word/PowerPoint in-session | Requires pm-os-bridge add-in loaded in Office | **Y** — modifies open Office documents in-session |
| `office-browser-edit.js` | Conservative Office Online browser fallback for small Word/PPT edits | `--action verify-visible-text\|word-insert-after-anchor\|ppt-insert-or-replace-text`, `--dry-run` | `~/.agent-browser/pm-os` profile with active Office Online SSO | **Y** (non-dry-run) — modifies Office Online documents |
| `onedrive-bulk-download.js` | Bulk download OneDrive for Business files | `--output <dir>`, `--skip <patterns>`, `--dry-run` | FOCI token (`Files.ReadWrite.All`) | **Y (local only)** — writes to local disk, does not mutate OneDrive |
| `graph-edit-pptx.js` | Orchestrator for complex PPT editing (download → pptx-edit.py → upload) | `--url`, `--action` + pptx-edit args | FOCI token + `uv` + `python-pptx` | **Y** — reuploads modified PPTX to SharePoint |
| `ooxml-surgery.py` | Word/PPT/Excel XML editor via Graph download/upload | `--url`, `--action extract-text\|find-replace\|bulk-replace\|comment-*\|xpath-edit`, `--old`, `--new`, `--slide`, `--pairs` | FOCI token; `uv run --with lxml` | **Y** — reuploads modified Office document to SharePoint; audit logged |
| `pptx-edit.py` | Complex PowerPoint editing via python-pptx (charts, images, tables, slides) | `--file`, `--output`, `--action`, `--slide`, `--data` | `uv run --with python-pptx`; run locally or via graph-edit-pptx.js | **Y (local file)** — writes local PPTX; upload is separate |

### 1.6 Morning Pipeline (4 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `run-morning.js` | 9-step async pipeline orchestrator for morning triage | `--since Nd`, `--dry-run`, `--skip-classify`, `--skip-dedup`, `--config <path>` | `ANTHROPIC_API_KEY`; FOCI + Chat tokens; `bin/ensure-tokens.js` runs automatically | **Y** — writes state/*, drafts/*, reports/* |
| `classify-messages.js` | Classifies messages via Anthropic haiku API | (called by run-morning.js; stdin JSON or file) | `ANTHROPIC_API_KEY`; `CLASSIFY_CONCURRENCY` env (default 8) | **Y** — writes classified JSON |
| `draft-responses.js` | Generates audience-aware draft responses via Anthropic sonnet API | (called by run-morning.js) | `ANTHROPIC_API_KEY`; FOCI token (Outlook draft push) | **Y** — writes draft .md files; optionally pushes to Outlook Drafts folder |
| `refresh-draft-context.js` | Refreshes pending draft context by re-fetching live data + LLM regeneration | `--date YYYY-MM-DD` | `ANTHROPIC_API_KEY`; FOCI + Chat tokens | **Y** — updates existing draft files in-place |

### 1.7 Weekly Pipeline (5 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `run-weekly.js` | 6-step async pipeline orchestrator for weekly email | `--since Nd`, `--dry-run`, `--skip-extract`, `--skip-dedup`, `--config <path>` | `ANTHROPIC_API_KEY`; FOCI + Chat tokens | **Y** — writes state/*, reports/* |
| `extract-evidence.js` | Extracts structured evidence from Teams/Outlook API output via haiku tool_use | (called by run-weekly.js) | `ANTHROPIC_API_KEY` | **Y** — writes evidence JSON |
| `dedup-evidence.js` | Deterministic evidence deduplication using thread IDs + Jaccard similarity | (called by run-weekly.js) | None (zero npm, no API calls) | **Y** — writes deduped evidence JSON |
| `filter-evidence.js` | Post-dedup evidence filter (3 sequential passes) against config/filter-rules.json | (called by run-weekly.js) | `config/filter-rules.json`; `state/org-chart.json` | **Y** — writes filtered JSON + audit log |
| `draft-weekly.js` | Generates executive weekly email draft via sonnet (single LLM call) | (called by run-weekly.js) | `ANTHROPIC_API_KEY`; `prompts/draft-weekly.md`; `templates/weekly-email.md` | **Y** — writes `reports/{date}-weekly.md` |
| `audit-draft.js` | Post-draft audit with iterative fix loop via sonnet (max 5 loops) | (called by run-weekly.js) | `ANTHROPIC_API_KEY` | **Y** — may overwrite report with fixed version |

### 1.8 Send Pipeline (2 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `run-send.js` | Interactive draft dispatcher with human confirmation | `--dry-run`, `--type <filter>`, `--date YYYY-MM-DD` | FOCI + Chat tokens; `outlook-send-mail.js`; `teams-send-chat.js` | **Y** — sends messages; updates draft status; writes `reports/{date}-send-summary.md` |
| `synthesize-feedback.js` | Synthesizes pm-send feedback JSONL into patterns.md via sonnet | (called by pm-send or SessionEnd hook) | `ANTHROPIC_API_KEY`; `state/pm-send-feedback.jsonl` | **Y** — rewrites `state/pm-send-patterns.md` |

### 1.9 Enterprise Search (1 tool)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `glean-search.js` | Glean enterprise search (search, ADVANCED AI chat, doc fetch) | `--query <q>`, `--chat <q>`, `--read <doc>`, `--datasource`, `--limit`, `--json` | `glean` CLI installed + authenticated via Okta OAuth | **N** — read-only; uses `glean` CLI subprocess |

### 1.10 Backtest / Analysis (5 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `backtest-audit.js` | Aggregates morning-classified-*.json for classification audit | (standalone) | State files present | **Y** — writes markdown summary + full JSON |
| `backtest-extract-candidates.js` | Extracts false positive/negative candidates from backtest data | (standalone) | `state/backtest-30d-full.json` | **Y** — writes FP/FN candidate JSON + markdown |
| `backtest-rerun.js` | Re-classifies all morning-scan files using updated classify-messages.js | (standalone) | `ANTHROPIC_API_KEY` | **Y** — writes `state/backtest-v2/` |
| `classify-email-audience.js` | Classifies sent emails by audience segment from TO recipients | stdin JSON → stdout JSON | `state/org-chart.json`; domain heuristics | **N** — stdin/stdout only |
| `fetch-comms-threads.js` | Batch fetches email threads for YK-comms study | stdin IDs → stdout JSONL | FOCI token (`Mail.ReadWrite`) | **N** — stdout only |

### 1.11 Scheduler / Infrastructure (5 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `dispatcher.js` | Launchd scheduler dispatcher; determines due skills and runs them | (invoked hourly by launchd) | `state/schedule.json`; `node`/`claude` in PATH | **Y** — writes `state/scheduler-log.json`; invokes `claude -p` |
| `install-scheduler.js` | Platform scheduler installer (macOS launchd / Windows Task Scheduler) | `--remove` | macOS: `launchctl`; Windows: `schtasks` | **Y** — writes launchd plist; registers system job |
| `install-watchdog.js` | Installs launchd watchdog that monitors scheduler health | `--remove` | `launchctl` | **Y** — writes launchd plist |
| `scheduler-launcher.sh` | Shell wrapper for launchd → dispatcher.js; resolves node at runtime | optional `<script.js> [args...]` | `node` in PATH | **Y** — launches dispatcher |
| `watchdog.js` | Detects stalled scheduler via log file mtime; fires macOS notification | (invoked by launchd) | `terminal-notifier` (optional) | **N** — read-only; sends OS notification |

### 1.12 Other (2 tools)

| Tool | Purpose | Main Actions / Flags | Login / Env Requirements | Does-it-write? |
|------|---------|---------------------|--------------------------|----------------|
| `enrich-reddit-saved.py` | Enriches reddit-saved.json with full posts, comments, metadata | (standalone; resumable) | Reddit public .json API (no OAuth) | **Y** — writes enriched JSON + markdown |
| `run-send.js` (see §1.8) | — | — | — | — |

---

## 2. Pipelines

### 2.1 pm-morning (Daily Triage)

**Trigger:** Manual `/pm-morning` or launchd scheduler (via `dispatcher.js` invoking `claude -p /pm-morning`)  
**Invocation:** `node bin/run-morning.js [--since Nd] [--dry-run] [--skip-classify] [--skip-dedup]`  
**Models:** haiku-4-5 (classification) + sonnet-4-6 (draft generation, 5 concurrent)

**Steps (handled entirely by run-morning.js):**
1. Config + tokens — load `config/morning-pipeline.json`; run `ensure-tokens.js`
2. Fetch (parallel) — `teams-read-chats.js --scan-recent` + `outlook-read-mail.js --list --unread --since 24h`
3. Merge + dedup — Combines Teams + Outlook, Jaccard cross-source dedup (threshold 0.5, 3-hour window)
4. Classify — `classify-messages.js` via haiku API (action_required / fyi / noise + urgency 0–3)
5. Prioritize — score by sender seniority (ELT / directs / peers) + urgency + age
6. Draft — `draft-responses.js` via sonnet API; applies yk-comms audience routing + yk-voice styling
7. Report — assembles `reports/{date}-morning.md` + `reports/{date}-morning-summary.md`
8. State — updates `state/last-scan.json`
9. Summary — prints pipeline counts and timings to stderr

**Outputs:**
- `reports/{date}-morning-summary.md` — formatted briefing (displayed to user)
- `reports/{date}-morning.md` — full raw report
- `drafts/{date}/*.md` — one draft per action item (with frontmatter: audience, situation)
- `state/morning-scan-{date}.json`, `state/morning-classified-{date}.json`, `state/action-items-{date}.json`
- `state/teams-scan-raw-{date}.json`, `state/outlook-evidence-{date}.json`
- `state/morning-run-status.json` — structured exit status for downstream consumers

**Bin tools called:** `ensure-tokens.js`, `teams-read-chats.js`, `outlook-read-mail.js`, `classify-messages.js`, `draft-responses.js`, (lib: `dedup.js`, `seniority.js`)

**Human-approval points:** None inside the pipeline. User reviews drafts, then dispatches via `/pm-send`.

**Token failure fallback:** pm-morning SKILL.md delegates to `/pm-login`, then re-runs pipeline.

---

### 2.2 pm-weekly (Weekly Status Email)

**Trigger:** Manual `/pm-weekly` or launchd scheduler (Fridays)  
**Invocation:** `node bin/run-weekly.js [--since 7d] [--dry-run] [--skip-extract] [--skip-dedup]`  
**Models:** haiku-4-5 (extraction, 16 concurrent) + sonnet-4-6 (synthesis, single call)

**Prerequisite:** Raw scan files for today (`state/teams-scan-raw-{date}.json`, `state/outlook-evidence-{date}.json`)  
If missing, the skill pre-runs: `teams-read-chats.js --scan-recent --since 7d` + `outlook-read-mail.js --since 168h`

**Steps (handled by run-weekly.js):**
1. Check inputs — verify raw scan files exist
2. Extract — `extract-evidence.js` (haiku, parallel batches, tool_use schema-enforced JSON)
3. Dedup — `dedup-evidence.js` (deterministic: conversationId grouping + cross-source Jaccard + 3-day window)
4. Filter — `filter-evidence.js` (3 sequential filter passes against `config/filter-rules.json`)
5. Draft — `draft-weekly.js` (sonnet, single call with full evidence; `prompts/draft-weekly.md` system prompt)
6. Audit — `audit-draft.js` (sonnet, P0/P1 findings trigger iterative fix loop, max 5 iterations)
7. Summary — writes `state/weekly-run-status.json`

**Outputs:**
- `reports/{date}-weekly.md` — executive-style weekly email draft with YAML frontmatter
- `state/weekly-evidence-{date}.json` — deduped evidence array
- `state/filter-log-{date}.json` — filter audit trail
- `state/weekly-audit-{date}.json` — post-draft audit findings

**Bin tools called:** `teams-read-chats.js`, `outlook-read-mail.js`, `extract-evidence.js`, `dedup-evidence.js`, `filter-evidence.js`, `draft-weekly.js`, `audit-draft.js`

**Human-approval points:** User reviews draft at `reports/{date}-weekly.md` before dispatching. `/pm-send` handles actual delivery.

**Audience / style:** Peer (governance-lt@diligent.com). yk-comms peer profile + yk-voice. Invariant rules enforced: date tags `(launched mm/dd/yy)`, no invented content, no exclamation points.

---

### 2.3 pm-send (Draft Dispatch)

**Trigger:** Manual `/pm-send [--type=<filter>] [--date YYYY-MM-DD]`  
**Invocation:** Skill orchestrator wrapping `run-send.js`

**Steps:**
0. Load learned patterns — read `state/pm-send-patterns.md` for skip rules and priority overrides
1. Draft discovery — `run-send.js --dry-run`; then re-reads drafts for enriched context
1.5. Refresh stale drafts (past-date) — `refresh-draft-context.js` via haiku/sonnet
2. Human approval loop — per-draft display + AskUserQuestion (Send / Edit / Keep / Skip)
2.5. Voice transformation — `/yk-comms` → `/yk-voice` applied to raw draft body before send
3. Dispatch:
   - **Outlook**: `outlook-send-mail.js` (reply or compose)
   - **Teams 1:1**: `teams-send-chat.js`
   - **Teams channels**: agent-browser fallback (no ChannelMessage.Send scope)
   - **JIRA comments**: `mcp__atlassian__jira_add_comment` MCP call
4. Status update — draft frontmatter updated to `status: sent`; log entry appended
5. Summary — `reports/{date}-send-summary.md`
5.5. Feedback synthesis — `synthesize-feedback.js` (sonnet) rewrites `state/pm-send-patterns.md`

**Safety gate:** NEVER auto-send — every draft requires explicit user confirmation via AskUserQuestion.

**Bin tools called:** `run-send.js`, `check-token-health.js`, `foci-device-login.js`, `refresh-draft-context.js`, `outlook-send-mail.js`, `teams-send-chat.js`, `synthesize-feedback.js`

**Human-approval points:** Every single draft (mandatory). Feedback collection after each draft.

---

### 2.4 pm-jira (Daily JIRA/Confluence Monitor)

**Trigger:** Manual `/pm-jira [--since 24h]`

**Steps:**
1. Data retrieval (parallel):
   - JIRA: `curl` → `https://diligentbrands.atlassian.net/rest/api/2/search?jql=assignee=currentUser() AND updated>=-24h`
   - Confluence: `curl` → `/wiki/rest/api/content/search?cql=mention=currentUser() AND lastmodified>=now("-24h")`
2. Classification — needs-comment vs. fyi (P0/P1 open, action keywords, @Yu-Kuan mentions, Confluence review titles)
3. Draft generation — yk-voice transformed JIRA comment drafts via LLM; written via pm-output
4. Report — `reports/{date}-jira.md` via pm-output write_report
5. State — updates `state/last-scan.json` `"jira"` key

**Outputs:** `reports/{date}-jira.md`, `drafts/{date}/jira-comment-*.md`  
**Auth:** `$ATLASSIAN_API_TOKEN` from `~/.zshrc`; basic auth `yklin@diligent.com:$TOKEN`  
**No bin tools called** (skill uses direct curl, pm-output patterns, and yk-voice skill)  
**Human-approval points:** User reviews jira-comment drafts; dispatched via `/pm-send` → `mcp__atlassian__jira_add_comment`

---

### 2.5 pm-pulse (Biweekly Pulse Survey)

**Trigger:** Manual `/pm-pulse`  
**Target:** `https://edxc.fa.us2.oraclecloud.com` (Oracle HCM)

**Steps:**
1. Browser navigation — open Oracle Journeys page via agent-browser pm-os profile
2. SSO detection — delegate to `/pm-login` if Okta/sign-in keywords found
3. Survey discovery — search Journeys → Notifications → Todos (in order)
4. Form fill — hardcoded defaults: Empowerment = Neutral; Comment = fixed text; Wins = blank
5. Review gate — OFF by default (no human review); submit immediately
6. Submit — click submit button; verify confirmation text
7. Report — write `reports/{date}-pulse.md`

**Bin tools called:** None (pure agent-browser + skill logic)  
**Human-approval points:** SSO MFA prompt (delegated to pm-login). Review gate is OFF by default.

---

### 2.6 pm-login (SSO Authentication)

**Trigger:** Manual `/pm-login` or called by other skills on SSO detection

**Steps:**
1. `ensure-tokens.js` — attempt autonomous API token refresh
2. Session validity check — `agent-browser eval "document.title"` at teams.microsoft.com
3. Headed MFA flow (if needed) — opens `https://diligent.okta.com/` in headed browser; AskUserQuestion
4. Microsoft sign-in cascade — handles post-Okta Teams sign-in page
5. Session verification (headless) — closes headed browser; re-navigates; verifies title
6. FOCI device code enrollment (if needed) — `foci-device-login.js`; extract chat token; check office token
7. Summary — displays token status (FOCI / Chat / Graph scopes / Office scopes)

**Auth chain managed:** Okta SSO → Microsoft Teams SSO → FOCI RT → skypeToken (Chat) → Office Desktop RT

---

### 2.7 yk-comms/yk-voice Voice Transformation (Internal Pipeline)

**Trigger:** Called by pm-output `apply_voice()`, pm-send Step 2.5, pm-weekly draft generation, draft-responses.js

**Steps:**
1. Audience detection — classify recipient (c-suite / peer / direct-report / vendor / team-broadcast)
2. Situation detection — classify intent (requesting / delegating / feedback / disagreement / follow-up / declining / celebrating / escalating / bad-news / status-update)
3. yk-comms composition — apply audience profile (formality, directness, warmth, greeting, sign-off) + situational framework
4. yk-voice styling — apply linguistic rules (contractions, active voice, forbidden patterns: no em dash, no "However" opening, no "Furthermore", no "Additionally")
5. Final check — word count vs. audience median; sign-off correct; no anti-patterns

**Data source:** 12 months of sent emails (2,454 classified, 177 deep-analyzed)  
**Precedence:** yk-comms wins over yk-voice when they conflict on email/messaging

---

### 2.8 Glean Enterprise Search

**Trigger:** Called standalone or from other skills needing enterprise knowledge  
**Bin tool:** `glean-search.js` wrapping `glean` CLI

**Two modes:**
- `--query` mode: search/retrieval for verifying facts, finding source docs, provenance
- `--chat` mode: ADVANCED agentic AI for synthesis, analysis, open-ended questions

**Do NOT** use `glean chat` CLI directly — it strips `agentConfig`. Always use the wrapper.

---

### 2.9 No Video/Media Creation Pipeline

No video, media, MP4, or recording creation pipeline found in pm_os. `pptx-edit.py` handles PPTX charts and images but is document editing, not media creation.

---

## 3. Auth / Token Model

### 3.1 Token Files and Storage

All tokens are AES-256-GCM encrypted at rest using a key from `~/.age/pm-os.key`.  
Store: `~/.secrets/pm-os/*.json.enc` (read/written via `lib/token-store.js`)  
Audit log: `~/.pm-os/audit-log.jsonl` (appended by all write-capable tools)

| Token File | Client ID | Key Scopes | Used By |
|-----------|-----------|------------|---------|
| `foci-token.json.enc` | Teams (`1fec8e78-...`) | `Sites.ReadWrite.All`, `Mail.ReadWrite`, `Mail.Send`, `ChatMessage.Send`, `Files.ReadWrite.All`, `Notes.ReadWrite.All` | graph-workbook, graph-file-ops, outlook-read/send, teams-send, ooxml-surgery, sp-checkout, graph-list-crud |
| `foci-office-token.json.enc` | Office Desktop (`d3590ed6-...`) | `Chat.ReadWrite`, `Directory.Read.All`, `ChannelMessage.Read.All`, `Group.ReadWrite.All`, `User.Read.All` | graph-org-chart, teams-read-channels (directory), chat-read ops |
| `teams-chat-token.json.enc` | Skype/Chat (`authsvc`) | Native messaging API (skypeToken) | teams-read-chats, teams-read-channels |

### 3.2 FOCI (Family of Client IDs) Model

FOCI allows any enrolled client's refresh token to be exchanged for any other client's access token. PM-OS exploits this:

- **Phase 1 (enrollment):** `foci-device-login.js` performs device code flow against Teams client ID (`1fec8e78-...`). User visits URL, enters code, approves. Yields a 90-day rolling refresh token (RT).
- **Phase 2 (cross-pollination):** The same RT is exchanged against Office Desktop client ID (`d3590ed6-...`) to obtain `foci-office-token` with Directory/Chat scopes unavailable from Teams client.
- **skypeToken chain:** FOCI RT → `foci-device-login.js --skype-exchange` → authsvc.teams.microsoft.com/v1.0/authz → skypeToken stored in `teams-chat-token`.

**FOCI clients enrolled:** Teams, Office Desktop (Azure CLI and Outlook Mobile are known alternatives but not used by default)

**Token lifetime:** Access tokens ~60-90 min; FOCI RT ~90 days rolling (refreshed on each use). Chat skypeToken: expires `expiresAt` field in JSON (typically 24-48h).

**Critical constraint (TOKEN-7 incident 2026-03-16):** Each OAuth RT exchange invalidates the prior RT. An agent performing exchanges without saving the rotated token breaks the entire chain, requiring interactive device code re-enrollment. Per `CLAUDE.md`, all RT refresh ops require explicit user authorization.

### 3.3 ensure-tokens.js Lifecycle

`ensure-tokens.js` is the autonomous manager for scheduled/non-interactive runs:
1. Check FOCI token freshness via JWT `exp` claim (>30 min remaining = fresh)
2. If stale: HTTP POST to `login.microsoftonline.com` RT refresh endpoint (saves rotated RT)
3. Check Chat token freshness
4. If stale: attempt `foci-device-login.js --skype-exchange` with retry+backoff
5. If still stale: detect SSO state via agent-browser (headless)
6. If SSO expired: open headed browser for MFA; poll until login detected or 5-min timeout
7. macOS notification on MFA needed or success

**Exit codes:** 0 = all fresh, 1 = refresh failed, 2 = MFA timed out, 3 = no network

### 3.4 Browser Session Auth (agent-browser)

- **Profile:** `~/.agent-browser/pm-os` — persists cookies/storage across runs
- **Auth state snapshots:** `~/.agent-browser/pm-os-auth.json` — exported by `/pm-login`; loaded by parallel workers
- **Parallel workers (pm-morning):** Sessions `worker-0` through `worker-N` load from auth.json snapshot; 3 SSO-retry attempts

### 3.5 Glean Auth

- `glean` CLI OAuth via Okta SSO; refresh_token grant with ~7-day cycle
- Checked via `glean auth status`; re-enrolled via `glean auth login` (browser Okta)

### 3.6 Atlassian Auth

- Basic auth: `yklin@diligent.com:$ATLASSIAN_API_TOKEN`
- `$ATLASSIAN_API_TOKEN` from `~/.zshrc`
- Used by pm-jira skill via `curl`; Atlassian MCP for pm-send JIRA dispatch

### 3.7 Reachable Services

| Service | Access Method | Read? | Write? |
|---------|--------------|-------|--------|
| Microsoft Teams (1:1 chats) | FOCI + skypeToken native API | Y | Y |
| Microsoft Teams (channels) | Skype chatService API + Graph beta fallback | Y | N (no ChannelMessage.Send scope; browser fallback only) |
| Microsoft Outlook (email) | Graph API via FOCI | Y | Y |
| SharePoint / OneDrive | Graph API via FOCI | Y | Y |
| Microsoft Azure AD / Directory | Graph API via Office Desktop FOCI | Y | N |
| JIRA | Atlassian REST API basic auth + Atlassian MCP | Y | Y |
| Confluence | Atlassian REST API basic auth | Y | Y (via MCP or browser) |
| Oracle HCM (Pulse survey) | agent-browser SSO session | N/A | Y (form fill only) |
| Glean enterprise search | glean CLI OAuth | Y | N |
| Anthropic API | `ANTHROPIC_API_KEY` in `.env` | N/A | Y (LLM calls) |

---

## 4. Tool-Registry Conventions

### 4.1 The Guarded-Tool Pattern

All Office document and SharePoint operations must go through registered tools. Direct use of python-docx, pandas, openpyxl, or ad-hoc download scripts is banned. This pattern is documented in `~/.claude/rules/tool-registry.md` and referenced by pm-os `CLAUDE.md`.

**Why:** Guarded tools enforce write-protection (`--confirm` flag), append to the audit log, handle FOCI token routing correctly, handle chunked upload for large files, manage ETag concurrency, and follow the 423-Locked recovery protocol.

### 4.2 Routing Order for Word/PowerPoint Edits

1. **pm-os-bridge** (`bin/office-bridge-command.js`) — Office.js add-in for in-session edits on open Word/PPT
2. **office-browser-edit.js** — Small visible edits in Office Online (headless browser fallback)
3. **ooxml-surgery.py** — OOXML package operations when file is not open in Office (text find-replace, comment CRUD)
4. **graph-edit-pptx.js** — Complex PPT structural edits (charts, images, tables)
5. **graph-file-ops.js upload** — Whole-file replacement (last resort)

**423 Locked protocol:** Do NOT delete/recycle/rename/bypass-lock. Preserve local draft. Finish via Office-session path. Escalate if that also fails. Never invent a replacement strategy.

### 4.3 Write-Protect Mechanism

All write tools require one of:
- `--confirm` flag (graph-workbook, graph-file-ops, graph-list-crud, sp-checkout)
- `--agent-mode` or `PM_OS_AGENT=1` env var
- Every write is appended to `~/.pm-os/audit-log.jsonl`

### 4.4 Banned Alternatives

| Operation | Banned | Use Instead |
|-----------|--------|-------------|
| Excel read/write | pandas, openpyxl, xlrd, xlsxwriter | `graph-workbook.js` |
| SharePoint file download/upload | requests, wget, curl + parse, urllib | `graph-file-ops.js` |
| Word/PPT text edits | python-docx, docx, lxml direct, mammoth | `ooxml-surgery.py` |
| Complex PPT edits | python-pptx standalone | `graph-edit-pptx.js` (which calls `pptx-edit.py`) |
| SharePoint list CRUD | raw REST | `graph-list-crud.js` |
| Token extraction | direct HTTP to login.microsoftonline.com without saving RT | `foci-device-login.js` |

---

## 5. Skills Layer

All skills live in `~/Code/pm_os/skills/` and are symlinked to `~/.claude/skills/pm-os/`.  
`yk-voice.md` and `o365-doc-edit/SKILL.md` live directly in `~/.claude/skills/`.

| Skill | File | What It Wraps | Inputs / Expectations |
|-------|------|---------------|----------------------|
| `pm-morning` | `skills/pm-morning/SKILL.md` | `bin/run-morning.js` (9-step pipeline) | None; tokens + `ANTHROPIC_API_KEY` auto-checked; falls back to `/pm-login` on token failure |
| `pm-login` | `skills/pm-login/SKILL.md` | `ensure-tokens.js`, agent-browser MFA flow, `foci-device-login.js` | AskUserQuestion required for MFA confirmation; called standalone or from other skills on SSO detection |
| `pm-weekly` | `skills/pm-weekly/SKILL.md` | `bin/run-weekly.js` (6-step pipeline) | Raw scan files should exist (pre-collected); falls back to `/pm-login` on token failure |
| `pm-send` | `skills/pm-send/SKILL.md` | `bin/run-send.js` + human approval loop + yk-comms/yk-voice + Atlassian MCP | Drafts in `drafts/{date}/`; AskUserQuestion for every draft; FOCI + Chat tokens fresh |
| `pm-pulse` | `skills/pm-pulse/SKILL.md` | agent-browser Oracle HCM | Active Okta SSO (or delegates to `/pm-login`); hardcoded survey defaults |
| `pm-output` | `skills/pm-output/SKILL.md` | Shared write_report / write_draft / apply_voice patterns | Called by other PM-OS skills; `audience`/`situation` fields for voice routing |
| `pm-jira` | `skills/pm-jira/SKILL.md` | Atlassian REST API via curl | `$ATLASSIAN_API_TOKEN` in `~/.zshrc`; relies on pm-output + yk-voice |
| `yk-comms` | `skills/yk-comms/SKILL.md` | Audience-aware communication strategy routing | Audience + situation; consumed by pm-send, pm-morning, pm-weekly, pm-output |
| `yk-voice` | `~/.claude/skills/yk-voice.md` | Linguistic style rules (Yu-Kuan's voice) | Text to transform; called after yk-comms or standalone; forbids em dashes, "However" openers, etc. |
| `glean-connect` | `skills/glean-connect/SKILL.md` | `bin/glean-search.js` wrapping `glean` CLI | `glean` CLI installed + authenticated; smart mode routing (--query vs --chat) |
| `o365-doc-edit` | `~/.claude/skills/o365-doc-edit/SKILL.md` | Full Office doc edit routing (bridge → browser → OOXML → Graph) | FOCI tokens fresh; pm-os browser profile authenticated |

---

## 6. Gaps and Fragility Notes

### 6.1 FOCI Token Chain: Single Point of Failure (Critical)

The entire Microsoft Graph capability (Teams, Outlook, SharePoint, JIRA-adjacent) depends on the FOCI refresh token chain. If the RT is consumed without being saved (TOKEN-7 incident), all API tools fail simultaneously and interactive device code re-enrollment is required. `ensure-tokens.js` handles this autonomously for scheduled runs, but:
- Any agent that calls `foci-device-login.js --refresh` or `--foci-exchange` directly without explicit user authorization breaks the chain
- The `CLAUDE.md` safety rule is the only protection — there is no system-level guard

### 6.2 agent-browser Profile Auth: Fragile to Profile Corruption / Okta Session Expiry

`pm-pulse` and Teams channel send (via browser fallback) require `~/.agent-browser/pm-os` to have a valid Okta SSO session. This session expires independently of the FOCI tokens (different auth stack: browser cookies vs. OAuth tokens). If the profile is corrupted or the Okta session expires:
- `/pm-login` must be run with a headed browser and Okta MFA push
- This requires the user to be present (cannot be automated without MFA bypass)
- Parallel workers need the auth state saved to `~/.agent-browser/pm-os-auth.json` and loaded by each worker

### 6.3 `$ATLASSIAN_API_TOKEN` in `~/.zshrc`: Env-Var-Only Auth

`pm-jira` uses `bash -c 'source ~/.zshrc 2>/dev/null; curl ...'` to pick up the Atlassian token. This is fragile because:
- `~/.zshrc` is sourced at curl invocation time (not at skill start); changes between sessions don't auto-propagate
- If `~/.zshrc` is not sourced (e.g., non-interactive shell, CI context), the token is empty and curl returns 401
- There is no retry or fallback — pm-jira logs error and produces an empty report

### 6.4 `prompts/draft-weekly.md` is a Fatal Dependency

`run-weekly.js` / `draft-weekly.js` exits 1 immediately if `prompts/draft-weekly.md` is missing. This file is the system prompt for sonnet's draft synthesis call. The file is in `pm_os/prompts/` (git-tracked) but if accidentally deleted or path misconfigured in `config/weekly-pipeline.json`, the entire weekly pipeline fails silently at the draft step with no partial output.

### 6.5 Teams Channel Send Has No API Path

There is no Graph API scope for `ChannelMessage.Send` in the FOCI token set. Sending messages to Teams channels requires the agent-browser fallback, which requires:
- Active Okta SSO in the pm-os profile
- Correct DOM navigation (Teams v2 DOM is complex; trailing space in CSS selector is documented as critical)
- This path is entirely fragile to Teams UI changes

### 6.6 No Salesforce Integration

Despite Salesforce being mentioned in the auth model (Glean may surface Salesforce content), there is no direct Salesforce API integration in pm_os. Customer data (churn risk, deal status) reaches pm_os only via emails/Teams messages + Glean search.

### 6.7 Oracle Pulse Survey: Hardcoded Defaults, No Config File

Survey answers in `pm-pulse` are hardcoded in the SKILL.md itself (not in a config file). Changing the survey response requires editing the SKILL.md. Additionally, the Oracle JET radio button click requires a JavaScript `eval` workaround — any Oracle UI update could break this.

### 6.8 Token Encryption Key Must Pre-Exist

`lib/token-store.js` requires `~/.age/pm-os.key` to be a 32-byte hex AES-256 key. If this file is missing (new machine setup, key rotation), every single bin tool that touches tokens throws immediately. There is no setup wizard; the setup is documented only in referenced (but not locally present) "pm-os security setup docs."

### 6.9 `synthesize-feedback.js` Staleness Check Only

The pm-send feedback learning loop (`synthesize-feedback.js`) relies on an mtime comparison between `pm-send-feedback.jsonl` and `pm-send-patterns.md`. If the system clock is adjusted or files are touched without content changes, this check can produce false "already fresh" results and the patterns file goes stale.

---

## 7. Summary Statistics

- **Total bin/ tools:** 49 (46 .js, 2 .py, 1 .sh)
- **Write-capable tools (Y):** 34 | **Read-only tools (N):** 15
- **Pipelines documented:** 8 (pm-morning, pm-weekly, pm-send, pm-jira, pm-pulse, pm-login, yk-comms/yk-voice, glean)
- **Skills in pm_os/skills/:** 9 (pm-morning, pm-login, pm-weekly, pm-send, pm-pulse, pm-output, pm-jira, yk-comms, glean-connect)
- **Skills in ~/.claude/skills/ wrapping pm_os:** 2 (yk-voice, o365-doc-edit)
- **Services reachable:** Teams (read + 1:1 write), Outlook (read + write), SharePoint/OneDrive (full CRUD), JIRA (read + write via MCP), Confluence (read), Oracle HCM Pulse (write via browser), Glean (read), Anthropic API (LLM calls)
- **Services NOT covered:** Salesforce (no direct API), GitHub (no integration), Teams channels (read only via API; write via browser fallback only)
- **LLM models used:** `claude-haiku-4-5-20251001` (classification, evidence extraction — cost efficiency), `claude-sonnet-4-6` (drafting, synthesis, audit, feedback synthesis — quality)
- **Encryption scheme:** AES-256-GCM via `~/.age/pm-os.key`; stored at `~/.secrets/pm-os/*.json.enc`
