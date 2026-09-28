# Template Design Specification — MFG-C2-106

Manufacturing Engineering Change Order (ECO) Production Impact Assessment Agent — a Cat 2,
read-only, advisory deliverable workflow. Given a batch of engineering change orders, it produces a
deterministic production-impact assessment (affected assemblies, inventory/WIP disposition, schedule and
cost impact, risk tier) grounded in BOM master data, and routes the binding decision to an authorized
human via a mandatory HumanApprovalGate. It never mutates the source feed and never auto-executes a
disposition.

## Position in AgentCore Architecture

- **Agent Class**: ManufacturingEngineeringChangeOrderProductionImpactAgent
- **L1 Base**: **AgentBaseGraph** (Cat 2 — outer 5-node backbone; direct L1 inheritance, no L2)
- **Category**: Cat 2 (multi-step MFG job-to-be-done; domain complexity encapsulated in a `GraphNode` in
  the `main` slot wrapping an inner `BaseGraph`)
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible); complex fields JSON-encoded (ADR-005)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only — **no `config` param**)
  - Graph: composition (`register_nodes()` for node substitution; `GraphNode.get_subgraph()` for the inner workflow)
- **Determinism**: the entire pipeline is **deterministic — no LLM is used anywhere** (no `model` in
  `config/agent.yaml`, no LLM dependency in `pyproject.toml`, no model call in `src/`). Retrieval, disposition
  classification, and impact math are exact BOM where-used lookup + threshold rules → fully auditable.

## Architecture Overview

### Node Configuration (outer 5-node backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | trust level, session, trace | (invoke) | schema/session/trust | InitializeNode (default) |
| pre_process | EcoRequestNormalize — S-1 validate + hygiene-redact + slot extract | user_input | validated_input, input_format, enriched_context, (error_code) | PreProcessNode (FunctionNode) |
| main | EcoImpactWorkflowGraphNode — wraps inner workflow | validated_input | result, ingest_count, error_code, status | GraphNode (composition) |
| post_process | ImpactReportFinalize — S-3 gate + citation completeness + disclaimer + S-4 audit | result | formatted_output, disclaimer, audit_logged | PostProcessNode (FunctionNode) |
| finalize | response metadata | * | response_metadata | FinalizeNode (default) |

### Data Flow (outer)

```
START → initialize → pre_process → main(GraphNode) → {route:status} → post_process → finalize → END
                                        ↓ (retry, unused)
                                      pre_process
```

### Inner Domain Workflow (`src/graph/domain_workflow_graph.py`, 4 steps + HumanGate)

Linear topology with **per-node skip guards** (conditional edges do not propagate across the subgraph
boundary → guards, not `add_conditional_edges`):

```
START → eco_ingest → bom_impact → impact_evaluate → report_generate → END
```

1. **eco_ingest** — count valid ECO records; 0 (rejected / no rows) → `ingest_count=0` + `error_code=NO_ECO`.
2. **bom_impact** — deterministic BOM where-used lookup + inventory/WIP disposition classification
   (`use_as_is` / `rework` / `scrap` / `use_as_is_with_deviation`) with a master-data citation per matched ECO.
3. **impact_evaluate** — deterministic schedule (replenishment lead-time) + cost impact + risk tier.
4. **report_generate** — compose the advisory deliverable **including the mandatory HumanGate**
   (`human_review_required=True` + approver-role placeholder); 0-ingest / rejected → out-of-scope safe answer
   (`citations=[]`, no fabricated assessment).

**HumanGate**: surfaced as an advisory `human_review_required` flag + `approver_role` on the deliverable
(deterministic fail-closed advisory — the agent recommends a disposition but the binding call is an
authorized production/quality engineer). It is not a runtime `interrupt()` (hitl disabled); it is an
explicit, always-present gate on the output.

### State Definition (agent-specific; all `NotRequired`, JSON-encoded per ADR-005)

| Field | Type | Purpose |
|-------|------|---------|
| validated_input | str (JSON) | `{ecos:[{eco_id, part_no, change_type, effectivity, notes}], scope}` (hygiene-redacted) |
| input_format | str | json / text / empty / rejected |
| enriched_context | str (JSON) | `{source, channel}` (read-only caller context) |
| ingest_count | int | valid ECO records ingested (0 → out-of-scope safe answer) |
| eco_impacts | str (JSON) | per-ECO disposition + citation |
| evaluations | str (JSON) | per-ECO cost/schedule/risk |
| result | str (JSON) | assembled production-impact deliverable |
| formatted_output | str (JSON) | final envelope (report + HumanGate + disclaimer) |
| disclaimer | str | mandatory DRAFT / HumanGate advisory disclaimer |
| audit_logged | bool | terminal S-4 audit emitted |
| error_code | str | INJECTION_REJECTED / INPUT_TOO_LONG / INPUT_REJECTED / NO_ECO |

**State Constraints (mandatory):** flat TypedDict only; no JWT/API keys/credentials/PII in State (part &
assembly identifiers are non-PII business master-data references); no Pydantic/dataclass (msgpack).

## Security Design (S-1 … S-5)

