# Specification: Agentic EHR Extraction Pipeline

## 1. System Overview

The Agentic EHR Extraction Pipeline ingests unstructured clinical documents in Markdown from a
local directory, extracts chronological patient journeys using a large language model (default
`gpt-oss`, e.g. `gpt-oss-120b`), and persists the validated records as per-patient JSON for
downstream analytics and evaluation.

The system is implemented in LangGraph as a cyclic state machine with SQLite checkpointing.
State is recorded after every node transition, allowing an interrupted run to resume by
re-invoking the graph with the same `thread_id`.

## 2. Architecture and Orchestration

### 2.1 Graph State

`GraphState` (`state.py`) is the execution context passed through every node:

| Field               | Type                        | Description                                                          |
|---------------------|-----------------------------|----------------------------------------------------------------------|
| `document_id`       | `str`                       | Absolute path of the current Markdown file.                          |
| `raw_text`          | `str`                       | Full text loaded from the file.                                      |
| `extracted_data`    | `Optional[Dict[str, Any]]`  | The model's structured payload mirroring the `Patient` schema.       |
| `validation_errors` | `Annotated[list[str], add]` | Accumulated error feedback (reducer concatenates across iterations). |
| `iteration_count`   | `int`                       | Extraction attempts; bounds the self-correction loop.                |
| `status`            | `Literal[...]`              | One of `loading`, `extracting`, `validating`, `persisting`, `failed`, `completed`. |

### 2.2 Nodes

The graph registers five nodes (graph key → implementing function in `nodes.py`):

1. **`load` → `load_document_node`.** Reads the file referenced by `document_id` into
   `raw_text`; sets `status` to `extracting`, or to `failed` on a read error.
2. **`extract` → `agent_extraction_node`.** Constructs the prompt from `prompt.py`
   (`SYSTEM_PROMPT`, `USER_PROMPT_BASE`, and, on retries, `VALIDATION_ERROR_TEMPLATE`
   populated with prior `validation_errors`), invokes the LLM via
   `with_structured_output(Patient, method="json_schema", include_raw=True)`, stamps the
   patient `id` from the filename, and increments `iteration_count`. On success it sets
   `status` to `validating`; on an exception it sets `status` to `extracting` to trigger a
   retry.
3. **`validate` → `validation_node`.** Re-instantiates `Patient(**data)` to enforce the schema,
   then applies domain logic: for each treatment with both dates present and not `unknown`,
   `startDate` must not exceed `endDate`. Errors set `status` to `extracting`; otherwise
   `status` is set to `persisting`.
4. **`persist` → `persist_json_node`.** Writes or merges the per-patient JSON record (see
   Section 6). On success it sets `status` to `completed`; on a write error, to `failed`.
5. **`failed_state`.** Terminal node that sets `status` to `failed` and routes to `END`,
   functioning as a dead-letter sink. The checkpoint is preserved so the document can be
   re-run, but there is no automatic re-queue.

### 2.3 Routing

Conditional edges are defined by `route_load`, `route_extract`, `route_validate`, and an inline
predicate on `persist`:

- `load` routes to `extract`, or to `failed_state` if the read failed.
- `extract` routes to `validate` on success. On an extraction error it loops back to `extract`
  while `iteration_count < MAX_RETRIES`, otherwise to `failed_state`.
- `validate` routes to `persist` when validation passes. On failure it loops back to `extract`
  while `iteration_count < MAX_RETRIES`, otherwise to `failed_state`.
- `persist` routes to `END` on success, or to `failed_state` on a write error.

`MAX_RETRIES` defaults to 3 and is configurable via the `MAX_RETRIES` environment variable.

## 3. Extraction Engine

### 3.1 Endpoint and Model Configuration

The extraction client (`_build_chat_llm`) is a LangChain `ChatOpenAI` instance targeting an
OpenAI-compatible endpoint (for example a vLLM server or a LiteLLM proxy). It is configured
with `model` from `LLM_MODEL` (default `gpt-oss`), `temperature` 0.0 for deterministic output,
`base_url` from `LLM_BASE_URL`, `api_key` from `LLM_API_KEY`, and a 300-second timeout.
Reasoning effort for `gpt-oss` is configured at the inference endpoint rather than in the
client.

### 3.2 Schema Enforcement

