# CoScientist v4.5.0 — Reasoning Roles and Skills

## Purpose

V4.5 keeps the V4.4 single-paper Research Director and deterministic scientific-state kernel intact while adding explicit routing for reasoning roles and reusable scientific skills.

## Added

- `src/coscientist/director_v45.py` — backward-compatible role/skill facade around the V4.4 Director.
- `agents/scientific_reasoning/AGENT.md` — primary reasoning role contract.
- `agents/independent_audit/AGENT.md` — fresh-context adversarial audit role contract.
- `skills/literature_theory/SKILL.md` — LiteratureTheory v1.0.
- `skills/study_design_reasoner/SKILL.md` — StudyDesignReasoner v1.0.
- `skills/hostile_reviewer/SKILL.md` — HostileReviewer v1.0.
- `skills/manuscript_writer/SKILL.md` — ManuscriptWriter v1.0.
- `skills/citation_integrity/SKILL.md` — CitationIntegrity v1.0.
- `skills/final_audit_reasoner/SKILL.md` — FinalAuditReasoner v1.0.
- existing Humanizer v2.0 remains the manuscript-expression skill.
- `GUARANTEES_REASONING_LAYER.yaml` and `tests/test_reasoning_layer.py`.
- `docs/REASONING_LAYER.md`.

## Routing model

The deterministic Director still owns one active paper and one pending action. The V4.5 facade attaches an execution profile to each action:

- `SCIENTIFIC_REASONING` for literature/theory, data-feasibility reasoning, design closure, manuscript drafting and ordinary repair;
- `INDEPENDENT_AUDIT` for pre-freeze and final audit;
- `DETERMINISTIC` for freeze creation and analysis routing.

Each reasoning answer declares `execution_role` and exact `skills_used` versions. Audit actions additionally require `fresh_context_attested: true`.

## Independence boundary

The fresh-context field is an auditable execution attestation, not cryptographic proof of cognitive independence. The ChatGPT/LLM operating environment must actually open a fresh audit context for actions whose profile requires it.

## Backward compatibility

Existing V4.4 pending actions are not replaced. V4.5 derives and persists the execution profile from the existing action kind while preserving the original action ID.

## Unchanged deterministic boundaries

V4.5 does not delegate source suitability, hashes, power execution, freeze, AnalysisLock, R/Python/Stan execution, result-stream sealing, ResultsBridge, numeric provenance, or verified publication to LLM skills.
