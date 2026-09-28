"""MFG-C2-106 — post_process node: ImpactReportFinalize (S-3 output gate + S-4 audit).

S-3: verify **per-assessment** citation completeness (each grounded/matched ECO impact must cite its BOM
master-data source), re-redact any residual credential-form / My-Number / email / phone token from the
serialized envelope (defense-in-depth), and append the mandatory advisory disclaimer (DRAFT deliverable;
binding disposition via the HumanGate). S-4: emit an audit event (status kind / counts / risk aggregates /
error_code only — no PII, never raw ECO free-text). Runs on both the full report and the out-of-scope safe
branch.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.pre_process_node import _hygiene
from src.utils.audit import emit_trace_event

# The disclaimer intentionally carries the literal tokens "DRAFT" and "HumanGate" — this is a draft
# deliverable and the binding disposition decision is made by an authorized human at the HumanGate.
_DISCLAIMER = (
    "本レポートは BOM マスタと供給される ECO 明細に基づく決定論的な参考 (DRAFT) 影響評価であり、"
    "在庫処置 (use-as-is / rework / scrap) の最終判断ではありません。実際の処置・コスト・スケジュール確定は"
    "生産技術 / QA 部門の権限者による HumanGate 承認を必須とします。本エージェントは読み取り専用で、"
    "BOM・在庫データを更新せず、処置を自動実行しません (advisory-only)。"
)
_DISPOSITION_KINDS = frozenset({"report", "out_of_scope"})


class PostProcessNode(FunctionNode):
    """Verify citations, re-redact, append the DRAFT/HumanGate disclaimer, emit audit."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 preservation check: DRAFT + HumanGate disclaimer present in the output envelope.

        SDK 1.0.0 contract: receives the **result dict from ``execute()``**; returns the (possibly
        filtered) result. MAY raise to block an output missing the mandatory disclaimer.
        """
        out = result.get("formatted_output", "")
        if out and ("DRAFT" not in out or "HumanGate" not in out):
            raise ValueError("S-3: mandatory DRAFT/HumanGate disclaimer missing from output")
        return dict(result)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(state.get("result", "{}") or "{}")
        status_kind = report.get("status_kind")

        assessments = report.get("assessments", [])
        citations = report.get("citations", [])
        # S-3 per-entry authoritative correspondence: every matched (grounded) assessment must carry BOTH
        # its own local citation AND an exact top-level {eco_id, source} citation for the same ECO (a
        # non-empty citation list, or a citation belonging to a different ECO, is not enough). eco_id can
        # repeat across parts, so match on the (eco_id, source) pair set. Any missing/mismatched matched
        # assessment fails closed.
        grounded = [a for a in assessments if a.get("matched")]
        cited_pairs = {(c.get("eco_id"), c.get("source")) for c in citations if c.get("source")}
        citation_complete = (
            all(a.get("citation") and (a.get("eco_id"), a.get("citation")) in cited_pairs for a in grounded)
            if grounded
            else (status_kind in _DISPOSITION_KINDS)
        )

        # ── S-3 fail-closed: a grounded deliverable with any uncited assessment is WITHHELD ──────────
        # (routed to human review) rather than shipped. The body is emptied; error_code surfaces to the
        # terminal audit; the disclaimer and HumanGate stay. Injection / no-data paths are unaffected.
        if grounded and not citation_complete:
            error_code = state.get("error_code") or "CITATION_INCOMPLETE"
            blocked = {
                "status_kind": "needs_review",
                "assessments": [],
                "summary": None,
                "citations": [],
                "citation_complete": False,
                "human_review_required": True,
                "approver_role": report.get("approver_role"),
                "message": "citation incomplete for one or more grounded ECOs — deliverable withheld "
                "and routed to human review (fix BOM master-data reference).",
            }
            out_str = self._finalize(blocked)
            emit_trace_event(
                "post_process.citation_blocked",
                {"status_kind": "needs_review", "grounded_count": len(grounded), "error_code": error_code},
                state,
            )
            return {
                "formatted_output": out_str,
                "disclaimer": _DISCLAIMER,
                "audit_logged": True,
                "error_code": error_code,
                "status": AgentStatus.SUCCESS.value,
            }

        formatted = {
            "status_kind": status_kind,
            "assessments": assessments,
            "summary": report.get("summary"),
            "citations": citations,
            "citation_complete": citation_complete,
            "human_review_required": report.get("human_review_required", True),
            "approver_role": report.get("approver_role"),
            "message": report.get("message"),
        }
        out_str = self._finalize(formatted)

        emit_trace_event(
            "post_process.complete",
            {
                "status_kind": status_kind,
                "eco_count": len(assessments),
                "citation_complete": citation_complete,
                "human_review_required": True,
                "error_code": state.get("error_code"),
            },
            state,
        )
        return {
            "formatted_output": out_str,
            "disclaimer": _DISCLAIMER,
            "audit_logged": True,
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _finalize(body: dict[str, Any]) -> str:
        """S-3 output redaction on the report body, then attach the (never-redacted) disclaimer.

        The whole-report redactor (`_hygiene`: credential / My-Number / email / phone / company) runs on
        the serialized body BEFORE the disclaimer is added, so the mandatory DRAFT/HumanGate disclaimer is
        preserved verbatim. Redaction masks are JSON-safe, so re-parsing the redacted body is lossless.
        """
        redacted = _hygiene(json.dumps(body, ensure_ascii=False))
        try:
            final = json.loads(redacted)
        except ValueError:  # pragma: no cover - masks are JSON-safe; defensive only
            final = dict(body)
        final["disclaimer"] = _DISCLAIMER
        return json.dumps(final, ensure_ascii=False)
