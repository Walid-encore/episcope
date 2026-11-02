# AI_USAGE.md

Project: EpiScope (CSE 4345 Big Data Analytics, CEP)
Repository: github.com/Walid-encore/episcope

This file records how generative AI was used, as the assignment requires. Each team member keeps their own section. This is the Data Engineer section. The Analytics Engineer and the Visualisation Engineer add theirs below it.

---

## Data Engineer: Mohammed Walid (ID 0222220005101147)

### 1. Tools

| Tool | Version | How it was used |
|---|---|---|
| Claude (Anthropic), chat and coding-assistant sessions | Claude Sonnet 5.5 (`claude-sonnet-5-5`)| Drafting and reviewing code, debugging terminal output, writing documentation, and drafting the report. |

I did not give the AI the hashing salt, the `.env` file or any real personal data. All data in the project is synthetic. 

### 2. What the AI was used for

| Area | Files | How AI was used | What I did myself |
|---|---|---|---|
| Bronze ingestion | `pipeline/bronze/ingest.py`, `tests/test_bronze.py` | AI drafted the file registry (source, load time, batch ID, SHA-256 checksum) and the idempotent re-run logic, and the six tests. | Copied the files in, ran the tests (6 passed), ran `--dry-run`, ingested the data, and confirmed a second run printed "nothing to do (idempotent re-run)". |
| Silver layer and quality rules | `pipeline/silver/`, `pipeline/quality/rules.py` | AI helped draft and explain the quarantine design and reason codes. | Ran the stage, checked each quarantined reason code against a defect the generator injects on purpose, and checked the counts (for example the bad-age rows with ages like 218 and 171). |
| Spark SQL catalog | `pipeline/catalog.py`, changes to `pipeline/spark_session.py` and `pipeline/run_all.py` | AI wrote the catalog stage that registers bronze, silver and gold as tables. | Ran it, queried `SHOW TABLES IN gold` and a row-count query to check the 21 tables. |
| Serving export | `pipeline/serving_export.py` | AI edited my exporter: the size valve that trims the oldest weeks, `disease_windows` in `meta.json`, the 52-week minimum, the ILI and malaria windows, the stock and peak-allocation limits, and the stricter validation. | Decided the policy together with my teammates, ran the export, ran `scripts/check_serving.py`, and checked the total (9,419 KB at SF 0.25, under 10,000 KB). |
| Input check for the hidden test drop | `analytics/check_inputs.py`, `tests/test_check_inputs_weather_gap.py` | AI wrote the change that tolerates a trailing weather gap of up to 28 days and the three tests. The Analytics Engineer applied it on the branch `fix/weather-check-trailing-gap`. | Found the failure in my own drop rehearsal ("weather covers every case week"), described it, and re-ran the rehearsal until it passed. |
| One-command run and README | `README.md` (run section, hidden-drop section, architecture paragraph), `Makefile` use | AI drafted the text and the command lists. | Tested the one-command run from a clean clone on a second Windows PC, installed `make` there, and corrected the commands that did not work. |
| Scalability runs | `docs/experiments/scaling_*.log`, `docs/hardware.md` | AI explained how to run larger scale factors and what to record. | Ran SF 0.25, (0.5 and 1 ran did not support in my machine)on my machines, saved the logs and wrote the hardware details. |
| Report | `docs/` report (IEEE format) | AI drafted the structure and wording and turned my logs into tables. | Supplied every number from my logs, read the draft against the logs, and corrected it (see section 4). |

### 3. Representative prompts

These are shortened from my actual prompts. The full conversations are available on request.

1. "Could I run SF 0.25 now? Will it affect my project so far?"
2. "Now write the README run section" (with my current `README.md` attached).
3. "This is the output from my friend's PC. Why is there not any AN-2? Also tell the steps to do the hidden drop test."
4. "Why is there an error?" (with the hidden-drop output that failed at `weather covers every case week`).
5. "Yeah, write the change and give the modified file" (for `analytics/check_inputs.py`).
6. "Will there be any problem if I don't use this serving export? My visual engineer did his work with my previous one."
7. "Now write me the full report in IEEE format as asked by the assignment."
8. "Spark version, hash algorithm, how age is stored. Where do I look and how?" (I ran the searches it suggested and wrote the answers myself from the output.)