- **S-1 trust gate**: every `FunctionNode` subclass (pre_process, post_process, and all four inner nodes)
  declares `required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL`, matching the
  agent-level `required_trust_level` in `config/agent.yaml`. `GraphNode` is exempt (delegates to inner nodes).
- **S-1 field-level input hygiene**: `pre_process` passes **every field written to `validated_input`**
  through `_hygiene` — credential-form tokens / My-Number (12-digit) / email / phone / JP+EN company
  names are redacted from free-text (incl. `scope`, which never bypasses hygiene); a `requested_by`
  person field is replaced with an opaque role token. Caller identifiers that are echoed into the
  deliverable (`eco_id`, `part_no`) are **unconditionally tokenized** via `safe_identifier` (opaque `eco:<sha8>` surrogate; NO syntactic allowlist)
  (`^[A-Za-z0-9_.:/\-]{1,128}$`) — a non-conforming value (customer name / phone / free text) is dropped
  or replaced so it cannot leak into the output citation/assessment. `part_no` is BOM master data required for grounding: looked up against the validated BOM master, and a
  matched assessment carries only fields from that master record (a miss → no grounded assessment → 0-hit safe answer). Result: **no
  PII or credentials persist in State or reach the output**.
- **S-2 input hook** (`_extra_security_gate_input`): SDK 1.0.0 contract — **MUST NOT raise**, returns
  `dict(state)`. It **never short-circuits**: on injection / oversize it flags `error_code` but keeps
  `status=SUCCESS`. The authoritative degrade lives **inside `execute()`** (so the runtime path degrades
  identically whether or not the hook fires): injection / oversize / empty set `status=SUCCESS` +
  `error_code`, **discard the body** (`validated_input="{}"`), and the inner workflow skips to the
  out-of-scope safe answer. **A rejected request is SUCCESS + error_code, never `status=ERROR`** — this
  guarantees `main` → `post_process` still run so the disclaimer, output redaction, and S-4 audit are
  always delivered. `status=ERROR` is reserved for genuine `execute()` exceptions (framework-caught).
- **S-3 output hook** (`_extra_security_gate_output`): receives the `execute()` result delta, **MAY raise**
  to block an output missing the mandatory DRAFT/HumanGate disclaimer. `post_process` enforces
  **per-assessment citation completeness fail-closed**: if any matched/grounded ECO lacks a BOM
  master-data citation, the deliverable is **withheld** — the body is emptied and the output degrades to
  `status_kind=needs_review` + `error_code=CITATION_INCOMPLETE` (surfaced to the terminal S-4 audit),
  keeping the disclaimer and HumanGate — rather than shipping an uncited assessment. Citations are the
  validated BOM master-data source only (never fabricated). The **whole-report S-3 redactor** (`_hygiene`:
  credential / My-Number / email / phone / JP+EN company names) runs on the serialized body **before** the
  disclaimer is attached, so the mandatory disclaimer is preserved verbatim.
- **S-4 audit** (`emit_trace_event` via `src.utils.audit` shim): **every `execute()` path emits at least
  one domain event — including skip / degraded paths** (count-only aggregates: status kind / counts /
  disposition & risk rollups / error_code; never raw ECO free-text → no PII). `node_start`/`node_complete`
  are emitted by `BaseNode.__call__` only.
- **S-5 read-only / grounding**: the agent reads BOM master data and never writes it; 0-hit → out-of-scope
  safe answer (no fabricated assessment).

## Framework Utilization

- **S-2**: `_extra_security_gate_input()` — size cap + prompt-injection markers (mirror of the execute-level screen)
- **S-3**: `_extra_security_gate_output()` — disclaimer preservation (raises) + envelope re-redaction
- **S-4**: `emit_trace_event()` — ≥1 domain event per `execute()`, skip paths included
- **Composition Pattern**: `GraphNode` (subgraph) in `main`, `error_strategy="propagate"`,
  `get_subgraph()` caches the inner graph on the **class attribute** (`EcoImpactWorkflowGraphNode._subgraph`, not `self` — avoids mutable node-instance state per §9); `merge_output` surfaces `result/ingest_count/error_code/status`
  with **outer error_code priority** (`state.get("error_code") or sub_result.get("error_code")`) so a
  pre-stage reject survives to the terminal audit.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `src/` only (no `agents/base/` required); services layer has no framework import

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | **AgentBaseGraph** | AutonomousBaseGraph | **AgentBaseGraph** | Cat 2 fixed 5-node backbone; no autonomous loop / budget |
| Composition pattern | Standalone FunctionNode | **GraphNode (inner workflow)** | **GraphNode-in-main** | Cat 2 multi-step domain workflow (ingest→impact→evaluate→report) |
| Degraded reject | status=ERROR | **SUCCESS + error_code** | **SUCCESS + error_code** | ERROR short-circuits route→finalize, skipping post_process/S-3/S-4 |
| Synthesis | LLM narrative | **deterministic composition** | **deterministic (no LLM)** | auditable BOM/threshold rules; no model dependency |
| HumanGate | runtime interrupt() | **advisory fail-closed flag** | **advisory flag on deliverable** | advisory-only; binding decision by authorized human, no auto-execute |
