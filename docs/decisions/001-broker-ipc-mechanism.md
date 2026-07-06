# ADR-001: Broker IPC Mechanism (Unix Domain Socket + JSON-RPC)

**Date:** 2026-07-06  
**Status:** RESOLVED  
**Deciders:** Yu-Kuan (owner), via Bootstrap Decision BD1 (plan §8:548)

---

## Decision

The broker process communicates with the assistant/gateway process via **unix domain socket + JSON-RPC 2.0**, not via file queue, message broker, or local HTTP server.

**Socket path:** `~/.hermes/broker/broker.sock` (mode `0600`, owner-only)  
**Framing:** newline-delimited JSON, one RPC message per line  
**No network listener ever.** Local-only; macOS launchd non-GUI session compatible.

---

## Context

**The Problem:** The Fable factory (P1a phase) introduces a separate broker process that holds all send/API/push credentials and enforces approval gates and safe-lane policies. The assistant and broker must communicate; their IPC mechanism affects:

- **Crash safety & restart semantics** — the assistant must not retransmit enqueued actions after a broker restart
- **Durability across process bounces** — held actions queue in SQLite and survive both processes restarting
- **Launchd non-GUI session reachability** — overnight workers may spawn the broker from a launchd service with no GUI context
- **Message framing robustness** — a broker or assistant crash mid-write must not corrupt the message stream
- **Credential exposure surface** — IPC carries action payloads, not credentials; no network path = no credential leak via the wire

**Design context:**  
- P1a Design Doc (`docs/plans/harness/fable/p1a/P1a-design.md` §1.2) specifies the JSON-RPC method set: `enqueue_action`, `list_pending`, `approve`, `reject`, `send` (internal), `git_push` (internal), `health`.
- BD1 (plan §8:548) marked this a **bootstrap-blocking decision** — it must be resolved before P1a coding begins because the broker's architecture is built *on* this choice.

---

## Alternatives Considered

### 1. File Queue (Rejected)

**Mechanism:** Assistant writes action structs to a JSON or JSONL file; broker polls the directory or watches with `inotify`/`FSEvents`.

**Pros:**
- Simple, no RPC complexity
- Natural log structure for audit/replay

**Cons:**
- **Polling latency** — 1–10 second jitter before the broker sees a new action; approved actions that route back through the file queue create buffering delays
- **Concurrency hazards** — file locking semantics vary across macOS/Linux; incomplete writes create partial frames; truncation race between the assistant's write and the broker's read is real
- **Restart ambiguity** — the broker cannot distinguish "did I process this file already?" from "is this a new action?" without global sequence numbers and reconciliation logic
- **macOS non-GUI session** — FSEvents may not fire reliably in a launchd background session; `stat()` polling is the fallback and scales poorly with action frequency

**Verdict:** Too slow for interactive approval loops; error-prone atomicity for a durability-critical component.

---

### 2. Local HTTP Server (Rejected)

**Mechanism:** Broker listens on `localhost:PORT` (ephemeral or fixed). Assistant makes REST calls.

**Pros:**
- Familiar; lots of stdlib support
- Request/response pattern is natural
- Standard library TLS if paranoia strikes later

**Cons:**
- **Network exposure surface** — even `localhost`-only, the port is a named socket that can be probed, hijacked via port collisions, or accessed by any process on the machine; if the port is leaked (e.g., in a debug log or error message), any co-located process can send actions to the broker
- **No natural backpressure** — HTTP/1.1 requires timeouts; handling a slow broker (e.g., waiting for approval) is awkward (long-poll, chunked streaming, or WebSocket proliferation)
- **macOS launchd complexity** — binding to `localhost` in a non-GUI launchd session works, but reachability/DNS resolution can be brittle; port collisions require coordination

**Verdict:** Unix socket is strictly more secure and simpler; no reason to introduce a network path.

---

### 3. **Adopted: Unix Domain Socket + JSON-RPC 2.0**

**Mechanism:** Assistant and broker communicate over a UNIX domain socket (`AF_UNIX`) at `~/.hermes/broker/broker.sock`. Each request and response is a single line of JSON (newline-delimited). No auxiliary protocol layer; minimal framing.

**RPC framing:**

```
Request:  {"jsonrpc":"2.0","id":1,"method":"enqueue_action","params":{...}}\n
Response: {"jsonrpc":"2.0","id":1,"result":{"action_id":"...","disposition":"held"}}\n
Error:    {"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"..."}}\n
```

**Why this wins:**

