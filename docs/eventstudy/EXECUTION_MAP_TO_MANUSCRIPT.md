# AI-Washing Event Study — Execution Map to Manuscript

Project: **When Regulators Call the Bluff: AI-Washing Enforcement and the Repricing of Corporate AI Narratives**

## Operating rule
Every stage has a primary route and a fallback. If a cloud runner, API, or connector fails, execution moves to the fallback route; the project does not stop merely because one implementation path is unavailable.

GitHub is the versioned code/provenance plane. Google Drive is the persistent data/results plane. Antigravity/local Python is the heavy-compute execution plane while GitHub-hosted private runners are unavailable.

## Current state
- Frozen pre-event 10-K universe: 7,361 filings / 7,361 CIKs.
- Raw corpus: complete in Google Drive `Raw Data/raw_filings`.
- Filing window: 2023-01-03 through 2024-03-15.
- Frozen manifest SHA-256: `cc2067a4689536f47afdf6fafc464e87385a658e6010cca41880715fcd6d2d8f`.
- Raw filename-set SHA-256: `dfff64049e9d5758c0f2a854bfe6f9ede7b96853c5da80cf136d4b3c9ac8614d`.
- Parser v2 implemented; previous Item 1/Item 1A collision defect repaired.
- Local resumable executor: `src/coscientist/eventstudy_local.py`.
- One-command launcher: `scripts/run_eventstudy_local.ps1`.
- Returns/outcomes remain separated from pre-return construction.

## Stage map

| Stage | Required product | Primary route | Fallback route | Completion condition |
|---|---|---|---|---|
| S0 | Raw-corpus identity | Drive metadata + local inventory | Antigravity filesystem inventory | 7,361 unique expected filenames, identity hash matches |
| S1 | Correct Item 1/1A/7 extraction | Local Antigravity + Drive Desktop | GitHub Actions if hosted runners return | 7,361 filing summaries; failures resolved/categorized; zero section-hash collisions |
| S2 | AI candidate sentence corpus | deterministic frozen dictionary on S1 sections | same code locally | every hit traceable to CIK/accession/section/sentence; Item 1A separated from Primary Talk |
| S3 | Gold set + classifier QA | ChatGPT/CoScientist coding workflow + deterministic sample | manual dual coding exported to CSV | >=400 sentence gold set; frozen QA thresholds satisfied |
| S4 | Issuer/sample screen | pre-event issuer metadata | SEC + public/static sources | domestic US operating common-equity universe, exclusions documented, market-cap rule applied pre-event |
| S5 | AI Walk evidence | public pre-event patent/deployment evidence | source-by-source research queue | patents through 2023 + VerifiedDeployment 0-3 + source/date audit |
| S6 | CredibilityGap master | frozen Talk + Walk + size + SIC2 | local Python/R | one issuer-level master file; model residual z-score frozen and hashed |
| S7 | Pre-outcome audit | CoScientist audit | manual hostile audit checklist | design/data/classifier/Walk/CredibilityGap frozen |
| S8 | Event returns + confounds | connected market-data source | licensed/exported data supplied by user | event-day and required windows acquired; confounds registered |
| S9 | Statistical analysis | Python/R reproducible scripts | local Antigravity/R | primary + robustness tables/figures with provenance |
| S10 | Manuscript | ChatGPT synthesis from frozen data/results | user supplies missing licensed source exports if any | full journal-ready manuscript + figures/tables + audit |
| S11 | Final data handoff | CSV + XLSX | local/Drive export | canonical analysis CSV, readable Excel workbook, codebook, hashes, manuscript |

## S1 — local extraction

