# AI-Washing Event Study — Execution Map to Manuscript

Project: **When Regulators Call the Bluff: AI-Washing Enforcement and the Repricing of Corporate AI Narratives**

## Operating rule
Every stage has a primary route and a fallback. If a cloud runner, API, or connector fails, execution moves to the fallback route; the project does not stop merely because one implementation path is unavailable.

GitHub is the versioned code/provenance plane. Google Drive is the persistent data/results plane. Antigravity/local Python is the heavy-compute fallback and currently the preferred extraction executor because GitHub-hosted private runners are not allocating.

## Current state
- Frozen pre-event 10-K universe: 7,361 filings / 7,361 CIKs.
- Raw corpus: complete in Google Drive `Raw Data/raw_filings`.
- Filing window: 2023-01-03 through 2024-03-15.
- Frozen manifest SHA-256: `cc2067a4689536f47afdf6fafc464e87385a658e6010cca41880715fcd6d2d8f`.
- Raw filename-set SHA-256: `dfff64049e9d5758c0f2a854bfe6f9ede7b96853c5da80cf136d4b3c9ac8614d`.
- Parser v2 implemented; previous Item 1/Item 1A collision defect repaired.
- Returns/outcomes remain locked until the pre-outcome construction is frozen.

---

# Stage map

| Stage | Required product | Primary route | Fallback route | Completion condition |
|---|---|---|---|---|
| S0 | Raw-corpus identity | Drive metadata + local inventory | Antigravity filesystem inventory | 7,361 unique expected filenames, identity hash matches |
| S1 | Correct Item 1/1A/7 extraction | Local Antigravity + Drive Desktop | GitHub Actions if hosted runners return | 7,361 filing summaries; parser failures resolved/categorized; zero section-hash collisions |
| S2 | AI candidate sentence corpus | deterministic frozen dictionary on S1 sections | same code locally | every hit traceable to CIK/accession/section/sentence; Item 1A separated from Primary Talk |
| S3 | Gold set + classifier QA | ChatGPT/CoScientist coding workflow + deterministic sample | manual dual coding exported to CSV | >=400 sentence gold set; frozen QA thresholds satisfied |
| S4 | Issuer/sample screen | pre-event issuer metadata | SEC + public/static sources | domestic US operating common-equity universe, exclusions documented, market-cap rule applied pre-event |
| S5 | AI Walk evidence | public pre-event patent/deployment evidence | source-by-source research queue | patents through 2023 + VerifiedDeployment 0-3 + source/date audit |
| S6 | CredibilityGap master | frozen Talk + Walk + size + SIC2 | local Python/R | one issuer-level master file; model residual z-score frozen and hashed |
| S7 | Pre-outcome audit | CoScientist audit | manual hostile audit checklist | design/data/classifier/Walk/CredibilityGap frozen; no unresolved blocker that changes treatment definition |
| S8 | Event returns + confounds | connected market-data source | licensed/exported data supplied by user | event-day and required windows acquired only after S7; confounds registered |
| S9 | Statistical analysis | Python/R reproducible scripts | local Antigravity/R | primary + robustness tables/figures with provenance |
| S10 | Manuscript | ChatGPT synthesis from frozen data/results | user supplies missing licensed source exports if any | full journal-ready manuscript + figures/tables + audit |
| S11 | Final data handoff | CSV + XLSX | local/Drive export | canonical analysis CSV, readable Excel workbook, codebook, hashes, manuscript |

---

# S1 — Local extraction implementation

Use `src/coscientist/eventstudy_local.py`.

The local executor:
- scans the Drive-mounted `raw_filings` directory;
- refuses a different corpus identity;
- reads each filing once;
- isolates exact primary `<TYPE>10-K`;
- extracts Item 1, Item 1A and Item 7 with exact item-token separation;
- computes section offsets, headings, characters, word counts and SHA-256;
- creates deterministic AI sentence candidates using the frozen dictionary;
- checkpoints every filing individually;
- resumes after interruption without repeating completed filings;
- supports 1-8 local worker processes;
- writes compact CSV/JSON outputs only;
- never accesses outcome data.

Recommended local output folder inside Google Drive Desktop:

`My Drive/Event Study/_phase2_v2_local`

Recommended command after cloning/checking out the branch:

```powershell
python -m pip install -e ".[dev]"
python -m coscientist.eventstudy_local --raw-dir "<GOOGLE_DRIVE_MOUNT>\My Drive\Raw Data\raw_filings" --out-dir "<GOOGLE_DRIVE_MOUNT>\My Drive\Event Study\_phase2_v2_local" --mode full --workers 4
```

If the machine restarts, run the same command again. Completed checkpoint files are reused.

Outputs:
- `LOCAL_RUN_START.json`
- `LOCAL_RUN_SUMMARY.json`
- `section_index.csv`
- `ai_sentence_candidates.csv`
- `extraction_failures.csv`
- `checkpoints/*.json`

Once the full run completes, compact outputs sync automatically through Google Drive Desktop.

---

# S2 — AI sentence corpus

Authoritative frozen dictionary is embedded in parser v2 and originates from the original corpus builder.

Primary Talk candidate sections:
- Item 1
- Item 7

Falsification/risk section:
- Item 1A

For every candidate retain:
- CIK
- company
- filing accession/date
- section
- deterministic sentence ID
- sentence index
- previous sentence
- focal sentence
- next sentence
- exact dictionary hit(s)
- parser version

Do not use Google Drive full-text search to define zero exposure. Corpus-wide deterministic extraction is the authority.

---

# S3 — Gold set and classifier

Frozen classes:
- C0 NO_AI / false positive
- C1 background / third-party context
- C2 risk-only
- C3 generic current AI claim
- C4 aspirational / forward-looking AI claim
- C5 specific operational AI claim
- C6 quantified / performance AI claim
- C7 vendor / partnership AI claim

Frozen weights:
- C0 0
- C1 0
- C2 0
- C3 1.0
- C4 1.0
- C5 1.5
- C6 2.0
- C7 1.0

Gold-set seed remains `20260826`.

Required QA from the frozen protocol:
- >=400 manually coded sentences
- claim-bearing C3-C7 vs C0-C2 F1 >=0.90
- claim-bearing precision >=0.90
- multi-class macro-F1 >=0.80
- no key C3-C7 class F1 <0.70
- >=95% repeatability on a 10% rerun

Classifier input is limited to focal sentence, one preceding sentence, one following sentence, and filing metadata. No event returns or later factual knowledge enters classification.

Products:
- `gold_set_n400.csv`
- `gold_set_adjudicated.csv`
- `classifier_predictions.csv`
- `classifier_qa.json`
- `filing_talk_summary.csv`

TalkIntensity:

`1000 * sum(fixed C3-C7 claim weights in Item1+Item7) / Item1+Item7 word count`

Item 1A C2 language remains a falsification variable.

---

# S4 — Issuer/sample screen

Construct pre-event issuer master from the 7,361 filing universe.

Required fields:
- CIK
- ticker/security mapping
- domestic US operating-company status
- common-equity status
- shell/SPAC/fund exclusion status
- pre-event market capitalization as of 2024-03-15 or last trading day before cutoff
- adequate pre-event price-history flag
- SIC and SIC2

Primary inclusion follows frozen protocol:
- domestic US operating companies
- Form 10-K
- US common equity
- pre-event market cap >= $1B
- adequate pre-event price history

Source hierarchy:
1. SEC identity/filing metadata where available
2. connected/licensed financial source if available
3. reproducible public source/API
4. user export only when a required licensed field has no accessible alternative

All source dates must be pre-event or point-in-time appropriate.

Product: `issuer_screen_master.csv`.

---

# S5 — AI Walk evidence

Primary Walk variables are frozen:
1. `log(1 + AI patent stock through 2023)`
2. `VerifiedDeployment` score 0-3 using evidence public by 2024-03-15
3. `ln(MarketCap)`
4. SIC2 fixed effects

Patent workflow:
- map issuer/company identities to patent assignees;
- retrieve AI-relevant patents with grant/application dates through 2023 according to frozen patent definition;
- maintain matched/unmatched and ambiguity audits;
- count only evidence available by cutoff.

VerifiedDeployment workflow:
- source-linked evidence only;
- evidence publication date <= 2024-03-15;
- record URL/source title/date/quotation-or-paraphrase field/evidence type;
- assign frozen 0-3 score with written rationale;
- unresolved cases remain unresolved rather than inferred.

Products:
- `ai_patent_stock_2023.csv`
- `verified_deployment_evidence.csv`
- `walk_master.csv`