### 4. How I checked the AI's work, and mistakes I found

- I ran every stage and the tests, and compared logs with what I was told to expect.
- I ran a clean-clone `make all` and a hidden test-drop rehearsal on a second PC.
- I checked the reconciliation results in the gold stage (three checks, zero failures).
- Mistakes found while checking:
  - AI suggested `python -m generator --help`, but the package has no `__main__`; the correct module is `generator.generate`.
  - An early size-valve target left too little room under the 10 MB limit, so we lowered it.
  - The first report draft used the wrong runtime total (711.4 s instead of 710.4 s), reported silver counts before de-duplication, described the recommended equity parameter as "smallest" instead of "largest", and cited a bronze test screenshot as hidden-drop evidence. I found each of these by reading the draft against the logs and the code, and fixed them.

### 5. Responsibility

I reviewed the code listed above and I can explain it, including how bronze registers files by checksum, how silver quarantines and de-duplicates, how the salted SHA-256 hash is built, how the gold marts reconcile with silver, how the serving export enforces suppression and the 10 MB limit, and how the hidden-drop run stays incremental and idempotent.

---

## Analytics Engineer: Nishat Afroz (ID 0222220005101145)

**Tool:** Claude (Anthropic), used through the claude.ai chat interface across several sessions: the analytics work
(AN-1 to AN-10), and later the scaling experiment, the serving-size fix and the gold optimisation (ENG-2, ENG-3).

**What the assistant was used for**

- Drafting and revising the analytics modules: outbreak detection (EARS C2, CUSUM, reporting-artefact rule),
  forecasting (negative-binomial lagged-weather model, seasonal-naive and persistence baselines, rolling
  backtest, WIS, weather ablation, as-of backtest), demand conversion, the allocation optimiser with its
  efficiency-equity curve and coverage floor, the preparedness calendar and the SEIR scenario stage.
  `[confirm which files the assistant first drafted]`
- Writing unit tests and parity tests (Spark against pandas) and repairing failing ones.
- Writing independent check scripts that recompute results from the gold tables: `check_an2.py` (detection
  rows of `gold_eval_metrics`), `check_an4.py`, `check_an5.py` (backtest), `check_an6.py` (demand) and
  `check_eval_metrics.py`, plus the `gold_eval_metrics` writer in `eval_metrics.py`.
- Diagnosing bugs from pasted terminal output: the Spark round trip that shifted week start dates on a
  UTC+6 machine; an infeasible solver status when the coverage floor and the equity floor were combined;
  a calendar that jumped to 2027 when the data ended in January; a dashboard threshold of 7.5 that came
  from a stale alerts file on a teammate's machine.
- Removing all use of the stand-in tables so every analytics file reads the real gold data.
- Drafting run scripts and helpers (`run_demand.py`, `plot_tradeoff.py`, `test_truth_isolation.py`, the
  seasonal-strength gate in `preparedness.py`), and a first draft of `docs/proposal.md`.
- Reading result tables with me and suggesting what to check next.

*Scaling experiment, serving fix and gold optimisation (ENG-2, ENG-3):*

- Reading the generator and pipeline logs at SF 0.5 and SF 1, comparing stage timings, and planning how to keep
  each scale's data and logs apart (move `data/` out of the repo, `git restore` the generated files).
- Diagnosing the SF 1 `serve` failure: the log prints KiB (9,870 KiB) and the checker counts bytes (10.11 MB
  against a limit of 10,000,000). Drafting the fix, which leaves the default `suppressed: false` flag out of
  `cases_weekly.json`, in `pipeline/serving_export.py`, `scripts/check_serving.py` and `docs/serving_contract.md`
  (v0.4 note).