Run from a current checkout of the repository:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_eventstudy_local.ps1
```

The launcher discovers the Google Drive Desktop mount and uses:
- raw source: `My Drive/Raw Data/raw_filings`
- output: `My Drive/Event Study/_phase2_v2_local`

The executor validates corpus identity, reads each filing once, isolates exact `<TYPE>10-K`, extracts Item 1/Item 1A/Item 7, computes section metadata/hashes, builds deterministic AI sentence candidates, and checkpoints every filing. Re-running the same command resumes from completed checkpoints.

Outputs:
- `LOCAL_RUN_START.json`
- `LOCAL_RUN_SUMMARY.json`
- `section_index.csv`
- `ai_sentence_candidates.csv`
- `extraction_failures.csv`
- `checkpoints/*.json`

## S2 — AI sentence corpus

Primary Talk candidate sections are Item 1 and Item 7. Item 1A remains separate for risk-only falsification. Each candidate retains CIK, company, filing accession/date, section, deterministic sentence ID, sentence index, previous/focal/next sentence, exact dictionary hit(s), and parser version.

## S3 — Gold set and classifier

Frozen classes C0-C7 and weights remain as defined in the frozen classifier prompt. Gold-set seed remains `20260826`.

Products:
- `gold_set_n400.csv`
- `gold_set_adjudicated.csv`
- `classifier_predictions.csv`
- `classifier_qa.json`
- `filing_talk_summary.csv`

TalkIntensity:

`1000 * sum(fixed C3-C7 claim weights in Item1+Item7) / Item1+Item7 word count`

## S4 — issuer/sample screen

Required fields include CIK, ticker/security mapping, domestic-US operating status, common-equity status, shell/SPAC/fund exclusions, pre-event market capitalization, adequate price-history flag, SIC and SIC2.

Product: `issuer_screen_master.csv`.

## S5 — AI Walk evidence

Primary Walk variables:
1. `log(1 + AI patent stock through 2023)`
2. `VerifiedDeployment` 0-3 from evidence public by 2024-03-15
3. `ln(MarketCap)`
4. SIC2 fixed effects

Products:
- `ai_patent_stock_2023.csv`
- `verified_deployment_evidence.csv`
- `walk_master.csv`

## S6 — CredibilityGap master

Estimate:

`Talk_i = alpha + beta1*log(1+AIPatents_i) + beta2*VerifiedDeployment_i + beta3*ln(MarketCap_i) + SIC2_FE + error_i`

Primary CredibilityGap = `z(model residual)`.

Product: `credibility_gap_master.csv` plus model/audit/hash files.

## S7 — pre-outcome package

Freeze corpus identity, parser/extraction audit, AI sentence corpus hash, gold set/classifier QA, issuer screen, patent/deployment evidence, TalkIntensity, CredibilityGap master, exclusions/unresolved cases, code commit SHA, and dataset hashes.

## S8 — event outcomes

Core events currently defined in the protocol:
- 2024-03-18 SEC Delphia/Global Predictions
- 2024-10-10 SEC Rimar
- 2024-11-26 FTC Evolv

If no connected source can provide a required licensed market field, the user handoff is limited to one minimal CSV/export containing the frozen security identifiers, dates, and required prices/returns.

## S9 — analysis

Products:
- `analysis_master.csv`
- `model_results.csv`
- `robustness_results.csv`
- publication figures/tables
- analysis audit JSON

## S10 — manuscript

Structure:
1. Title page / abstract / keywords
2. Introduction and contribution
3. Institutional setting and enforcement events
4. Theory and hypotheses
5. Data and AI Talk/Walk/CredibilityGap construction
6. Event-study design
7. Results
8. Mechanism/falsification/robustness
9. Discussion and limitations
10. Conclusion
11. References
12. Tables/figures

Every quantitative manuscript statement maps to a frozen result row/table/figure.

## S11 — final deliverables

### Canonical CSV
`AI_Washing_Event_Study_Analysis_Master.csv`

### Reader-friendly Excel workbook
`AI_Washing_Event_Study_Data_and_Results.xlsx`

Sheets:
- README
- Codebook
- Issuer_Master
- Filing_Talk
- AI_Sentences
- Walk
- CredibilityGap
- Events
- Analysis_Data
- Main_Results
- Robustness
- Source_Audit
- Hashes

### Manuscript
`AI_Washing_Event_Study_Manuscript.docx`

### Reproducibility package
Frozen protocol, code commit identifiers, CSV/XLSX hashes, extraction/classifier/Walk/pre-outcome/analysis audits, figure files, and table files.

## Failure-routing map

- GitHub hosted runner unavailable -> Antigravity/local executor.
- Drive connector cannot bulk-compute -> Google Drive Desktop filesystem.
- Financial API unavailable -> public/static source; if no defensible alternative, request only the minimal user export.
- One parser failure -> repair/retry only affected filing class.
- Patent assignee ambiguity -> manual evidence queue.
- Deployment ambiguity -> unresolved/manual evidence queue.
- Outcome-data source unavailable -> minimal frozen market-data export from user; downstream analysis remains automated.

The project always advances to either the next automated stage or a narrowly scoped user handoff for only the data that cannot be obtained programmatically.