# **EpiScope: M1 Proposal**

**Group:** 3 Musketeers \| **Members:** \[Dodaibul Islam Tuhin, 1903610201784\], \[Nishat Afroz, 0222220005101145\], \[Mohammed Walid, 0222220005101147\] \| **Course:** CSE 4345, Spring 2026 **Data note:** all patient-level data is synthetic (own generator). Boundaries, population and weather are public (S1-S3).

## **1\. Problem Statement**

The Ministry of Health needs a prototype national platform that turns routine health, climate and logistics data into (i) early outbreak warnings, (ii) 1-4 week case and hospital-demand forecasts, (iii) an allocation plan for a limited national stock, and (iv) a seasonal preparedness calendar, for all 64 districts, served through a trustworthy D3.js dashboard that works with late and incomplete data and never exposes identifiable patients. The platform must run on a 16 GB laptop and be able to grow to national scale.

## **2\. Stakeholders and Conflicts**

| Conflict | Stakeholders in tension | Planned resolution   |
| :---- | :---- | :---- |
| C1 Sensitivity vs. false alarms | Epidemiologists vs. district officers (alert fatigue) | Sweep threshold and minimum-case floor; plot sensitivity vs. false alerts per district-year; pick the highest-sensitivity point under a stated false-alert limit |
| C2 Granularity vs. privacy | Vector-control units vs. regulator | District-level serving only; suppress counts 1-4 with complementary suppression so totals cannot reveal cells (PRV-2) |
| C3 Efficiency vs. equity | Policymakers ("why did X get more than Y?") vs. low-risk districts | Equity floor alpha in the LP; efficiency-equity curve over at least 5 budget levels; recommend alpha with a stated tolerance |
| C4 Freshness vs. cost | District officers vs. infrastructure | Daily batch with idempotent loads, justified by weekly decision cycles; 21-day late-report updates |
| C5 Accuracy vs. interpretability | Hospital managers, officials | Negative-binomial regression with lagged weather instead of a black box; compare against seasonal-naive and persistence baselines |

Other stakeholders: hospital managers (need uncertainty bands), the meteorological department (data source), patients and the public (privacy, stigma).

## **3\. Architecture and Tools**

Medallion lakehouse (bronze, silver, gold) with an analytics layer and a static D3.js serving layer.

| Layer | Choice | Justification |
| :---- | :---- | :---- |
| Generator | Seeded, deterministic Python/Spark generator (S4-S6) with negative-binomial noise, weather-driven dengue lag of 4-8 weeks, at least 30 injected outbreaks and reporting artefacts | Meets Section 5.2; ground truth kept in a separate file that detection code never reads |
| Ingestion / transforms | Apache Spark (local mode), Parquet | ING-2/3; runs on a laptop; pandas only for small reference data |
| Quality and privacy | At least 12 rules with a quarantine table and reason codes; salted hashing of patient IDs (salt outside the repo); age bands | DQ-1/2/3, PRV-1 |
| Orchestration | Makefile DAG with one documented command | ENG-1; simpler than Airflow at this scale |
| Analytics | Python on gold tables; Spark where per-series work parallelises | See section 4 |
| Serving / UI | Aggregated, suppressed JSON (under 10 MB) read by D3.js v7 | VIS-1, VIS-4 |

Alternatives considered: Delta/Iceberg (ING-7, bonus) and Structured Streaming (ING-6) are deferred because the weekly decision cadence does not need them; HDFS/MinIO (ING-5) is optional.

## **4\. Diseases and Methods**

**Diseases:** dengue, diarrhoeal disease, influenza-like illness and malaria (the four named in the brief). Gold tables are keyed by district, disease and epi-week.

| Task | Method | Evaluation |
| :---- | :---- | :---- |
| Detection and Evaluation (AN-1, AN-2, AN-3)  | EARS C2 (7-week baseline, 2-week guard) and CUSUM; reporting-artefact flag so silent-then-back-fill weeks do not alert  | Against ground\_truth\_outbreaks: sensitivity, false alerts per district-year, median days to detection, and a threshold trade-off curve (C1) with a chosen operating point; two methods compared  |
| Forecasting (AN-4, AN-5) | Per-series negative-binomial model with lagged weather, trained in parallel with Spark | Rolling-origin backtest over the last 52 weeks vs. seasonal-naive: MAE, 80% interval coverage, WIS |
| Demand (AN-6) | Cases x admission rate x length of stay for beds; per-case rates for IV saline, NS1 and ORS, using the forecast's upper quantile (q90) | Stated assumptions plus a sensitivity run |
| Allocation (AN-7, AN-8) | Linear programme with budget, equity floor and severity weights (PuLP/CBC) | vs. population-proportional baseline; efficiency-equity curve |
| Calendar (AN-9) | Seasonal decomposition per division and disease | Onset, peak, pre-positioning and campaign weeks |
| Scenarios (AN-10) | SEIR calibrated on the injected novel-respiratory surge | What-if contact-reduction scenarios |

## **5\. Roles**

| Member | Lead role | Primary requirements |
| :---- | :---- | :---- |
| Mohammed Walid | Data Engineer | Generator, ING-*, DQ-*, ENG-1-3 |
| Nishat Afroz | Analytics Engineer | AN-\*, backtests, evaluation |
| Dodaibul Islam Tuhin | Visualisation Engineer | Serving exports, VIS-\*, PRV-2/3 |

## **6\. Risk Register**

| \# | Risk | Likelihood / impact | Mitigation |
| :---- | :---- | :---- | :---- |
| R1 | Late-arriving reports (up to 21 days) distort recent weeks | High / Medium | Trim the newest week; as-of backtest on report lags; late-record updates in the load |
| R2 | Reporting artefacts raise false alerts | High / High | Completeness-based artefact flag; test it against injected artefacts |
| R3 | Synthetic data looks too clean, so results overstate real-world skill | Medium / High | State limits in the report; use real weather and boundaries; sensitivity analyses |
| R4 | SF=1 run exceeds 45 minutes on a 16 GB laptop | Medium / High | Early scaling tests at SF 0.25/0.5; broadcast joins, AQE, partition pruning |
| R5 | Small-cell counts leak patient information | Low / High | Suppression and complementary suppression in the serving layer; automated tests |
| R6 | Hidden test drop breaks on hard-coded dates or file names | Medium / High | No hard-coded dates; schema contract tests; dry run on a held-out increment |
| R7 | Detector sensitivity is low at an acceptable false-alert rate | Medium / Medium | Report the full trade-off curve and per-disease results honestly |

## **7\. Repository and Schema**

Layout follows Appendix A. The generator schema is committed in docs/schema.md and the gold contract in docs/gold\_contract.md. All three members commit throughout the two weeks.  
