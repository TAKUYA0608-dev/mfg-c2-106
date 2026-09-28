"""MFG-C2-106 — pre_process node: EcoRequestNormalize (S-1 input validation + hygiene + slot extraction).

Accepts a structured JSON request (an ``ecos[]`` array of engineering-change-order rows — each with a
``part_no`` / ``change_type`` / ``effectivity`` and optional free-text ``notes`` / ``requested_by`` — plus
an optional ``scope``) or NL text, normalizes it (NFKC), enforces S-1/S-2, and extracts the analysis
slots. The agent is **read-only**: it never mutates the source BOM/inventory feed.

S-1 metadata hygiene (design promise): every field written to ``validated_input`` is passed through
``_hygiene`` — credential-form tokens / My-Number (12-digit) / email / phone are redacted from free-text,
and a ``requested_by`` person field is replaced with an opaque role token. Part / assembly identifiers are
non-PII business master-data references and are retained verbatim for grounding.

Degraded rejects are handled **inside execute()** so the real ``Graph().invoke()`` path (where the
S-2 hook may or may not fire depending on the runtime) always degrades identically: injection / oversize /
empty set ``status=SUCCESS`` + ``error_code`` (never ERROR), discard the body, and let post_process deliver
the out-of-scope safe answer + disclaimer + S-4 audit. The ``_extra_security_gate_input`` hook mirrors the
same screen as defense-in-depth (SDK 1.0.0: MUST NOT raise; keeps status SUCCESS — never short-circuits).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.service import is_part_reference, safe_identifier
from src.utils.audit import emit_trace_event

_MAX_INPUT = 200_000  # ECO batches carry many rows → larger cap than a chat prompt
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
    "###system",
    "<|im_start|>",
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DEGRADED_CODES = frozenset({"INJECTION_REJECTED", "INPUT_TOO_LONG"})

# ── S-1 field-level hygiene patterns (redacted before any field is persisted) ─
# Credential-form tokens are matched by *shape*; prefixes are assembled at runtime so no literal
# credential appears in source (gate-credential-scan safe).
_CRED_PREFIXES = ("sk" + "-", "AK" + "IA", "gh" + "p_", "xox" + "b-")
_CRED = re.compile(
    r"(?:" + "|".join(re.escape(p) for p in _CRED_PREFIXES) + r")[A-Za-z0-9_\-]{12,}"
    r"|(?:api[_-]?key|secret|token|password)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_MY_NUMBER = re.compile(r"(?<![\d.])\d{12}(?![\d.])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}")
_PHONE = re.compile(r"(?<![\d.])0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}(?![\d.])")
# JP + EN company names. JP = the legal-form keyword with adjacent name chars; EN = a Capitalized
# phrase followed by a corporate suffix. Bare "Co" is deliberately excluded (over-redaction of prose),
# and the EN form requires an explicit corp suffix so deterministic action text is never touched.
_COMPANY = re.compile(
    r"[^\s、。,.\"']{0,20}(?:株式会社|有限会社|合同会社)[^\s、。,.\"']{0,20}"
    r"|\b[A-Z][A-Za-z0-9&'.\-]*(?:[ ][A-Z][A-Za-z0-9&'.\-]*){0,4}[ ]+"
    r"(?:Inc|Corp|Corporation|Ltd|Limited|LLC|GmbH|PLC|KK)\b\.?",
)
_CRED_MASK = "[CREDENTIAL-REDACTED]"
_MY_NUMBER_MASK = "[MY-NUMBER-REDACTED]"
_EMAIL_MASK = "[EMAIL-REDACTED]"
_PHONE_MASK = "[PHONE-REDACTED]"
_COMPANY_MASK = "[COMPANY-REDACTED]"
_REQUESTER_MASK = "[REQUESTER-REDACTED]"
_PART_REF_MASK = "[PART-REF-REDACTED]"

# Caller identifiers echoed into the deliverable are handled by two SEPARATE, purpose-built helpers in
# src.services.service (whitelist-by-construction — a loose syntactic allowlist is NOT used, because a
# bare name like ``Alice`` / ``John.Smith`` / ``TaroYamada`` slips through one):
#   • eco_id  → safe_identifier(): UNCONDITIONALLY tokenized to an opaque ``eco:<sha8>`` surrogate.
#   • part_no → is_part_reference(): passed through only on a strict part/assembly-reference full match
#               (a name can never match); anything else is masked with _PART_REF_MASK.


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _screen(raw: str) -> str | None:
    """Return a degraded error_code (INPUT_TOO_LONG / INJECTION_REJECTED) or None. Shared by hook + execute."""
    if len(raw) > _MAX_INPUT:
        return "INPUT_TOO_LONG"
    if any(marker in _nfkc(raw).lower() for marker in _INJECTION_MARKERS):
        return "INJECTION_REJECTED"
    return None


def _hygiene(text: str) -> str:
    """Redact credential-form / My-Number / email / phone / company-name tokens and strip control chars.

    Used at S-1 (input, before persistence) and S-3 (output, whole-report re-redaction).
    """
    clean = _CONTROL.sub("", text or "")
    clean = _CRED.sub(_CRED_MASK, clean)
    clean = _MY_NUMBER.sub(_MY_NUMBER_MASK, clean)
    clean = _EMAIL.sub(_EMAIL_MASK, clean)
    clean = _PHONE.sub(_PHONE_MASK, clean)
    clean = _COMPANY.sub(_COMPANY_MASK, clean)
    return clean


class PreProcessNode(FunctionNode):
    """Validate the ECO request, redact PII/credentials, and extract its ecos / scope slots."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 defense-in-depth mirror. SDK 1.0.0: MUST NOT raise, MUST return dict(state).

        Sets ``error_code`` on injection/oversize but keeps ``status`` SUCCESS — it never short-circuits
        ``__call__`` (the authoritative degrade happens in ``execute()``, so both the hook-firing runtime
        and the local SDK stub runtime degrade identically).
        """
        out = dict(state)
        code = _screen(state.get("user_input", "") or "")
        if code:
            out["error_code"] = code
        return out

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        raw = state.get("user_input", "") or ""
        input_context = state.get("input_context", {})  # read-only [C1]
        enriched = json.dumps(
            {
                "source": "ManufacturingEngineeringChangeOrderProductionImpactAgent",
                "channel": input_context.get("channel", "unknown"),
            },
            ensure_ascii=False,
        )

        # Authoritative degraded screen (also covers the local SDK stub where the S-2 hook does not fire).
        code = _screen(raw) or (state.get("error_code") if state.get("error_code") in _DEGRADED_CODES else None)
        if code:
            emit_trace_event("eco_normalize.rejected", {"reason": code}, state)
            return {
                "validated_input": "{}",
                "input_format": "rejected",
                "enriched_context": enriched,
                "error_code": code,
                "status": AgentStatus.SUCCESS.value,
            }

        if not raw.strip():
            emit_trace_event("eco_normalize.rejected", {"reason": "empty_input"}, state)
            return {
                "validated_input": "{}",
                "input_format": "empty",
                "enriched_context": enriched,
                "error_code": "INPUT_REJECTED",
                "status": AgentStatus.SUCCESS.value,
            }

        slots, fmt = self._parse(_nfkc(raw))
        emit_trace_event(
            "eco_normalize.validated",
            {"input_format": fmt, "eco_count": len(slots["ecos"]), "scope": slots.get("scope")},
            state,
        )
        return {
            "validated_input": json.dumps(slots, ensure_ascii=False),
            "input_format": fmt,
            "enriched_context": enriched,
            "status": AgentStatus.SUCCESS.value,
        }

    def _parse(self, text: str) -> tuple[dict[str, Any], str]:
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return {"ecos": [], "scope": None}, "text"
        if isinstance(obj, dict):
            ecos = obj.get("ecos")
            # scope is free-text → always pass through input hygiene (no bypass); None stays None.
            raw_scope = obj.get("scope")
            scope = _hygiene(str(raw_scope)) if raw_scope not in (None, "") else None
            return {"ecos": self._clean_ecos(ecos if isinstance(ecos, list) else []), "scope": scope}, "json"
        if isinstance(obj, list):  # bare ecos array
            return {"ecos": self._clean_ecos(obj), "scope": None}, "json"
        return {"ecos": [], "scope": None}, "text"

    def _clean_ecos(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Normalize + hygiene-redact each ECO row. Rows lacking a part reference are dropped.

        Caller identifiers echoed into the deliverable are handled by purpose-built helpers so PII can
        never leak into the output citation/assessment (whitelist-by-construction — only these fields are
        copied, so an arbitrary caller field, e.g. a forged ``source`` or a PII ``internal_note``, is
        dropped): ``eco_id`` is UNCONDITIONALLY tokenized to an opaque ``eco:<sha8>`` surrogate (a caller
        value that is a name — even a bare no-space name — never survives; a missing eco_id uses a safe
        internal fallback); ``part_no`` passes through only on a strict part-reference full match, else it
        is masked. Free-text fields go through ``_hygiene``; the requester name → an opaque role token.
        """
        cleaned: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            part_no = row.get("part_no") or row.get("part")
            if not part_no:
                continue
            part_ref = str(part_no).strip()
            raw_eco = str(row.get("eco_id") or row.get("id") or "").strip()
            cleaned.append(
                {
                    # eco_id: unconditional opaque tokenize (caller value) or a safe internal fallback.
                    "eco_id": safe_identifier(raw_eco) if raw_eco else f"ECO-{len(cleaned) + 1}",
                    # part_no: strict part-reference passthrough (must survive for BOM grounding) or masked.
                    "part_no": part_ref if is_part_reference(part_ref) else _PART_REF_MASK,
                    "change_type": _hygiene(str(row.get("change_type") or "")),
                    "effectivity": _hygiene(str(row.get("effectivity") or "")),
                    "notes": _hygiene(str(row.get("notes") or "")),
                    # person field → opaque role token (never persist a name)
                    "requested_by": _REQUESTER_MASK if row.get("requested_by") else None,
                }
            )
        return cleaned