- Profiling the gold stage: timing prints and an optional Spark UI hold (`EPISCOPE_HOLD_UI`) in
  `pipeline/gold/build.py`; reading my Spark UI screenshots; a micro-benchmark
  (`docs/experiments/eng3_pyrdd_test.py`); an exact table-by-table comparison script
  (`docs/experiments/eng3_compare_gold.py`); and the two changes in `pipeline/gold/weeks.py` and
  `pipeline/gold/marts.py`.
- Git and log hygiene: explicit-file commits (no `git add -A`), cleaning PowerShell stderr and UTF-16 out of the
  experiment logs, and a rebase after a rejected push.
- Drafting and number-checking report section VI-E (text, Table VIII, figure captions) and the wording of my
  individual reflection.

**What I did myself**

- Ran every command and test on my machine and pasted the real output back. No result in the report comes
  from the assistant's own environment. 
- Decided that nothing may run on stand-in data, and that the demand-conversion values must come from the
  generator parameters (`generator/model.py`) and not from guesses. I asked whether the values existed in
  the project, which led to using `ITEM_COEF`, `ADMIT_P` and the 5-day stay.
- Chose the operating points by rule and evidence: EARS C2 threshold 7.0 with a 15-case floor (highest
  sensitivity with at most 0.5 false alerts per district-year on `run_detection`), CUSUM threshold 22,
  planning on the 90th percentile forecast, a 25% own-need coverage floor in the allocation stage.
  `[confirm / edit]`
- Compared the `relative` and `need` rules for the recommended alpha. Both gave 0.0 with the floor on,
  so the pipeline was left unchanged. `[update if the final run differs]`
- Decided not to build `seir.json` (optional bonus) and to cite the SEIR results from `data/analytics`.
- Decided not to commit teammates' files or generated outputs (`serving/data`, `allocation_assumptions.md`),
  and to agree one final dataset and one final run with the data engineer.
- Checked the results against the data contract and the requirement catalogue.

*Scaling experiment, serving fix and gold optimisation (ENG-2, ENG-3):*

- Ran every scaling, gold, comparison and test command on my own laptop (MSI Thin A15, Ryzen 7 7735HS, 16 GB RAM)
  and pasted the real output back, and took all Spark UI screenshots. The assistant's environment was used only
  for arithmetic on numbers copied from my logs and for syntax checks of edited files; it never ran Spark or the
  pipeline. `[confirm]`
- Kept the failing SF 1 log in the repository and reported SF 1 as a finding instead of leaving it out.
- Chose the `suppressed`-flag fix over shortening the alert window, after asking what each would affect, and asked
  for a backup and a diff after every edit so that none of my original files changed unseen.
- Questioned Table VIII when its means did not match my logs. That led to adding the third baseline run (165.0 s)
  and restating the SF 0.5 gain as 56% instead of 58%.
- Accepted that the broadcast join of the facility registry and adaptive query execution were already enabled,
  so I did not claim them as optimisations, and did not implement the incremental gold rebuild. `[confirm]`

**Where the assistant was wrong, and how it was caught**

- It said the demand assumptions were missing; the code already contained them.
- Its first guess for the allocation crash (floor too expensive at low budgets) was wrong; the code already
  capped the floor. The `min_cover_used` table showed the real interaction with the equity floor.
- It claimed the allocation-grid code was not pushed; the commit statistics showed otherwise.
- It guessed the meaning of the ILI start date 2022-12-19; the exporter code showed it was a serving window.
- A search command it gave matched the bundled D3 library and flooded the output.

*Scaling experiment, serving fix and gold optimisation (ENG-2, ENG-3):*

- Its first estimate for SF 1 (about 37 minutes, scaled from another machine) was far too high; the measured
  pipeline took about 17 minutes.
- Its first outline of the serving fix assumed the `suppressed` key was set on a JSON row. The exporter builds a
  DataFrame and measures its size in two places, which a code search showed before anything was edited.
