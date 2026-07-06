# P1a-1 Completion Report — Broker IPC Decision Record

**Task:** P1a-1 (Decide and document the broker IPC mechanism)  
**Status:** COMPLETED  
**Date:** 2026-07-06  
**Coder:** Claude (agent)

---

## Summary

Created a decision record documenting the resolution of BD1 (bootstrap-blocking decision) from the Fable plan: **unix domain socket + JSON-RPC 2.0** is the broker IPC mechanism.

**Decision record:** `/Users/yklin/Code/hermes/docs/decisions/001-broker-ipc-mechanism.md`  
**Report location:** this file

---

## Artifact: Decision Record File

### Path
`/Users/yklin/Code/hermes/docs/decisions/001-broker-ipc-mechanism.md` (created; repo layout created `docs/decisions/` as no prior ADR/decisions convention existed)

### Sections Present (Requirement REQ-01)
1. **Decision statement** — unix domain socket + JSON-RPC 2.0 for broker IPC (single-host, expandable to remote via transport swap)
2. **Context** — the P1a architecture requires IPC; crash safety, durability, launchd compatibility, and credential exposure all depend on the choice
3. **Alternatives considered:**
   - File queue (rejected: latency, concurrency hazards, FSEvents unreliability in launchd non-GUI)
   - Local HTTP server (rejected: network exposure surface, port collision risk, macOS launchd brittleness)
   - Unix domain socket + JSON-RPC (adopted: local-only, simple framing, launchd-compatible, transport-agnostic for future multi-host)
4. **Consequences** — immediate impact on P1a codebase (jsonrpc.py, server.py, broker_client.py), medium-term implications (P1b multi-host via RPC transport swap, credential rotation), and residual risks with mitigations
5. **Related decisions** — cross-references to P1a-2 (server), P1a-4 (approval nonce), P1a-7 (remote stub)

### Grounding (REQ-01 rationale sourced from design + plan)
- **Design reference:** P1a-design.md §1.2 (JSON-RPC method set), §1.3 (integration points), §4.3 (remote stub proves transport-agnosticity)
- **Plan reference:** hermes-fable-plan.md §8:548 (BD1 decision statement), §1a.1 (architecture narrative)
- **Why UDS:** explicitly documented rationale section "Why this wins" (6 technical points)
- **Why rejected:** explicit "Pros / Cons / Verdict" for file queue and HTTP alternatives

---

## Convention Compliance (REQ-02)

**Repo ADR convention:** None existed in the codebase. `docs/decisions/` was created as a sensible canonical location following common ADR practice.

**Naming:** `001-broker-ipc-mechanism.md` follows ADR-NNN pattern, stable and sortable.

**Precedent:** No pre-existing decision records found; the decision record becomes the first entry. File structure is parallel to other `.md` docs (title + sections + cross-links).

---

## Traceability

| Requirement | Evidence |
|---|---|
| REQ-01: Decision record exists with 4 sections (decision, context, alternatives, consequences) | File `docs/decisions/001-broker-ipc-mechanism.md` contains all 4; sections titled and substantive |
| REQ-01: References P1a design §1 (IPC mechanism section) | Link to P1a-design.md §1.2, §1.3 in decision record + context section |
| REQ-01: Documents context (why a separate egress-owning process needs local IPC) | Context section explains crash safety, durability, launchd compatibility implications |
| REQ-01: Alternatives considered + rationale for rejection | "Alternatives Considered" section: file queue (3 cons), HTTP server (3 cons), adopted choice (6 justifications) |
| REQ-01: Consequences (single-host now, retrofittable to remote workers per P1a-7) | Consequences section §Medium-term notes transport-agnostic RPC; remote_stub.py (P1a-7) can swap UDS for TCP |
| REQ-02: Matches repo convention or sensible path | No convention existed; created `docs/decisions/` as standard practice; naming `001-*` is stable and sortable |

---

## Next Steps

- **P1a-2 (P1a-b):** Implement broker server and JSON-RPC layer (`broker/server.py`, `broker/jsonrpc.py`, `broker_client.py`) using the decision record as specification
- **P1a-3..P1a-8:** Dependent phases (held store, approval, safe-lane, worker launcher, egress re-route) reference this decision for socket path, method names, and RPC semantics
- **P1a-7 (P1a-g):** Remote-worker stub uses the same RPC method set over a different transport (deferred to post-P1a per design §4.3)

---

## Self-Verification Checklist

- [x] Decision record created and placed in canonical location
- [x] All 4 sections present and substantive (not stubs)
- [x] Context grounded in P1a design doc and plan
- [x] Alternatives enumerate actual rejected options with rationale
- [x] Consequences map to immediate code impact + future expansion path
- [x] Traceability matrix filled; every REQ has evidence
- [x] File is in repo (`docs/decisions/`) accessible to all P1a tasks
- [x] File is NOT committed yet (per task constraint "do not commit/push")
