# What Never Graduates

Source of truth: `docs/plans/harness/fable/p2/P2-design.md` §3.2. This file is a
member of the immutable ring (`factory/immutable_ring.py` `RING_PATHS`) — a worker
diff that touches it is categorically rejected at merge-submission.

These capabilities are permanently outside any trust-tier graduation. No matter
how high a worker's or repo's trust tier climbs (P1b onward), the factory never
auto-grants any of them to a worker:

- **Pushing to a protected branch without a held, owner-approved merge card.**
  Every merge to `main` / `master` / any `protected_branches` entry rides the
  broker's held-approval surface. Auto-merge for high trust is a *reduction in
  which merges hold*, never a bypass of the ring or the broker executor.
- **Editing the enforcement boundary.** `broker/**`, `broker_client.py`, the
  factory guard code (`factory/immutable_ring.py`, `factory/injection_scan.py`,
  `factory/cost_stops.py`, `factory/worker_env.py`), the trust policy, and this
  list. A worker can never author a diff that weakens the gate that judges it.
- **Holding an egress credential.** Workers run with a replacement env (design
  §2.3): no `GITHUB_TOKEN`, no messaging tokens, no cloud secrets — only the one
  model-inference key. This does not graduate; egress stays broker-only forever.
- **`--dangerously-skip-permissions`.** Categorically forbidden in every worker
  argv, at every trust tier.
- **Self-approving a merge.** The assistant / supervisor holds no approval nonce;
  approval is a human tap on Telegram. Trust never mints a self-approval path.

If a future phase proposes graduating any item above, that is a maintainer
decision requiring a human-reviewed commit to the enforcement boundary — never a
worker-authored change, and never an implicit consequence of a trust-tier bump.