- A cleaning command used a wildcard that also rewrote the committed SF 0.25 logs. I noticed it in `git status`
  and restored them with `git restore` before pushing.
- Its first hypothesis for the slow gold stage (repeated scans of silver, fixed by caching) was wrong: the lineage
  table scans the encounters four times in 2.7 to 4.4 s, and the Spark UI showed the time in DataFrames built from
  Python lists.
- Its first micro-benchmark failed on Windows (no Python executable set, and the errors were hidden by `2>$null`).
  The missing output showed it, and a corrected script gave the timings.
- Its first Table VIII left out one of the three baseline runs (165.0 s) and quoted a 14% run-to-run variation
  from two runs. I questioned the values; the corrected table shows a 56% gain and a 28% spread.
- The mechanism (a new Python process for every partition on Windows) is the assistant's inference. The cost per
  partition is measured; the process creation was not observed. 

**Representative prompts** 

1. "This is the work I have done so far and I have attested my output. What should I do next. I'm the
   analytics engineer." (with the project brief and my terminal log pasted) `[earlier session]`
2. "Why am I again getting this error??" (with the failing pytest output pasted) `[earlier session]`
3. "Tell me what to do next" (with the detection, forecast and allocation outputs pasted) `[earlier session]`
4. "standin gold is the dummy data. I don't want any of my files to run on dummy data. I want them to run on
   real gold data."
5. "Is random values added here?"
6. "Isn't their values not available anywhere?? Like can it be generated?? Should I ask my data engineer?"
7. "Why did not it change??"
8. "Hey is my alpha value for some data is 0.0 and for others it is 0.2? Why is it ?? Is it intended?"
9. "check it out. I think I have an error in SF 1" (with the SF 0.5 and SF 1 logs pasted)
10. "I don't want to run SF 0.25. is there any solution to fix SF 1. Also I want to push the documents in my github"
11. "If I make changes with omitting the `suppressed` flag is it going to affect anywhere? Like the dashboard or any
    other data. If not I want to proceed with this."
12. "Make sure to check everytime whether anything changes is happenning to the original file which will distort my
    project"
13. "why did you overwrite these??"
14. "That's a lot of change. Is there any alternative solution that's simpler"
15. "are these values guess?? they are not accurate from the real run"
16. "Which screenshots are you talking about?? Can you specify??"

**How AI-generated code was verified**

- Automated tests for each module (detectors, evaluation harness, features, forecast, demand, optimiser
  constraints, calendar, CUSUM masking, truth isolation); the full suite passes (218 tests at the last full
  run) `[update after the final run]`. Spark and pandas detector outputs are compared in parity tests.
- Independent check scripts recompute the key results from the real gold data: `check_an2`, `check_an4`,
  `check_an5`, `check_an6` (24 of 24 checks) and `check_eval_metrics`. `[re-run after the final data run]`
- ENG-2: every gold reconciliation reported 0 failures at SF 0.5 and SF 1. After the serving fix the SF 1 export is
  9,368,232 bytes and `scripts/check_serving.py` ends with `RESULT: all checks passed`; it also passes on the
  committed default-scale files, which still carry the old flag. I checked the dashboard in the browser (hatched
  suppressed cells on the map, and a console with only a favicon 404).
- ENG-3: `eng3_compare_gold.py` compares all eight gold tables with the unmodified pipeline's output using an exact
  two-way row difference, at SF 0.5 and SF 1 (no differing rows; `eng3_compare_sf05.log`, `eng3_compare_sf1.log`).
  Two baseline runs were compared with each other first, to show the comparison is stable. The gold unit tests
  (17) pass after the final change. Every timing in Table VIII comes from the committed logs
  (`docs/experiments/eng3_*.log`, `scaling_sf05_pipeline.log`, `scaling_sf1_pipeline.log`).
- Ground truth is read only in evaluation scripts (`run_detection.py`, `evaluate.py`, `artefact_demo.py`),
  enforced by a static test.