1. **Local-only, no network exposure** — the socket file is owned by the broker (UID `0600`), unreadable by the assistant; no in-flight credentials on any wire
2. **Simple framing** — newline-delimited JSON avoids length-prefixed fragmentation; a half-written line (broker crash mid-write) is simply discarded, not misparsed
3. **Atomic enqueue + durability** — the broker writes the action to SQLite (with fsync) *before* responding to the enqueue RPC; a crash after the RPC returns never leaves an action "acked but not stored"
4. **Launchd non-GUI compatible** — no DNS, no loopback interface, no port binding; a launchd service can create a socket file and a client can connect to it reliably
5. **Clean approval loops** — the assistant enqueues and polls `list_pending` for Telegram notifications; no request–response blocking; the approval response comes in-band through the socket in the real `approve` RPC from the gateway Telegram-callback handler
6. **Standard, no custom deps** — Python's `socket.AF_UNIX` and `json` module are stdlib; no Protobuf, no ZeroMQ, no broker middleware

**Schema evolution:** JSON-RPC 2.0 supports arbitrary params; new optional fields in `enqueue_action` (e.g., `priority`, `tags`) can be added without breaking existing callers. Not a concern for P1a (fixed interfaces), but future-safe.

---

## Consequences

### Immediate (P1a)

- **Broker source tree:**
  - `broker/jsonrpc.py` — newline-framed JSON-RPC encode/decode (no third-party dependency)
  - `broker/server.py` — UDS listener + dispatch loop; fail-closed on socket open error
  - `broker_client.py` — assistant-side thin stub; frames RPC calls, no credentials

- **Socket lifecycle:**
  - Broker creates `~/.hermes/broker/broker.sock` at startup with `chmod 0600`
  - Client (assistant) connects on-demand; transient socket errors (broker down) are surfaced as "enqueue failed, action not queued" — *no fallback to direct send*
  - Broker removal of the socket file signals shutdown; clients detect closed socket and error
  - Launchd service (`broker/launchd/com.hermes.broker.plist`) manages the broker lifecycle independent of the gateway

- **Testing:**
  - `test_jsonrpc_halfline_discarded` verifies that a truncated JSON line doesn't corrupt the next message
  - `test_broker_listens_on_uds` verifies socket exists, is owned correctly, `health` succeeds
  - Socket-level race tests (concurrent enqueue, broker restart mid-approve) are in P1a-c (durability layer)

### Medium-term (P1b–P2)

- **Multi-host future (P1a-7 milestone "post-P1a"):** The RPC interface is *independent of transport*. A future remote-worker phase can:
  - Keep the JSON-RPC method set unchanged
  - Swap the UDS transport for TCP (with mTLS, auth tokens)
  - Use the same dispatch logic; only the socket creation/listen call changes
  - The design doc already notes this: §4.3 `remote_stub.py` satisfies the same `Worker` ABC, proving the supervisor code path is transport-agnostic

- **Credential rotation (P1b trust ledger):** The approval-nonce mechanism (P1a-d) is tied to broker-held secrets; rotating broker credentials requires only a broker restart and new launchd env, *not* assistant redeploy

### Risks & Mitigations

| Risk | Severity | Mitigation | Owner |
|---|---|---|---|
| Socket file permissions drift (`0644` readable by other users) | **Medium** | Broker must `chmod 0600` at creation; tests verify `stat(sock)` mode. Launchd umask pinning (via `.plist` config) or explicit call in server startup. | P1a-b |
| Socket reachability in a launchd non-GUI session (keychain unlock, filesystem mount timing) | **Medium** | Empirically validate in P1a-8 that a broker started from launchd can write the socket file and a background worker can reach it. Fallback: add a socket-startup health check to the worker launcher (P1a-6). | P1a-8 |
| Assistant connects before broker is ready | **Low** | Client stub uses exponential backoff on socket-not-found; after 5 retries, error surfaces to the caller. Not a hang. | P1a-b |
| Half-written JSON line from broker crash | **Low** | Newline framing: a truncated line (no trailing `\n`) is skipped by the reader; the next complete line is parsed. Verified by test. | P1a-b, P1a-c |

---

## Related Decisions

- **P1a-2 (P1a-b):** Broker server architecture and JSON-RPC implementation
- **P1a-4 (P1a-d):** Approval-nonce mechanism; RPC signature becomes `approve{action_id, nonce}` (no trust-me `approver` string)
- **P1a-7 (P1a-g):** Remote-worker stub; proves the RPC dispatch is transport-agnostic

---

## Decision Rationale (one-liner)

Unix domain socket isolates the broker on the same machine (no network leak), is robust to process crashes via newline framing, works from launchd non-GUI sessions, and is a natural stepping stone to remote workers via RPC transport swap (not method change).

---

## Approval Chain

- **Architect** (P1a-design author): approved as part of P1a-design.md (§1.2, §1.3)
- **Owner** (Yu-Kuan): bootstrap decision BD1 in plan §8:548 ("adopt unless the owner objects")
- **Plan** (hermes-fable-plan.md): recorded as bootstrap decision; P1a-1 task is "decide and document"

**This record closes task P1a-1 (decide + document the broker IPC mechanism).**