Structured outputs are requested with `method="json_schema"` derived from the Pydantic
`Patient` model (`schema.py`). The Pydantic model is the single source of truth for the output
contract: it constrains generation and is also used to re-validate the returned payload in the
`validate` node.

### 3.3 Prompt Engineering

The prompts are defined in `prompt.py` as `SYSTEM_PROMPT`, `USER_PROMPT_BASE`, and
`VALIDATION_ERROR_TEMPLATE`. The system prompt encodes the domain rules that the schema alone
cannot express: the ovarian-carcinoma extraction scope, per-field definitions and allowed enum
values, German-specific handling (clinical abbreviations, drug-name aliasing to generic names,
date normalisation, and biomarker value standardisation), treatment-line logic, and a single
in-context worked example (redacted in this data-free release — it was a real patient protocol;
the scaffold remains and the prompt is otherwise unchanged). The user prompt supplies the document
text. On a retry, the validation
errors from the previous attempt are appended via `VALIDATION_ERROR_TEMPLATE` so the model can
self-correct.

## 4. Resiliency and Checkpointing

### 4.1 Persistence

`main.py` constructs a `SqliteSaver` from `DB_PATH`, calls `setup()`, and compiles it into the
graph as the checkpointer, so `GraphState` is recorded across node transitions. Each input file
is assigned a deterministic `thread_id` derived as `uuid5(NAMESPACE_URL, abspath(file))`,
isolating documents from one another.

### 4.2 Failure Handling

Each document is processed inside a `try`/`except` block in `main.py`; an uncaught exception
emits a `pipeline_crashed` log event rather than aborting the batch. Because state is
checkpointed, re-running the process with the same `thread_id` resumes from the last completed
node.

## 5. Observability and Logging

The application uses `structlog` to emit JSON-formatted logs to standard output. The `extract`
node logs an `llm_telemetry` event per call, capturing end-to-end latency and input, output,
and reasoning token counts. Validation failures are logged with the specific schema or
domain-logic errors that triggered each self-correction cycle.

## 6. Output Storage

`persist_json_node` writes one JSON file per patient to `OUTPUT_DIR`, named
`<patient_id>.json`, where `patient_id` is the leading numeric portion of the source filename.
If the target file already exists, the node appends the new `documents` to the existing record
and backfills `id` and `dateOfBirth` when they were previously missing. JSON is written with
`indent=2` and `ensure_ascii=False` for readability.

## 7. Evaluation

The `ehr.evaluation` package provides an offline evaluation suite, independent of the
pipeline, that runs on folders of JSON files.

### 7.1 Scoring — `evaluate.py`

- **Inputs.** `--pred` (folder of predicted JSON files), `--gold` (folder of gold JSON files),
  and `--tag` (output label). Predictions are matched to gold files by filename stem.
- **Normalisation.** Values are lowercased, whitespace-collapsed, and stripped of surrounding
  punctuation (including hyphens); dates, unit glyphs, and percentages are canonicalised; drug
  brand/abbreviation synonyms are mapped to a canonical generic; and `unknown`, `not documented`,
  `n/a`, `none`, and empty strings are treated as absent. JSON loading is lenient (strict parse,
  then trailing-comma repair, then `json5` when available).
- **Alignment.** Each `Patient` is flattened to dotted field paths. List-valued fields
  (treatment lines, medications, surgeries, biomarkers) are aligned by **clinical content** — a
  similarity over start/end month and shared drugs for treatment lines, drug identity for
  medications, date for surgeries, and name for biomarkers — with greedy best-first matching and
  each item used once. This replaces position- or hash-key-based alignment, which split a single
  line into a phantom missing + extra whenever a keying field differed between gold and prediction.
- **Scoring.** Each field is classified `TP`, `FP`, `FN`, or `TN`; a value mismatch counts as both
  a false positive and a false negative. Dates are compared **day-exact**. Outputs are
  `comparison_<tag>.csv` (every scored field for manual review) and `metrics_<tag>.csv`
  (accuracy, precision, recall, F1, and support per field type).

### 7.2 Agreement, comparison, and reporting

- **`compare.py`** builds the human-review `.xlsx` (gold vs the LLM output, one row per field); the
  `evaluation` column carries the per-field label (match / mismatch / missing / extra), which is the
  deliverable for rolling up any headline metric (e.g. with `significance.py`) separately.
