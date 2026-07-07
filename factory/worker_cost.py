# ABOUTME: Per-worker cost accounting for the Fable factory (P4-b, reviews F5/F8).
# ABOUTME: Prices claude/codex/openrouter token usage → USD, folds actual spend into
# ABOUTME: the DailyBudgetLedger, and counts a resume's re-spent phase as REAL spend
# ABOUTME: (not just a reservation returned). Defines TOLERANCE so reconciliation vs a
# ABOUTME: provider-reported figure can CATCH drift instead of self-referencing.
"""
Per-worker cost accounting + the reconciliation tolerance.

Design authoritative source: docs/plans/harness/fable/p4/P4-design.md §4.4 (per-worker
cost) + Revision v2 §R3 (F5/F8: resume re-spend counted, tolerance defined,
provider-reconciliation). Review: docs/plans/harness/fable/p4/P4-review.md F5/F8.

What this module owns:
  * ``usd_for(worker, model, input_tok, output_tok)`` — price a phase's token usage.
    Codex is post-hoc metered (capabilities.json codex.budget_flag:false) so its cost
    is folded in the instant its --json usage parses; OpenRouter is priced off the
    response usage block. An unpriced model uses a conservative fallback price (never
    zero — accounting fails toward COUNTING spend, never toward hiding it).
  * ``record_job_spend(ledger, job_id, cap_usd, actual_usd, ...)`` — commit a phase's
    real spend. On a crash+resume the pre-crash burn AND the re-run burn are BOTH
    committed (F5): the resumed phase re-runs, the provider bills both, so the ledger
    must count both to match what the provider actually billed.
  * ``TOLERANCE`` — the numeric reconciliation bound (F5). A gap between the ledger and
    a provider-reported figure that EXCEEDS this is a real accounting bug (hard fail),
    not "softer post-hoc."
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Price table — USD per 1M tokens (input, output). Approximate list prices; the
# exact figures are owner-tunable and do not affect the CAS (the ledger bounds the
# night regardless). What matters for the gates is that pricing is positive and
# monotonic in token count, and that an unknown model still prices non-zero.
# ---------------------------------------------------------------------------
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # claude
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (15.0, 75.0),
    "claude-haiku": (0.80, 4.0),
    # codex (OpenAI)
    "gpt-5": (1.25, 10.0),
    # openrouter overflow (third-party)
    "zhipuai/glm-5": (0.60, 2.20),
    "moonshot/kimi-k2": (0.55, 2.20),
    "deepseek/deepseek-r1": (0.55, 2.20),
    "deepseek/deepseek-v3": (0.28, 1.10),
}

# Conservative fallback for an unpriced model — priced at the top observed rate so an
# unknown model is never UNDER-counted (design §R3: fail toward counting spend).
_FALLBACK_PER_MTOK: tuple[float, float] = (15.0, 75.0)

# Per-job reconciliation tolerance (design §R3). The drift sources are (i) Codex
# post-hoc overshoot of at most one phase's tokens and (ii) crash re-spend of at most
# MAX_RESUMES phases. TOLERANCE = max($0.25, 15% × per_job_cap × MAX_RESUMES): the
# absolute floor absorbs cross-table rounding, the proportional term absorbs the
# bounded re-spend. A reconciliation gap ABOVE this is a real accounting bug.
MAX_RESUMES = 3
_TOLERANCE_FLOOR_USD = 0.25
_TOLERANCE_PER_JOB_CAP_USD = 2.0  # the design's default per-job cap
TOLERANCE = max(
    _TOLERANCE_FLOOR_USD,
    0.15 * _TOLERANCE_PER_JOB_CAP_USD * MAX_RESUMES,
)


def usd_for(worker: str, model: str, *, input_tok: int, output_tok: int) -> float:
    """
    Price a phase's token usage in USD for the given worker/model.

    Purpose: the one conversion from provider-reported token counts to dollars, used
    by both the per-job cost log and the reconciliation oracle. Positive and monotonic
    in token count; an unpriced model uses a conservative (non-zero) fallback so spend
    is never hidden by an unknown name.
    Usage: cost = usd_for("claude", "claude-sonnet", input_tok=40_000, output_tok=15_000)
    Gotchas:
      * worker is accepted for symmetry/logging; pricing keys on ``model``. A model not
        in the table falls back to the top rate (over-counts rather than under-counts).
      * Prices are per 1,000,000 tokens; the result is a float dollar amount.
    """
    in_rate, out_rate = _PRICE_PER_MTOK.get(model, _FALLBACK_PER_MTOK)
    return (input_tok / 1_000_000.0) * in_rate + (output_tok / 1_000_000.0) * out_rate


def usd_from_usage(worker: str, model: str, usage: Any) -> float:
    """
    Price a provider ``usage`` block (claude JSON / codex --json / OpenRouter) → USD.

    Purpose: adapt the heterogeneous per-provider usage shapes to usd_for. Reads the
    common token fields tolerantly (input/prompt, output/completion) so one call site
    handles all three workers.
    Usage: cost = usd_from_usage("openrouter", model, result["usage"])
    Gotchas: missing/unknown fields count as zero tokens for that side (not an error);
    the model price still applies to whatever tokens ARE reported.
    """
    if not isinstance(usage, dict):
        return 0.0
    input_tok = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("input")
        or 0
    )
    output_tok = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("output")
        or 0
    )
    return usd_for(worker, model, input_tok=input_tok, output_tok=output_tok)


def record_job_spend(
    ledger: Any,
    *,
    job_id: str,
    cap_usd: float,
    actual_usd: float,
    day: str | None = None,
    provider: str = "__nightly__",
) -> None:
    """
    Commit one phase's REAL spend into the ledger (F5: resume re-spend counted).

    Purpose: fold a phase's metered actual dollars into the DailyBudgetLedger and
    release its reservation. Called once per phase completion AND once per resume
    re-run — so a crash+resume records BOTH the pre-crash burn and the re-run burn,
    matching what the provider actually billed (the review's F5 fix: the reservation
    is not merely returned; the burned tokens are committed as spend).
    Usage: record_job_spend(ledger, job_id=jid, cap_usd=2.0, actual_usd=0.42)
    Gotchas:
      * provider '__nightly__' commits to the nightly row; 'openrouter' commits to
        BOTH the sub-ceiling and nightly rows (commit_spend handles the fan-out).
      * job_id is carried for the per-job cost log / forensics; the ledger CAS keys on
        (day, provider), not job_id.
    """
    ledger.commit_spend(
        job_cap_usd=cap_usd,
        actual_spend_usd=actual_usd,
        day=day,
        provider=provider,
    )