- Known limits, recorded in the report: operating points are tuned in-sample on the 34 injected outbreaks
  (EARS C2 detects 21 of 34 at 0.49 false alerts per district-year, CUSUM 11 of 34); both backtests are scored
  against final counts, and the as-of version costs about 1.5% in WIS; demand-conversion values are the
  generator's parameters, not clinical guidance. Gold timings vary between identical runs (the SF 0.5 baseline spans
  165.0 to 217.9 s), so Table VIII compares means; the SF 1 baseline is a single run from an earlier session; the
  gain was measured on Windows only; gold was not timed on an incremental load; and the full pipeline has not been
  re-run end to end since the gold change. 

**Understanding:** I can explain the method, the code and the trade-offs of each modules, detection, the evaluation against ground truth, the negative-binomial forecast with weather lags and its leakage-free rolling-origin backtest, demand conversion from the generator parameters, the allocation programme with the equity floor, the 25% coverage floor and its fallback, the calendar and SEIR scenarios, and the scaling changes.

## Visualisation Engineer: Dodaibul Islam Tuhin (ID 1903610201784)

### 1. Tools

| Tool | Version | How it was used |
|---|---|---|
| Claude (Anthropic), chat sessions | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | Planning the dashboard work, writing the dashboard code and helper scripts, debugging from my screenshots and terminal output, drafting README and report text, and drafting messages to my teammates. |

No other AI tool was used.

I did not give the AI the hashing salt, the `.env` file or any real personal data. What I shared with it was the assignment PDF, `serving_contract.md`, `gold_contract.md`, the README and this file, the boundary GeoJSON, the exported `allocation.json` and `dq_report.json`, terminal output, screenshots and extracts of the report draft. All data in the project is synthetic. 

The AI could not open the dashboard in a browser. For each file it wrote it could only check the syntax, and it ran one small logic test (the detection-threshold rule in `dashboard/js/alerts.js`). Every other behaviour was tested by me in the browser.

### 2. What the AI was used for

| Area | Files | How AI was used | What I did myself |
|---|---|---|---|
| Planning | none (a build order) | AI read the assignment and the contract and proposed the order: sample data, shared state, choropleth, time series, table, heatmap, allocation, freshness, README, usability tests. | Followed the plan, asked my teammates for the contract and the real exports, and decided the order of the later work (what-if, role view, data-quality panel). |
| Sample data and checker | `scripts/make_sample_data.py`, `scripts/check_serving.py` | AI wrote both: the sample generator (suppressed cells, a district with no data, wide forecast bands, an artefact alert) and the checker (district-code format, PRV-2 small-count rule, quantile order, size limit, data-quality reconciliation). | Ran both, ran the checker on the real `serving/data/` and sent the output back. It exposed a crash on `null` severity, which was fixed. |
| Dashboard core | `dashboard/index.html`, `css/style.css`, `js/main.js`, `js/state.js`, `js/data.js` | AI wrote the page, the shared store (`subscribe`/`set`, with a guard), the loader (the only module that knows file names), the freshness line and the per-disease windows. | Downloaded D3 v7 into `dashboard/vendor/`, ran `python -m http.server`, tested in the browser, and found the first bugs from screenshots. |
| Views 1, 2 and 5 | `js/views/choropleth.js`, `timeseries.js`, `warnings.js` | AI wrote the choropleth with alert dots and hatched suppression, the time series with forecast band, alert strip and brush, and the ranked early-warning table. | Tested every interaction, then reported what failed: the all-grey map, the brush that could not be dragged, and a table that stayed empty after the jump button. |
| Views 3 and 4 | `js/views/seasonal.js`, `allocation.js` | AI wrote the division-by-epi-week heatmap with calendar markers and the preparedness table, and the allocation bars with the efficiency-equity curve. AI also adapted them to the real file shapes I uploaded. | Supplied the real `seasonality.json`, `calendar.json` and `allocation.json` shapes, tested both views and the peak scenario, and asked my teammates about the budget grid and the calendar year. |
| What-if, role view, data-quality panel | `js/views/whatif.js`, `alerts.js`, `dq.js`, the guard in `state.js`, role code in `main.js` | AI wrote the what-if panel (budget, equity floor, detection threshold), the role view with division aggregates (minimum two districts), and the data-quality panel. | Decided to build each one, tested them with screenshots, and checked the numbers on screen (for example 3.1 against 5.4 gives 42.3% lower). |
| README | `README.md` (Dashboard section, VIS, PRV-2, PRV-3, DQ-2 rows) | AI drafted the section and the traceability rows from the code as built, and merged them into the team README. | Checked that the D3 file and both scripts are committed, and will fill in the usability table and the browser used. **CONFIRM.** |
| Report text | report sections VII, privacy note on PRV-3, "How to read the dashboard" | AI drafted the wording, flagged sentences in my draft that overstated the dashboard, and suggested where to find citations. | Will take the screenshots and write the real usability findings. I did not take any result from the AI. |
| Coordination | messages to the Data Engineer and the Analytics Engineer | AI drafted replies on the size policy (stock history, peak-scenario alpha values, valve rules, ILI window) and questions on `plan` against the grid, the calendar year and severity. | Sent the messages in the team chat and made the decisions with my teammates.|
| Usability test plan | `README.md` table, test tasks | AI suggested the four tasks and what to record. | Sessions with two non-CS users: **TBD, fill in after the sessions** (number of participants, date, results). |

