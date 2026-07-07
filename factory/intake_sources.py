# ABOUTME: P3-f multi-intake wiring: spec-doc, greenfield, and voice-note sources.
# ABOUTME: Each source injection-scans its input BEFORE calling task_splitter.split,
# ABOUTME: returns a TaskGraph | ParkedSpec (or ScaffoldPlan for greenfield), and
# ABOUTME: produces a stable intake_source_hash for idempotent re-processing.
# ABOUTME: Design source: P3-design.md §8 (REQ-04c) + §8.1 test list.
"""
Multi-intake sources — spec-doc, greenfield, voice-note → TaskGraph | ScaffoldPlan.

Design authoritative source:
  docs/plans/harness/fable/p3/P3-design.md §8

Three intake pipelines, each producing the same stable-contract output so the
scheduler / spec-review gate can consume them without special-casing the source:

  spec-doc   — a dropped .md/.txt/PRD file (or raw text) →
               injection-scan → split(text) → TaskGraph | ParkedSpec

  greenfield — a brand-new idea (no code yet) →
               injection-scan → ScaffoldPlan(kind="scaffold-plan",
               stages=[SCAFFOLD,SPEC,IMPLEMENT]) + a spec_dict for the scheduler

  voice-note — audio bytes → transcribe (external, mocked boundary) →
               injection-scan transcript → split(transcript) → TaskGraph | ParkedSpec

SECURITY CONTRACT (REQ-03):
  Every source MUST call injection_scan.scan() on its raw input text BEFORE
  the text reaches task_splitter.split() or any other consumer.  A finding
  fences the text via injection_scan.fence() so injections are quarantined,
  visible, but cannot issue instructions to downstream workers.

IDEMPOTENCY:
  Each source produces an intake_source_hash (SHA-256 over canonical text) so
  the scheduler / supervisor can detect and skip duplicate submissions.

AI BOUNDARIES:
  * task_splitter.split() is the ONLY AI boundary for spec-doc and voice-note.
    In tests it is patched via patch("factory.intake_sources.split", ...).
  * The `transcribe` callable passed to ingest_voice_note() is the ONLY audio
    transcription boundary.  In tests it is replaced with a MagicMock.
  * Greenfield uses NO AI in this module; it returns a plain ScaffoldPlan.
    The scheduler is responsible for spawning the SCAFFOLD worker stage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from factory.injection_scan import fence, scan
from factory.task_graph import ParkedSpec, TaskGraph
from factory.task_splitter import split


# ---------------------------------------------------------------------------
# Stage enum for the scaffold-plan kind
# ---------------------------------------------------------------------------

class ScaffoldStage(Enum):
    """
    Stages in the scaffold-plan pipeline.

    Purpose: three-stage pipeline for greenfield projects — SCAFFOLD produces the
    project skeleton, SPEC produces the feature spec, IMPLEMENT does the work.
    Usage: StagePlan.stages will contain [SCAFFOLD, SPEC, IMPLEMENT] for greenfield.
    Gotchas: values are lowercase strings so they compose naturally with two_stage.Stage
    values ("spec", "implement") — no collision because "scaffold" is new.
    """

    SCAFFOLD = "scaffold"
    SPEC = "spec"
    IMPLEMENT = "implement"


# ---------------------------------------------------------------------------
# ScaffoldPlan — the greenfield return type
# ---------------------------------------------------------------------------

@dataclass
class ScaffoldPlan:
    """
    Return value for greenfield intake: a scaffold-plan kind + spec dict.

    Purpose: wraps the three-stage plan (SCAFFOLD→SPEC→IMPLEMENT) together
    with a spec_dict in the canonical intake.py shape so the scheduler can
    enqueue a scaffold-plan job without special-casing greenfield.

    Fields:
      kind      — always "scaffold-plan"
      stages    — [SCAFFOLD, SPEC, IMPLEMENT] in order
      spec_dict — canonical intake spec dict; kind="scaffold-plan",
                  intake_source="greenfield", intake_source_hash=stable SHA-256
      idea      — the original (possibly fenced) idea text

    Usage:
        plan = ingest_greenfield(idea="...", repo="acme", base_branch="main")
        job_spec = plan.spec_dict   # feed to scheduler.enqueue_job(spec)

    Gotchas:
      * spec_dict["spec"] is the fenced idea (if injection was detected) or the
        raw idea.  The scheduler must re-scan the rendered node spec at enqueue
        per design §13.4, but the idea scan here is the first defence.
      * The SCAFFOLD worker stage is not launched by this module; the scheduler
        owns that step after the spec-review gate clears.
    """

    kind: str
    stages: List[ScaffoldStage]
    spec_dict: Dict[str, Any]
    idea: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _stable_hash(text: str) -> str:
    """
    Compute a stable SHA-256 hex digest for use as an intake idempotency key.

    Purpose: same canonical text always produces the same hash so the supervisor's
    has_job_for_intake_hash guard can deduplicate re-submissions.
    Usage: h = _stable_hash("spec-doc:acme:" + text)
    Gotchas: any whitespace variation in text changes the hash; callers must
    normalize / canonicalize text before passing it here.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fence_if_flagged(text: str) -> str:
    """
    Run injection scan and fence the text if any findings are detected.

    Purpose: the single call site that enforces the security contract — every
    source calls this on its raw input before handing the text to the splitter
    or storing it in a spec dict.
    Usage: safe_text = _fence_if_flagged(raw_user_text)
    Gotchas: returns the original text unchanged when there are no findings, so
    callers can use this unconditionally.
    """
    findings = scan(text)
    if findings:
        return fence(text, findings)
    return text