---

# S6 — CredibilityGap master

Estimate before any event return is opened:

`Talk_i = alpha + beta1*log(1+AIPatents_i) + beta2*VerifiedDeployment_i + beta3*ln(MarketCap_i) + SIC2_FE + error_i`

Primary CredibilityGap:

`z(model residual)`

Positive = more AI rhetoric than observable pre-event substance, size and industry predict.

No truncation at zero. Top-quartile wash-like classification is descriptive only.

Products:
- `credibility_gap_master.csv`
- model specification file
- residual diagnostics
- hash manifest

---

# S7 — Pre-outcome freeze

Create one pre-outcome audit bundle containing:
- corpus identity
- parser version and extraction audit
- AI sentence corpus hash
- gold set and classifier QA
- issuer screen
- patent evidence
- VerifiedDeployment evidence/date audit
- TalkIntensity
- CredibilityGap master
- all exclusions and unresolved cases
- code commit SHA
- dataset SHA-256 registry

After this package is frozen, the returns stage becomes a separate downstream data acquisition step.

---

# S8 — Event outcomes

Frozen core events from the protocol:
- 2024-03-18 SEC Delphia/Global Predictions
- 2024-10-10 SEC Rimar
- 2024-11-26 FTC Evolv

Operation AI Comply 2024-09-25 remains secondary under the existing design unless timing status is formally updated pre-analysis.

Acquire only the market data required for the frozen event-study specification and declared robustness windows. Register event-specific confounds before estimation.

If the connected market-data source is unavailable, the handoff to the user is narrowly scoped: supply one CSV/export containing the required security identifiers, dates and prices/returns for the frozen sample/window. The rest of the analysis remains automated.

---

# S9 — Analysis

Produce reproducible scripts and outputs for:
- primary repeated-enforcement specification
- event-specific estimates
- high-vs-low CredibilityGap effects
- continuous CredibilityGap
- Item 1A RiskOnly falsification
- alternative Talk/Walk definitions already pre-specified
- event-window robustness
- confound sensitivity
- sample/exclusion robustness

Every table/figure is generated from frozen analysis data.

Products:
- `analysis_master.csv`
- `model_results.csv`
- `robustness_results.csv`
- publication figures
- publication tables
- analysis audit JSON

---

# S10 — Manuscript

The manuscript will be written only from frozen project data, cited source evidence and the completed statistical outputs.

Planned manuscript structure:
1. Title page / abstract / keywords
2. Introduction and contribution
3. Institutional setting and AI-washing enforcement events
4. Theory and hypotheses
5. Data and construction of AI Talk, Walk and CredibilityGap
6. Event-study design
7. Results
8. Mechanism/falsification/robustness
9. Discussion and limitations
10. Conclusion
11. References
12. Embedded tables/figures as required by target journal

The final manuscript package will include a hostile consistency audit linking each quantitative manuscript claim to a result row or figure/table source.

---

# S11 — Final deliverables

## Canonical machine-readable CSV
`AI_Washing_Event_Study_Analysis_Master.csv`

One documented row unit (issuer-event or issuer, depending on final analysis table) with no hidden spreadsheet logic.

## Reader-friendly Excel workbook
`AI_Washing_Event_Study_Data_and_Results.xlsx`

Planned sheets:
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

## Manuscript
`AI_Washing_Event_Study_Manuscript.docx`

## Supporting reproducibility package
- frozen protocol
- code commit identifiers
- CSV/XLSX hashes
- extraction/classifier/Walk/pre-outcome/analysis audits
- figure files
- table files

---

# Failure-routing map

- GitHub hosted runner unavailable -> run the same code locally with Antigravity.
- Google Drive connector unable to bulk-compute -> Drive Desktop/local filesystem handles heavy bytes.
- Financial API unavailable -> use public/static source; if no defensible alternative exists, request only the minimal export from the user.
- One filing parser failure -> retry/repair that filing class; do not rerun the whole corpus.
- Patent assignee ambiguity -> manual evidence queue.
- Deployment evidence ambiguity -> unresolved/manual evidence queue.
- Outcome-data source unavailable -> user supplies the minimal frozen sample/window market-data export; all later analysis remains automated.

The project therefore always advances to either an automated next stage or a narrowly defined user handoff containing exactly the missing external data.