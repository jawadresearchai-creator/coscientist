# CoScientist v4.6.1 — Owner Withdrawal Governance

## Why this release exists

V4.6 correctly enforced one active paper and strict scientific retirement grounds, but that policy was too rigid for human governance: it prevented the owner from deliberately abandoning a viable topic for preference, priority, timing, or strategy reasons unless the system falsely labeled the paper scientifically impossible.

V4.6.1 corrects that mistake.

## New terminal state: USER_WITHDRAWN

`USER_WITHDRAWN` is distinct from `RETIRED`.

- `RETIRED` means an enumerated scientific blocker makes the core paper impossible to complete honestly.
- `USER_WITHDRAWN` means the owner explicitly chooses not to continue the paper.

The owner does not need a scientific blocker to change topics.

## One-paper rule after this release

The rule is now:

> Exactly one paper may be active at a time. The owner may explicitly withdraw the active paper at any non-terminal stage, after which discovery reopens immediately.

The system still forbids parallel active papers, reserve queues, and silent model-driven topic hopping.

## Directed next-topic handoff

Owner withdrawal may include `owner_next_topic_direction`. When present, the next Director discovery action is constrained to evaluate/refine that direction and requires exactly one candidate rather than reopening an unrelated topic tournament.

## Safety properties

- A model cannot use a normal stage transition to fake `USER_WITHDRAWN`; the dedicated `withdraw()` route is required.
- Withdrawal must have a non-empty owner reason.
- Withdrawal is never inferred from null findings, failed models, workflow errors, or ordinary repairs.
- Director reconciliation invalidates the old paper's pending action after withdrawal.
- A new paper may be admitted only after the old one is terminal, preserving the one-active-paper invariant.
- Existing v4.6 deterministic receipt, freeze, AnalysisLock, provenance, and analysis-autonomy guarantees remain unchanged.

## Operating facade

Use `src/coscientist/director_v461.py` / `python -m coscientist.director_v461`.

Example:

```bash
python -m coscientist.director_v461 \
  --state state/single_paper.json \
  --director state/director.json \
  --catalog state/gms_lake_catalog.json \
  withdraw \
  --reason "Owner changed research priority" \
  --next-topic "event study of the ChatGPT Astra release"
```

This produces `USER_WITHDRAWN` and immediately creates a fresh owner-directed `DISCOVER_TOPIC` action.
