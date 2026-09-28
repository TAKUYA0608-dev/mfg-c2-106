# MFG-C2-106 — Manufacturing Engineering Change Order Production Impact Assessment Agent

> **Category**: Cat 2 (domain workflow (a job to be done))
> **Industry**: Manufacturing

## Overview

Assesses the production impact of a batch of engineering change orders. Given a JSON payload with a scope and ECO records (eco id, part number, change type such as form-fit-function, material substitution or documentation only, effectivity, notes), the agent looks each part up in a seeded BOM master, derives the affected assemblies, classifies the inventory and WIP disposition (use as is, rework, scrap, use with deviation), estimates schedule impact from replenishment lead time and cost impact, assigns a risk tier and returns per-ECO assessments, a summary, an approver_role placeholder, citations to the BOM master records and a draft disclaimer. Everything is deterministic; no LLM is used. Every assessment is marked as requiring a person's decision — the agent never executes a disposition or writes to the source feed — a part not found in the BOM master yields no assessment, ECO and part identifiers are replaced by opaque tokens, and an assessment without a master-data citation causes the report to be withheld. The BOM master shipped here is a small seeded sample — replace it with your own.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | 3.11 or later (`requires-python = ">=3.11"`) |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
