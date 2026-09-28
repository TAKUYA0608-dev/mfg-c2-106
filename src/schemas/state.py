"""MFG-C2-106 — Agent state (Engineering Change Order Production Impact, Cat 2).

ADR-005: State is a flat TypedDict — never a validation/BaseModel instance. Complex fields are stored
as JSON strings (``NotRequired[str]`` + ``# JSON:``); nodes ``json.dumps`` on write / ``json.loads`` on
read (msgpack-safe checkpointing).

S-5 / State Safety: the agent is **read-only** over BOM/inventory master data and processes **no PII** —
part / assembly identifiers are business master-data references, not personal data; free-text and any
requester field are hygiene-redacted at S-1 (credential-form / My-Number / email / phone / name) before
they reach ``validated_input``. The agent produces an advisory deliverable and never auto-executes a
disposition — the final decision is an authorized human via the mandatory HumanGate.

All agent-specific fields are NotRequired (populated progressively; absent at empty-start invoke).
"""

from __future__ import annotations


from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Agent state for the ECO production-impact workflow."""

    # ── pre_process (EcoRequestNormalize, S-1 validated + hygiene-redacted request) ──
    validated_input: str  # JSON: {ecos:[{eco_id, part_no, change_type, effectivity, notes}], scope}
    input_format: str  # "json" | "text" | "empty" | "rejected"
    enriched_context: str  # JSON: {source, channel} (read-only caller context)

    # ── inner workflow (eco_ingest → bom_impact → impact_evaluate → report_generate) ──
    ingest_count: int  # valid ECO records ingested (0 → out-of-scope safe answer)
    eco_impacts: str  # JSON: [{eco_id, part_no, disposition, rationale, citation, ...}]
    evaluations: str  # JSON: [{eco_id, cost_impact_jpy, schedule_impact_days, risk_tier, ...}]
    result: str  # JSON: assembled production-impact report (deliverable)

    # ── post_process (S-3 gate + S-4 audit) ──────────────────────────────────
    formatted_output: str  # JSON: final response envelope (report + HumanGate + disclaimer)
    disclaimer: str  # mandatory DRAFT / HumanGate advisory disclaimer
    audit_logged: bool  # True once the terminal audit event is emitted

    # ── degraded-path signalling (SUCCESS + error_code, never status=ERROR) ──
    error_code: str  # INJECTION_REJECTED | INPUT_TOO_LONG | INPUT_REJECTED | NO_ECO
    error_message: str  # operator-facing detail
