# CoScientist V4.5 — Reasoning Roles and Skills

## Purpose

V4.5 keeps the V4.4 single-paper Research Director as the scientific-state authority and adds an explicit reasoning execution profile to every pending action.

The design principle is:

> Few roles, many skills. Models reason; deterministic systems control scientific state.

## Roles

### Research Director

The Research Director is deterministic infrastructure, not an LLM agent. It owns one active paper, one pending action, action identity, lifecycle transitions, same-paper repair routing, freeze/analysis mechanical routing, and validation of action-bound answers.

### Scientific Reasoning role

This is the normal LLM execution context. It handles literature, theory, design reasoning and manuscript drafting only when the pending Director action requests that work.

Contract: `agents/scientific_reasoning/AGENT.md`.

### Independent Audit role

This is a fresh adversarial reasoning context used for pre-freeze and final scientific audit. It is read-only toward canonical scientific state and returns findings through the same Director answer channel.

Contract: `agents/independent_audit/AGENT.md`.

## Skill registry

- `Humanizer` v2.0 — manuscript expression only.
- `LiteratureTheory` v1.0 — closest literature, residual contribution, mechanism, rival and constructs.
- `StudyDesignReasoner` v1.0 — estimand, identification, operationalization, falsification and robustness logic.
- `HostileReviewer` v1.0 — adversarial scientific attack with same-paper repair discipline.
- `ManuscriptWriter` v1.0 — canonical manuscript from verified records/results only.
- `CitationIntegrity` v1.0 — semantic citation support audit.
- `FinalAuditReasoner` v1.0 — judgment-heavy synthesis of verified final-audit evidence.

## Director routing

| Action | Role | Required skills |
|---|---|---|
| `DISCOVER_TOPIC` | SCIENTIFIC_REASONING | none permanently required |
| `DEVELOP_LITERATURE_THEORY` | SCIENTIFIC_REASONING | LiteratureTheory, CitationIntegrity |
| `DATA_FEASIBILITY` | SCIENTIFIC_REASONING | StudyDesignReasoner |
| `DESIGN_CLOSURE` | SCIENTIFIC_REASONING | StudyDesignReasoner |
| `PRE_FREEZE_AUDIT` | INDEPENDENT_AUDIT | HostileReviewer, CitationIntegrity |
| `CREATE_FREEZE` | DETERMINISTIC | none |
| `RUN_ANALYSIS` | DETERMINISTIC | none |
| `MANUSCRIPT_DRAFT` | SCIENTIFIC_REASONING | ManuscriptWriter, CitationIntegrity, Humanizer |
| `FINAL_AUDIT` | INDEPENDENT_AUDIT | FinalAuditReasoner, CitationIntegrity, HostileReviewer |
| `REPAIR` | SCIENTIFIC_REASONING | defect-specific; no fixed skill set |

[GUARANTEE: REASONING_ACTIONS_DECLARE_EXECUTION_PROFILE]
[GUARANTEE: REQUIRED_REASONING_SKILLS_ARE_ENFORCED]
[GUARANTEE: MECHANICAL_ACTIONS_USE_NO_REASONING_SKILLS]

## Independent-audit boundary

`PRE_FREEZE_AUDIT` and `FINAL_AUDIT` cannot be answered under the normal scientific-reasoning role. Their execution profile requires `INDEPENDENT_AUDIT` and `fresh_context_required: true`.

The answer must attest that a fresh context was used. This attestation is auditable metadata, not cryptographic proof of cognitive independence; the ChatGPT/LLM execution environment must actually start the audit in a fresh reasoning context.

[GUARANTEE: AUDIT_REQUIRES_INDEPENDENT_ROLE]
[GUARANTEE: AUDIT_REQUIRES_FRESH_CONTEXT_ATTESTATION]

## Backward compatibility

A pending V4.4 action in Drive is not discarded or recreated. On the first V4.5 ensure/status pass, its execution profile is derived from the existing action kind, stored in the action context, and the original action ID remains unchanged.

[GUARANTEE: LEGACY_PENDING_ACTIONS_PRESERVE_IDENTITY]

## Answer metadata

Reasoning answers now include:

```json
{
  "action_id": "DA-...",
  "execution_role": "SCIENTIFIC_REASONING or INDEPENDENT_AUDIT",
  "skills_used": {
    "SkillName": "version"
  },
  "fresh_context_attested": true
}
```

`fresh_context_attested` is required only for independent audit actions.

## Deterministic boundary

The role/skill layer does not take over:

- lake acquisition/curation;
- source suitability enforcement;
- dataset hashing;
- power execution;
- FreezeManifest creation;
- AnalysisLock creation/verification;
- R/Python/Stan execution;
- result-stream sealing;
- ResultsBridge creation;
- numeric provenance;
- verified Drive publication.

Those remain mechanical/deterministic components.