### 3. Representative prompts

These are shortened from my actual prompts.

1. "I'm the visualisation engineer. So far these are the works of data engineer and analytics engineer. Tell me how I can begin my work step by step." (with the assignment PDF)
2. "learn this. i did git pull now tell me step by step the next things"
3. "Build the time series view with brush (view 2) ... i want to do this now."
4. "point 4 and 5 don't work when i drag also tell me where is the overview chart" (with screenshots)
5. "also alocation.json is also ready. work on real data not sample data ... tell me step by step what to do next"
6. "next PRV-3 role view" and "naw what-if panel"
7. "write the readme dashboard section", then "write my readme section in this file and then give me the modified file"
8. "tell me how to write these things in report ..." and "is this ok to write in report?"

### 4. How I checked the AI's work, and mistakes I found

- I ran the dashboard in the browser after each change and compared it with the data, the contract and the screenshots, and I ran `python scripts/check_serving.py` on the real exports. It reports `RESULT: all checks passed`.
- I did not take the AI's numbers on trust: I compared figures on screen with the exported data (for example, in the allocation tooltip stock plus allocation equals demand).
- Mistakes found while checking:
  - The map first appeared as one grey block, because the boundary file uses the opposite ring winding from the one d3-geo expects. The loader now re-winds the rings.
  - Dragging on the overview chart did nothing, because a full-width brush box left nothing to drag. The box is now hidden at full range.
  - The early-warning table said six districts but showed the empty message after the jump button, because the row-matching key failed on the empty-state row.
  - The checker crashed on `null` severity, because it sorted text against `None`.
  - The allocation view could have shown a missing grid row as "nothing allocated" when the peak scenario had fewer alpha values. It now uses each scenario's own lists.
  - The first legend row and the threshold label were cut off or overlapped, which I found from screenshots.
  - In my report draft, a sentence said that clicking a disease updates every panel. The AI pointed out that this is not exact (the disease is chosen from a list, and the data-quality panel does not follow it).

### 5. Responsibility

I reviewed the code listed above and I can explain it, including how the shared store and its guard work, how `data.js` loads the files and builds the lookups, why the boundary rings are re-wound, how suppressed and no-data cells are drawn, how the brush sets the week range, how the detection threshold is re-applied in the browser, how the role view builds division aggregates and why it needs at least two districts, and why the role view is only a demonstration and not access control. 