- **`significance.py`** reports the per-field agreement of a pre-scored `GS` column (primary) vs a
  `Human` column (baseline) — each row already a match/mismatch verdict — with the per-field
  difference tested by an exact McNemar test, FDR-corrected (Benjamini–Hochberg) to a q-value.
  In this study both columns are the same model output scored against two references — `GS` against
  the audited gold standard (corrected field-by-field against the source protocols) and `Human`
  against the pre-audit human consolidation — so the test quantifies the effect of the audit: a
  significant GS > Human means the model matched the source-verified value where the initial human
  consolidation had not.
- **`interrater.py`** reports inter-rater agreement between the two clinicians on the gold
  standard: percent agreement and Cohen's κ (chance-corrected).
- **`agreement_stats.py`** derives supplementary robustness statistics from the same comparison
  table: per-field and overall model-vs-gold agreement each with a 95 % confidence interval from a
  **cluster bootstrap** over patients (resampled with replacement so within-patient dependence is
  respected); inter-rater agreement as **Gwet's AC1** alongside Cohen's κ (AC1 is prevalence-robust,
  so it does not collapse on high-prevalence fields where κ is deflated); and a **trivial-agreement
  decomposition** — the fraction of fields *Not documented* on both sides and the documented-only
  agreement once those jointly-absent fields are removed. Point estimates are deterministic; the CI
  is seeded (default 20 000 resamples).
- **`pharmacy.py`** checks the gold-standard medications (read from a comparison table's value
  column, e.g. `ground_truth (Clinician 2)`) against pharmacy dispensing records at the active-ingredient
  level (presence precision/recall/F1 and date concordance). Supportive medications and
  orally/externally dispensed agents (PARP inhibitors, oral endocrine therapy) that the intravenous
  pharmacy does not fill are excluded from both sides. Dates are compared at month resolution with a
  ±1-month (≤2-month lenient) tolerance, and each pharmacy dispensing span is capped at the patient's
  protocol (document) date — dispensings after that date could not have informed the gold standard,
  so a drug dispensed only afterwards is dropped and a later-ending span is truncated.

The reporting tools group fields by the same field type as the scorer and write their results to
the terminal and an `.xlsx`.

## 8. Data Schema

The authoritative schema is defined in `schema.py`. All fields are strings unless noted;
optional fields are marked, and unmarked fields are required.

```
Patient
  id
  dateOfBirth
  documents[]                       -> Document
    type
    date
    diagnoses[]                     -> Diagnosis
      tumor
      date
      figoStage                     (optional)
      tnmStage                      (optional)
      resectionStatus
      relapse
      biomarkers[]                  -> Biomarker
        biomarker, value, type, date
    treatments[]                    -> Treatment
      type
      startDate
      endDate                       (optional)
      treatmentLine                 (int)
      status
      medications[]                 (optional) -> Medication
        medicationName, dosage, interval, startDate, endDate (optional)
      surgeries[]                   (optional) -> Surgery
        date, type, resectionStatus
    tumorBoardOutcomes[]            -> TumorBoardMeetingOutcome
      date, input, recommendation
```

All fields above are included in the evaluation metrics (Section 7). Document-level `type` and
`date` are reported under the `document.type` and `document.date` field types.

## 9. Non-functional Requirements

- Environment and dependencies are managed with `uv`; the target runtime is Python 3.12+.
- Configuration is loaded from a `.env` file via `python-dotenv` (`load_dotenv()` at startup).
- The repository's `.gitignore` excludes the SQLite checkpoint database, the `.env` file, and
  the output directory.

## 10. Limitations and Planned Extensions

- **Columnar export.** The current persistence layer writes per-patient JSON only. A columnar
  Parquet export (nested payloads mapped to PyArrow `List<Struct>` types for embedded querying)
  is planned but not yet implemented.
- **Aggregate run metrics.** Per-call telemetry is logged today (Section 5). Aggregate measures
  such as extraction success rate, mean iterations per document, and total token consumption
  are planned.
- **Automated resume.** Resumption after a crash is currently manual via re-running the process.
  An external monitoring layer that detects pipeline exit and automatically re-invokes the
  graph is planned.