# ---------------------------------------------------------------------------
# Source 1: spec-doc
# ---------------------------------------------------------------------------

def ingest_spec_doc(
    text: str,
    *,
    repo: str,
    base_branch: str,
    defaults: Optional[dict] = None,
) -> Union[TaskGraph, ParkedSpec]:
    """
    Ingest a spec doc (file contents / PRD text) → TaskGraph | ParkedSpec.

    Purpose: the spec-doc intake path — scan the document for injections, fence
    any findings, then pass the (possibly fenced) text to task_splitter.split().
    Returns whatever split() returns (TaskGraph or ParkedSpec).

    Usage:
        result = ingest_spec_doc(text=doc_text, repo="acme", base_branch="main")
        if isinstance(result, ParkedSpec):
            handle_park(result)
        else:
            # result is a TaskGraph; pass to spec-review gate then scheduler
            handle_graph(result)

    Gotchas:
      * NEVER raises — any validation failure produces a ParkedSpec (via split()).
      * injection_scan.scan() is called BEFORE split() — this is the security
        invariant; tests assert the fenced marker is present in split()'s input.
      * The spec_hash on the returned TaskGraph is stable: same text → same hash.
        This is used as the intake idempotency key.
    """
    # SECURITY: scan and fence before the splitter sees the text.
    safe_text = _fence_if_flagged(text)
    return split(safe_text, repo=repo, base_branch=base_branch)


# ---------------------------------------------------------------------------
# Source 2: greenfield
# ---------------------------------------------------------------------------

def ingest_greenfield(
    idea: str,
    *,
    repo: str,
    base_branch: str,
    defaults: Optional[dict] = None,
) -> ScaffoldPlan:
    """
    Ingest a greenfield idea → ScaffoldPlan with kind="scaffold-plan".

    Purpose: the greenfield intake path.  A brand-new idea has no existing code
    so instead of splitting into tasks directly, this returns a ScaffoldPlan with
    the SCAFFOLD→SPEC→IMPLEMENT stage pipeline.  The scheduler will launch the
    SCAFFOLD stage first (to produce a project skeleton + first task graph), then
    SPEC, then IMPLEMENT.

    Usage:
        plan = ingest_greenfield(idea="Build a Redis cache service", repo="cache-svc", base_branch="main")
        schedule_scaffold_job(plan.spec_dict)

    Gotchas:
      * SECURITY: injection scan runs on `idea` before it is stored in spec_dict.
        The fenced text (not the raw idea) enters spec_dict["spec"].
      * No AI is called here; the SCAFFOLD worker stage is the AI boundary.
      * The intake_source_hash is stable: same idea + repo → same hash.
      * kind="scaffold-plan" is the scheduler's signal to use the three-stage
        pipeline instead of the two-stage feature pipeline.
    """
    # SECURITY: scan and fence the idea before it enters any spec dict.
    findings = scan(idea)
    safe_idea = fence(idea, findings) if findings else idea

    # Canonical hash for idempotency: "greenfield:<repo>:<idea>".
    canonical = f"greenfield:{repo}:{idea}"
    source_hash = _stable_hash(canonical)

    stages = [ScaffoldStage.SCAFFOLD, ScaffoldStage.SPEC, ScaffoldStage.IMPLEMENT]

    spec_dict: Dict[str, Any] = {
        "repo": repo,
        "spec": safe_idea,
        "base_branch": base_branch,
        "kind": "scaffold-plan",
        "worker": (defaults or {}).get("worker", "claude"),
        "model": (defaults or {}).get("model", "claude-opus-4-5"),
        "budget_usd": (defaults or {}).get("budget_usd", 1.0),
        "timeout_min": (defaults or {}).get("timeout_min", 30),
        "intake_source": "greenfield",
        "intake_source_hash": source_hash,
    }

    return ScaffoldPlan(
        kind="scaffold-plan",
        stages=stages,
        spec_dict=spec_dict,
        idea=safe_idea,
    )


# ---------------------------------------------------------------------------
# Source 3: voice-note
# ---------------------------------------------------------------------------

def ingest_voice_note(
    audio_bytes: bytes,
    *,
    transcribe: Callable[[bytes], str],
    repo: str,
    base_branch: str,
    defaults: Optional[dict] = None,
) -> Union[TaskGraph, ParkedSpec]:
    """
    Ingest a voice note → (mocked) transcription → TaskGraph | ParkedSpec.

    Purpose: the voice-note intake path — call the transcription boundary to get
    a text transcript, then injection-scan the transcript (it is UNTRUSTED external
    content), then pass the (possibly fenced) transcript to task_splitter.split().

    Usage:
        result = ingest_voice_note(
            audio_bytes=wav_data,
            transcribe=my_stt_function,
            repo="acme",
            base_branch="main",
        )

    Gotchas:
      * `transcribe` is the ONLY transcription boundary.  In tests it is replaced
        with a MagicMock; in production it would be the gateway's existing audio
        path.  This module does NOT spawn a real STT subprocess.
      * SECURITY: injection_scan.scan() runs on the TRANSCRIPT (not the audio
        bytes) BEFORE split() is called.  Audio from Telegram is untrusted;
        a malicious transcript that passed naive STT could carry injection payloads.
      * NEVER raises — any validation failure produces a ParkedSpec (via split()).
      * The spec_hash on the returned TaskGraph is stable: same transcript text
        → same hash (idempotency key for the scheduler).
    """
    # Transcription boundary — the ONLY external call; mocked in tests.
    transcript: str = transcribe(audio_bytes)

    # SECURITY: scan and fence the transcript before the splitter sees it.
    safe_transcript = _fence_if_flagged(transcript)

    return split(safe_transcript, repo=repo, base_branch=base_branch)
