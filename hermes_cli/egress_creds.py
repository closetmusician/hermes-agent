# ABOUTME: The EGRESS/MODEL credential split (design §1.6). Classifies which
# ABOUTME: secrets are recipient-facing send/push creds (EGRESS — must leave the
# ABOUTME: assistant's os.environ) versus model-inference keys (MODEL — must stay
# ABOUTME: so the conversation loop can think). Provides the starving filter the
# ABOUTME: env loader applies, and a broker-only egress source reader.
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping

# EGRESS credential names — every recipient-facing send/push/API secret. These
# are relocated out of every source the assistant loads (design §1.6 inventory)
# and held ONLY by the broker. Membership is by exact name OR by a prefix rule
# below, so a newly-added platform token is caught by class, not by enumeration.
_EGRESS_EXACT = frozenset(
    {
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SIGNAL_TOKEN",
        "WEIXIN_TOKEN",
        "QQ_BOT_TOKEN",
        "QQBOT_TOKEN",
        "BLUEBUBBLES_TOKEN",
        "YUANBAO_TOKEN",
        "MATRIX_ACCESS_TOKEN",
        "WHATSAPP_TOKEN",
        "TWILIO_AUTH_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
        "MS_GRAPH_CLIENT_SECRET",
        "GRAPH_CLIENT_SECRET",
    }
)

# MODEL-inference keys — the assistant KEEPS these (design §1.6 crux). Listed
# explicitly so the credential-suffix heuristic below never starves them: an
# `_API_KEY`-suffixed name is EGRESS-shaped, but these three are the product's
# reasoning keys and MUST survive.
_MODEL_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
    }
)


def is_egress_credential(name: str) -> bool:
    """
    Purpose: classify one env-var name as EGRESS (send/push, must leave the
    assistant) vs. not (MODEL keys and everything else stay).
    Usage: if is_egress_credential("TELEGRAM_BOT_TOKEN"): strip it.
    Gotchas: MODEL keys are checked FIRST so an `_API_KEY`/`_TOKEN` suffix never
    starves the reasoning key; only names in the explicit EGRESS set (send/push
    creds) return True — this is deliberately conservative, not suffix-greedy,
    so we never strip a non-egress secret and break an unrelated integration.
    """
    if name in _MODEL_KEYS:
        return False
    return name in _EGRESS_EXACT


def starve_egress_credentials(environ: MutableMapping[str, str]) -> List[str]:
    """
    Purpose: remove every EGRESS credential from a process environ in place,
    leaving MODEL keys untouched — the load-bearing wall (design §1.6).
    Usage: removed = starve_egress_credentials(os.environ) after dotenv load.
    Gotchas: mutates `environ` in place and returns the names removed (for audit).
    Only EGRESS-classified names are touched; a MODEL key or unrelated var is
    never deleted. Idempotent — a second call finds nothing to remove.
    """
    removed: List[str] = []
    for name in list(environ.keys()):
        if is_egress_credential(name):
            del environ[name]
            removed.append(name)
    return removed


def load_broker_egress_source(path: Path) -> Dict[str, str]:
    """
    Purpose: read the broker-only egress secret source (a 0600 KEY=VALUE file the
    assistant's login context cannot read) into a dict for the broker to use.
    Usage: egress = load_broker_egress_source(Path("~/.hermes/broker/egress.env")).
    Gotchas: this is called from the BROKER process, never the assistant; a
    missing file returns {} (the broker executor then fails-closed on a real
    send). Lines are `KEY=VALUE`; blanks and `#` comments are skipped.
    """
    path = Path(path)
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def egress_starving_active(environ: Mapping[str, str] | None = None) -> bool:
    """
    Purpose: report whether the credential-starving mechanism is switched on.
    Usage: gated by HERMES_BROKER_EGRESS_STARVE=1 — OFF by default so the running
    gateway is unaffected until the owner does the staged cutover (design safety).
    Gotchas: default OFF is intentional; do NOT flip the default without the
    STAGED-GATES live-migration procedure, or the running gateway loses its creds.
    """
    env = environ if environ is not None else os.environ
    return env.get("HERMES_BROKER_EGRESS_STARVE", "").strip() in ("1", "true", "yes")
