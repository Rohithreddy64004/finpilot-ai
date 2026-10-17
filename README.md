# FinPilot AI

FinPilot AI is a multi-expert educational finance assistant for the Nugen Intelligence hiring challenge. The FastAPI backend routes questions to Indian tax, investment, and insurance experts, reuses the existing domain-scoped retrieval and inference pipeline, and synthesizes only source-backed expert results.

The FastAPI backend includes a dedicated async Nugen API v3 client, typed provider schemas, document upload/list/status routes, a separate MongoDB provenance registry for each expert corpus, a local domain-scoped BM25 retriever, and a manually invoked inference check. No Nugen call runs at application startup. Alignment jobs and deployments have explicit confirmation-gated API routes; the application never starts them automatically. Base-model answers report `unaligned`.

## Backend layout

```text
finpilot-ai/
├── .env.example
├── backend/
│   ├── app/
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── schemas.py
│   │   ├── source_documents.py
│   │   ├── workflow_api.py
│   │   └── nugen/
│   │       ├── alignment_service.py
│   │       ├── client.py
│   │       ├── errors.py
│   │       ├── inference_check.py
│   │       └── schemas.py
│   ├── requirements.txt
│   └── tests/
│       └── test_workflow_api.py
├── docs/
└── frontend/                 # React + TypeScript + Vite chat UI
```

The implemented request path uses a model-assisted router with keyword fallback, concurrent domain experts, shared retrieval and citation checks, and a citation-validated synthesizer. Queries, user feedback, and reviewer-verified corrections are stored separately. User feedback is never promoted to ground truth automatically.

## Architecture

~~~mermaid
flowchart LR
  UI[React chat UI] --> ASK[POST /api/ask]
  ASK --> ROUTER[Router]
  ROUTER -->|selected domains| EXPERTS[Concurrent domain experts]
  ROUTER -->|out of scope| SAFE[Safe out-of-scope response]
  EXPERTS --> INFER[Shared per-domain inference]
  INFER --> DOCS[Mongo active-source and READY checks]
  INFER --> TEXT[Domain-filtered per-domain BM25 from local UTF-8 source content]
  INFER --> NUGEN[Nugen chat completions]
  EXPERTS --> SYNTH[Synthesizer with real passage IDs]
  SYNTH --> UI
  ASK --> QUERY[(queries)]
  UI --> FEEDBACK[POST /api/feedback]
  FEEDBACK --> FB[(feedback)]
  EVAL[Fixed benchmark and run_eval.py] --> ROUTER
  EVAL --> EXPERTS
  EVAL --> METRICS[Routing, coverage, grounding, latency]
  REVIEW[Authenticated reviewer] --> CORR[POST /api/verified-corrections]
  CORR --> VC[(verified_corrections)]
~~~

POST /api/inference remains available for a single explicitly selected domain. POST /api/ask records the question, routes or applies an expert override, calls selected experts concurrently, and synthesizes only grounded answers. Unknown or malformed citation markers cause wording to be withheld.

## Windows PowerShell setup

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Start MongoDB locally or configure `MONGODB_URI` for MongoDB Atlas. The app connects lazily when document provenance is read or written and creates unique per-domain document ID indexes after a successful ping. Index initialization preserves records and fails safely if duplicate IDs need manual review. Set `NUGEN_API_KEY` before making Nugen requests. Keep `.env` private; the key and Mongo URI use Pydantic `SecretStr`, and repository diagnostics omit URI and exception text.

Run the API and its local tests:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
.\.venv\Scripts\python.exe -m pytest -q
```

The liveness endpoint is `http://127.0.0.1:8000/health`; interactive API docs are at `http://127.0.0.1:8000/docs`.

## Frontend

The existing React client now calls POST /api/ask by default and displays the routing decision, per-expert answers, source passages, model alignment state, claim assessments, and feedback controls. The optional checkboxes send an explicit expert override; with no boxes checked, the router decides. Model text is rendered through React and the existing safe Markdown component; raw HTML is not enabled. Nugen and MongoDB credentials stay on the server.

For local development, Vite proxies `/api` and `/health` to FastAPI, so the browser still uses one origin. The backend grants no CORS origins in any environment.

## Nugen configuration

The default API base URL is `https://api.nugen.in`; override it with `NUGEN_API_BASE_URL` only for a documented Nugen host. The client uses HTTPS, an explicit timeout, and a bounded number of retries for GET requests. It does not retry POST requests, which could duplicate inference, uploads, alignment jobs, or deployments.

Configuration is loaded from the environment or a local .env file. `.env.example` contains the supported variable names with empty values only. Supply values locally or in the deployment secret manager; never put them in source control or logs.

~~~text
MONGODB_URI
MONGODB_DATABASE
NUGEN_API_KEY
NUGEN_API_BASE_URL
NUGEN_TEST_MODEL_ID
NUGEN_TAX_MODEL_ID
NUGEN_INVESTMENT_MODEL_ID
NUGEN_INSURANCE_MODEL_ID
FINPILOT_CORRECTION_REVIEW_TOKEN
SEED_CONTENT_ON_STARTUP
~~~

If a separate claim verifier is configured in the application, it must return strict structured output tied to the cited passages. Invalid readiness, provider errors, malformed output, unknown evidence IDs, or non-matching quotes fail closed. This automated model assessment is not a human or authoritative legal review.

Without an independent verifier, claims can receive only a conservative direct source-text comparison against complete sentences in evidence cited on that claim. This check avoids keyword-only overlap and paraphrase inference, and does not establish semantic entailment or factual correctness. Paraphrases and contradictions remain inconclusive. A configured verifier's label alone is insufficient: a conclusive result also needs a matching quote from evidence cited on that claim; numeric claims must match their stated numbers. `independent_verification_performed` is true only when a configured verifier actually completed an assessment.

RAG code is in `backend/app/evidence_retrieval.py` (domain-filtered local BM25 ranking), `backend/app/source_content_store.py` (private per-domain upload text), and `backend/app/inference_api.py` (READY checks, prompt context, answer guards, and citation-to-source mapping). No semantic embedding index is configured. Tax slab questions use a deterministic term expansion and a passage reranker that promotes text containing slab terminology, currency thresholds, and percentage rates; it does not add tax facts. Tax slab answers are not generated when retrieved passages lack usable thresholds and rates. A second literal check withholds a draft if numeric values in a tax-slab claim do not appear in that claim's cited passage. That check does not prove the values are correctly associated with each band or that rules are current.

The chat client sends a non-streaming request with `model`, `messages`, `max_tokens`, `prompt_truncate_len`, `temperature`, `stream`, and `n`. The response parser reads the documented completion fields `id`, `object`, `created`, `model`, `choices`, `usage`, and optional `confidence_score`.

## Document preparation workflow

The application accepts one official-source text document per upload. Nugen documents at least 500 words per file and a 100 MB per-file maximum; its developer edition accepts plain-text documents. FinPilot requires UTF-8 and accepts `.txt` and `.md` only, rejecting PDFs and other formats before forwarding a file. The original upload bytes are also saved locally under the configured source-content directory, keyed by domain and a hash of the Nugen document ID. The three expert sets are stored in separate MongoDB collections and uploaded to Nugen with separate categories:

| Domain | Allowed official source hosts | Nugen category |
|---|---|---|
| `tax` | `incometax.gov.in`, `incometaxindia.gov.in` | `finpilot-indian-tax-education` |
| `investment` | `sebi.gov.in` (including `investor.sebi.gov.in`) | `finpilot-investment-education` |
| `insurance` | `irdai.gov.in`, `policyholder.gov.in` | `finpilot-insurance-education` |

Official starting points: [Income Tax Department](https://www.incometax.gov.in/iec/foportal/), [SEBI Investor](https://investor.sebi.gov.in/), and [IRDAI Policyholders](https://irdai.gov.in/policyholders). IRDAI identifies its policyholder site as its official consumer education website. Upload metadata records the submitted source URL and retrieval date; if `retrieved_on` is omitted, the API uses the current UTC date. FinPilot does not scrape these sites automatically: prepare a plain-text document from the official source and upload it.

### Workflow endpoints

| FinPilot endpoint | Behavior |
|---|---|
| `GET /api/nugen/base-models` | Lists Nugen base models marked `alignment_ready`, `text-generation`, and not gated by an access request. |
| `GET /api/nugen/documents?domain=tax&status=READY&limit=50&offset=0` | Calls Nugen's documented list operation, then returns only documents carrying the requested FinPilot domain category and a matching local provenance record. |
| `POST /api/documents` | Multipart fields: `domain`, `file`, `source_url`, optional `retrieved_on` (`YYYY-MM-DD`) and `document_name`. Uploads one document and records its provenance. |
| `GET /api/documents?domain=tax` | Lists locally registered source documents for one domain, including their last checked status. |
| `GET /api/documents/{domain}/{document_id}/status` | Checks Nugen status for a document registered in that domain and saves the returned status. |
| `GET /api/documents/{domain}/active-source` | Returns the explicit active document set and the previous set available for rollback. |
| `PUT /api/documents/{domain}/active-source` | Validates every listed ID is registered in that domain, currently Nugen `READY`, and has locally available UTF-8 text whose byte count matches provenance; then atomically switches the active set. |
| `POST /api/documents/{domain}/active-source/rollback` | Revalidates and restores the previously active set. |
| `POST /api/alignments/prepare` | Validates selected domain documents are `READY`, verifies the base model, and returns an exact request payload with `submitted: false`. |
| `POST /api/alignments/submit` | Explicitly starts one Nugen alignment job only when the caller sends `confirm_submission: true`; records the local tracking ID and provider alignment ID. This can incur cost. |
| `GET /api/alignment-jobs/{tracking_id}` | Reads persistent local submission tracking, including ambiguous or failed submission state. |
| `GET /api/alignments/{alignment_id}/status` | Polls Nugen status, maps the returned `alignment_id` to a model row, checks deployment status, and persists the verified state. |
| `POST /api/alignments/{alignment_id}/deploy` | Explicitly deploys a completed model only with `confirm_deployment: true`; deployment is never automatic. |
| `POST /api/inference` | Checks selected-domain READY documents, verifies the selected model, retrieves relevant local passages, and returns source references plus separate per-claim verification state. |
| `POST /api/ask` | Routes or applies an expert override, runs selected domain experts concurrently, and validates synthesis citations against real retrieved passages. |
| `POST /api/feedback` | Stores a query ID, up/down rating, and optional user correction separately from verified corrections. |
| `GET /api/feedback/summary` | Returns aggregate feedback counts and verified-correction count without comments or question text. |
| `POST /api/verified-corrections` | Stores a reviewer-attested correction only with the configured bearer token and HTTPS sources. |

### Test domain inference in Swagger

Configure `NUGEN_API_KEY` and either `NUGEN_TEST_MODEL_ID` (a base model Nugen explicitly returns as inference-ready text-generation) or the relevant `NUGEN_TAX_MODEL_ID`, `NUGEN_INVESTMENT_MODEL_ID`, or `NUGEN_INSURANCE_MODEL_ID`. A request can also provide `model_id`; it must be a verified inference-ready base model, or it must match that domain's configured aligned-model ID and Nugen must confirm it is deployed. Base models are reported as `unaligned`.

Open `http://127.0.0.1:8000/docs`, expand `POST /api/inference`, choose **Try it out**, and send a body such as:

```json
{
  "domain": "tax",
  "question": "What is the difference between a deduction and a tax rebate?",
  "applicable_as_of": "2026-10-15"
}
```

`domain` must be `tax`, `investment`, or `insurance`; `question` must contain non-whitespace text. `model_id` is optional. Before retrieval, FinPilot checks live Nugen status only for the explicitly active document IDs in the requested domain. If no active set exists, the request fails closed; other `READY` records are never implicitly included. It ranks passages locally and sends only selected-domain passages to Nugen's documented chat-completions API as system context; Nugen itself does not accept document IDs or return source references. For a tax-slab question, table passages are prioritized and the model is not called when retrieved text does not contain both thresholds and rates.

Newly uploaded source bytes are stored in the local source-content store (default: `knowledge_bases/source_documents`). For the three pre-existing READY records, the repository includes local `knowledge_bases/{tax,investment,insurance}_expert.txt` files. FinPilot reuses one only when its fixed domain filename and exact byte size match the registered Mongo record. Those older records do not have a content checksum, so the match is not cryptographic proof that the bytes are identical to the original upload. Add and verify a checksum or re-ingest the original file if stronger provenance is required; do not upload it again to Nugen solely for local retrieval.

Responses contain `evidence` with exact retrieved excerpts, Nugen document IDs, passage IDs, and `[S1]`-style citation markers. `source_references` use the URL and retrieval date recorded with each document. `source_grounding_supported` reports that real retrieved text was included and citations resolve to known passages; it is separate from `claim_verification_status`.

Each sentence-level candidate claim has a `claim_verifications` result with `claim_text`, `status` (`supported`, `contradicted`, or `unverified`), `assessment_basis` (`source_text_comparison`, `independent_verifier`, or `unavailable`), evidence IDs, cited evidence IDs, and a concise note. Source grounding and claim assessment are separate. Direct text matching is deliberately strict and does not infer entailment from keyword overlap. When the optional verifier is enabled, it sees only the claim's own cited passages and must return known passage IDs and exact quotes that FinPilot validates locally; malformed output or verifier failures mark claims unverified and withhold the generated answer. A contradicted result also withholds the answer. Claims assessed by the separate model are automated findings, not authoritative truth. A retrieval date that differs from the requested applicable date is shown beside the answer as a freshness warning: it does not establish that time-sensitive rules applied on the requested date. Claim comparison remains separate from temporal currency.

### Prepare the Tax source cutover

`knowledge_bases/tax_expert.txt` is the reviewed AY 2026-27 source to upload. The existing Tax document `document_01m4zddgyc6jbwcn` cannot be replaced through the current upload endpoint: upload creates a new Nugen document. Keep the existing document and provenance as the rollback source. Every upload is inactive until explicitly selected. The old local source file is preserved at `knowledge_bases/tax_expert.txt.backup-20261015`.

Before activating the old document, stage its matching backup under the existing ID. The command reads local Mongo provenance and writes only local source-content bytes; it does not call Nugen. It rejects a byte-count mismatch and will not overwrite different bytes already staged for that ID:

```powershell
.\.venv\Scripts\python.exe -m backend.app.stage_legacy_source `
  --domain tax `
  --document-id document_01m4zddgyc6jbwcn `
  --file knowledge_bases\tax_expert.txt.backup-20261015
```

The legacy metadata has no content checksum, so exact size and UTF-8 validation are checks against known provenance, not cryptographic proof. The stage command must report success before you use the old document as a local retrieval rollback.

The same local-only staging check can pin the existing Investment and Insurance source files to their registered document IDs. Each command first reads the corresponding Mongo provenance record and refuses to write if the bytes are missing, malformed UTF-8, empty, or a different size:

```powershell
.\.venv\Scripts\python.exe -m backend.app.stage_legacy_source `
  --domain investment `
  --document-id document_01m4zejvsaxae9hr `
  --file knowledge_bases\investment_expert.txt

.\.venv\Scripts\python.exe -m backend.app.stage_legacy_source `
  --domain insurance `
  --document-id document_01m4zepm5h3246v7 `
  --file knowledge_bases\insurance_expert.txt
```

These staging commands do not call Nugen. After staging, check each registered record at `GET /api/documents/{domain}/{document_id}/status`, then explicitly activate that domain's source set. Activation still checks Nugen's current `READY` response and validates all local bytes before the atomic switch.

Inspect the reviewed active file and count words locally before deciding whether to upload:

```powershell
Get-Content -Raw knowledge_bases\tax_expert.txt
$taxText = Get-Content -Raw knowledge_bases\tax_expert.txt
($taxText -split '\s+' | Where-Object { $_ }).Count
```

After activating the existing document as the baseline, upload the reviewed file as a separate Nugen document. This request creates a new document and provenance record; it does not overwrite the existing Tax document or alter its deployed model:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/documents" `
  -F "domain=tax" `
  -F "source_url=https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1" `
  -F "retrieved_on=2026-10-15" `
  -F "document_name=Income Tax Department AY 2026-27 old and new regime comparison" `
  -F "file=@knowledge_bases\tax_expert.txt;type=text/plain"
```

Save the returned root-level `document_id`, poll `GET /api/documents/tax/{document_id}/status` until Nugen reports `READY`, and verify it appears in `GET /api/documents?domain=tax`. Uploaded text is stored locally by that new document ID. Then explicitly switch the active source set to only the new ID. If validation or inference fails, call the rollback endpoint, which rechecks the old document status and local content before restoring it. No provider upload or cutover has been performed by this work.

#### Manual Tax cutover commands

Run these only after reviewing the source and code changes. Start the API in another PowerShell window. First explicitly make the existing source active (after the staging command above succeeds):

```powershell
$baseline = @{ document_ids = @("document_01m4zddgyc6jbwcn") } | ConvertTo-Json
Invoke-RestMethod -Method Put `
  -Uri "http://127.0.0.1:8000/api/documents/tax/active-source" `
  -ContentType "application/json" `
  -Body $baseline | ConvertTo-Json -Depth 5
```

The new active-source registry starts empty, so inference in each expert domain is disabled until that domain's existing corpus is explicitly activated. Before relying on the other domains, initialize their known existing records as well; each PUT checks Nugen status and local content before switching:

```powershell
$investmentBaseline = @{ document_ids = @("document_01m4zejvsaxae9hr") } | ConvertTo-Json
Invoke-RestMethod -Method Put `
  -Uri "http://127.0.0.1:8000/api/documents/investment/active-source" `
  -ContentType "application/json" `
  -Body $investmentBaseline | ConvertTo-Json -Depth 5

$insuranceBaseline = @{ document_ids = @("document_01m4zepm5h3246v7") } | ConvertTo-Json
Invoke-RestMethod -Method Put `
  -Uri "http://127.0.0.1:8000/api/documents/insurance/active-source" `
  -ContentType "application/json" `
  -Body $insuranceBaseline | ConvertTo-Json -Depth 5
```

Upload the reviewed local Tax file, then poll its returned ID. This is the only listed step that creates a Nugen document:

```powershell
$uploaded = curl.exe -sS -X POST "http://127.0.0.1:8000/api/documents" `
  -F "domain=tax" `
  -F "source_url=https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1" `
  -F "retrieved_on=2026-10-15" `
  -F "document_name=Income Tax Department AY 2026-27 old and new regime comparison" `
  -F "file=@knowledge_bases\tax_expert.txt;type=text/plain" | ConvertFrom-Json
$newTaxDocumentId = $uploaded.document_id
$newTaxDocumentId
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/documents/tax/$newTaxDocumentId/status" | ConvertTo-Json -Depth 5
```

When status is `READY`, review the locally registered records and explicitly activate only the new ID. The PUT rechecks provider readiness, domain registration, local UTF-8 content, and exact byte count before atomically switching inference:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/documents?domain=tax" | ConvertTo-Json -Depth 6
$cutover = @{ document_ids = @($newTaxDocumentId) } | ConvertTo-Json
Invoke-RestMethod -Method Put `
  -Uri "http://127.0.0.1:8000/api/documents/tax/active-source" `
  -ContentType "application/json" `
  -Body $cutover | ConvertTo-Json -Depth 5
```

To restore the previous source after cutover, use the explicit rollback route. It preserves both Mongo provenance records and does not change model deployment or `.env`:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/documents/tax/active-source/rollback" | ConvertTo-Json -Depth 5
```

An active source change affects local retrieval only. It does not update, realign, redeploy, or relabel `model_01m4zn7v8md7m8mx`; that deployment and the existing `.env` values remain unchanged. If source content or provider readiness validation fails, stop and review the response rather than uploading repeatedly.

### Windows PowerShell examples

List supported base models and upload a document (replace the local file path):

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/nugen/base-models" | ConvertTo-Json -Depth 6

curl.exe -X POST "http://127.0.0.1:8000/api/documents" `
  -F "domain=tax" `
  -F "source_url=https://www.incometax.gov.in/iec/foportal/" `
  -F "retrieved_on=2026-10-15" `
  -F "document_name=Income Tax Department guide" `
  -F "file=@C:\path\to\guide.txt;type=text/plain"
```

List a domain's registered documents and refresh one status (replace the ID with an upload response ID):

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/documents?domain=tax" | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/documents/tax/<document-id>/status" | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/nugen/documents?domain=tax&status=READY&limit=50&offset=0" | ConvertTo-Json -Depth 6
```

Prepare an alignment preview using IDs returned by the list and upload endpoints. This command only calls the preview route; it does not submit an alignment job:

```powershell
$preview = @{
  domain = "tax"
  alignment_name = "Indian Tax Education Expert"
  base_model_id = "<base model ID from /api/nugen/base-models>"
  document_ids = @("<READY document ID from /api/documents?domain=tax>")
  description = "Educational answers grounded in Income Tax Department sources."
}
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/alignments/prepare" `
  -ContentType "application/json" `
  -Body ($preview | ConvertTo-Json -Depth 5) | ConvertTo-Json -Depth 8
```

Review the returned `request_payload`, `documents`, and base model ID. The preview route never submits a job.

### Manual inference check

After configuring both `NUGEN_API_KEY` and `NUGEN_TEST_MODEL_ID`, run this command manually from the repository root:

```powershell
python -m backend.app.nugen.inference_check
```

The command retrieves the live base-model list, checks that the configured ID is returned as an inference-ready `text-generation` model, then sends one simple chat completion. It prints only the model ID and returned answer. The inference request may consume Nugen credits. The test suite uses mocked HTTP responses and does not run this command.

## Verified Nugen API v3 surface

Authentication is HTTP Bearer (`Authorization: Bearer <NUGEN_API_KEY>`). The client uses the documented host and the public OpenAPI schemas linked from Nugen’s [documentation index](https://docs.nugen.in/llms.txt) and [OpenAPI spec](https://api.nugen.in/openapi-public.json).

| Operation | Route | Verified request and response shape |
|---|---|---|
| Inference settings | `GET /api/v3/inference/capture` | Returns required `enabled: bool` and `models: {model_id: bool}`. Read-only capture settings; this client does not change them. |
| List base models | `GET /api/v3/models/base` | Optional `alignment_ready`, `inference_ready`, `type`, `limit`, `offset`; returns `models[]` with `model_id`, `model_name`, `type`, readiness flags, and optional metadata. |
| Chat completions | `POST /api/v3/inference/chat/completions` | JSON body requires `model` and at least one `{role, content}` message; supports documented generation fields. Non-streaming response has completion `choices` and `usage`. |
| Upload documents | `POST /api/v3/documents/create` | Multipart arrays: required `files[]`, optional `categories[]` and `names[]`; returns `document_ids[]`. Upload processing is asynchronous. |
| List documents | `GET /api/v3/documents/list` | Optional `status`, `limit`, `offset`; returns `documents[]` and `categories[]`. |
| Document status | `GET /api/v3/documents/{document_id}/status` | Returns `status` (`PROCESSING`, `READY`, `FAILED`), `document_id`, timestamps, and optional progress fields. |
| Create alignment | `POST /api/v3/alignment-projects/create` | JSON requires `alignment_name`, `base_model_id`, `document_ids[]`; `workflow_id`, `benchmark_id`, and `description` are optional. Returns `alignment_id` and initial status. This starts an asynchronous alignment job when explicitly called. |
| Alignment status | `GET /api/v3/alignment-projects/{alignment_id}/status` | Returns `alignment_id`, lifecycle `status`, timestamps, progress/queue qualifiers, and optional evaluation fields. |
| List aligned models | `GET /api/v3/models/aligned` | Optional `deployment_status`, `base_model_id`, `limit`, `offset`; returns `domain_aligned_models[]`, including `model_id`, `alignment_id`, and deployment state. |
| Deploy aligned model | `POST /api/v3/models/{model_id}/deployment` | No request body; optional `early` query flag. Returns `model_id`. Explicit method only; never called automatically. |
| Deployment status | `GET /api/v3/models/{model_id}/deployment/status` | Returns `model_id`, status (`UNDEPLOYED`, `DEPLOYING`, `DEPLOYED`, `UNDEPLOYING`), error, timestamps, and optional progress fields. Only `DEPLOYED` serves inference. |

Endpoint references: [inference settings](https://docs.nugen.in/api-reference/inference/inference-settings), [base models](https://docs.nugen.in/api-reference/models/list-base-models), [chat completions](https://docs.nugen.in/api-reference/inference/generate-chat-completions), [upload documents](https://docs.nugen.in/api-reference/documents/upload-documents), [list documents](https://docs.nugen.in/api-reference/documents/list-documents), [document status](https://docs.nugen.in/api-reference/documents/get-document-status), [create alignment](https://docs.nugen.in/api-reference/model-alignment/create-alignment), [alignment status](https://docs.nugen.in/api-reference/model-alignment/get-alignment-status), [aligned models](https://docs.nugen.in/api-reference/models/list-aligned-models), [deploy aligned model](https://docs.nugen.in/api-reference/models/deploy-aligned-model), and [deployment status](https://docs.nugen.in/api-reference/models/get-deployment-status).

The Nugen chat-completions contract documents a non-streaming response with `id`, `object`, `created`, `model`, `choices`, `usage`, and optional `confidence_score`. Its request body has `model`, `messages`, and generation settings; neither the request nor response schema includes document IDs or source references. The official [document list](https://docs.nugen.in/api-reference/documents/list-documents) and [get document](https://docs.nugen.in/api-reference/documents/get-document) operations return metadata, not source text. Nugen's [embedding](https://docs.nugen.in/api-reference/inference/generate-embeddings) and [reranker](https://docs.nugen.in/api-reference/inference/rerank-documents) APIs operate on text provided in their request; no Nugen search/content retrieval endpoint is used here. FinPilot therefore performs BM25 retrieval over locally stored source text and passes excerpts to the chat endpoint. It selects only models returned as inference-ready text-generation base models, or a domain-configured aligned model that Nugen returns with deployment status `DEPLOYED`.

Document constraints stated in Nugen’s upload documentation: each uploaded document needs at least 500 words (training JSONL is exempt); the developer edition accepts plain-text files; the documented per-file maximum is 100 MB. Check the account’s edition before preparing PDFs or other formats.

## Alignment submission and verification

No alignment job is started by application startup, evaluation, or ordinary chat requests. Nugen's create operation is asynchronous and may incur cost. Only call `POST /api/alignments/submit` after reviewing a fresh preview, with `confirm_submission: true`. Provider status is checked through the existing workflow; a result is called aligned only after Nugen confirms successful alignment and a deployed inference-ready model.

For each expert, submit its own domain and document IDs. The provided IDs are tax `document_01m4zddgyc6jbwcn`, investment `document_01m4zejvsaxae9hr`, and insurance `document_01m4zepm5h3246v7`. Before submitting, verify the model ID and the source document status through the preview route; the service checks Nugen's current `alignment_ready` and document `READY` responses.

### Exact manual action to start one alignment job

First run `POST /api/alignments/prepare` and review the returned document IDs, source records, and exact request payload. Then, and only if you want to start the potentially billable asynchronous job, send that same payload to `POST /api/alignments/submit` with `confirm_submission: true`. For the tax document already registered in this project, the PowerShell request is:

```powershell
$alignmentRequest = @{
  domain = "tax"
  alignment_name = "Indian Tax Education Expert"
  base_model_id = "<base model ID returned by /api/nugen/base-models>"
  document_ids = @("document_01m4zddgyc6jbwcn")
  description = "Educational answers grounded in Income Tax Department sources."
}

# Preview first and verify the returned request_payload and READY document.
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/alignments/prepare" `
  -ContentType "application/json" `
  -Body ($alignmentRequest | ConvertTo-Json -Depth 5) | ConvertTo-Json -Depth 8

# This separate, explicit call is the action that submits the real job.
$alignmentRequest.confirm_submission = $true
$submitted = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/alignments/submit" `
  -ContentType "application/json" `
  -Body ($alignmentRequest | ConvertTo-Json -Depth 5)
$submitted | ConvertTo-Json -Depth 8
```

For Investment and Insurance, prepare and submit separate requests using their own domains and document IDs (`document_01m4zejvsaxae9hr` and `document_01m4zepm5h3246v7`). Do not reuse the tax document across experts. Obtain each base-model ID from the current model-list response rather than copying an example or relying on the inference fallback.

After a job has been explicitly submitted:

1. Poll `GET /api/alignments/{alignment_id}/status`. FinPilot persists the domain, source document IDs, base model ID, provider job ID and reported state.
2. A completed `READY`/`UNDEPLOYED` job still is not inference-ready. FinPilot additionally requires one `GET /api/v3/models/aligned` row tied to the same `alignment_id` and base model, and a matching `DEPLOYED` response from both the aligned-model list and deployment-status endpoint.
3. If status reports a completed model as `UNDEPLOYED`, review the returned model ID and separately call `POST /api/alignments/{alignment_id}/deploy` with `{"confirm_deployment": true}`. This is a second explicit provider action. Poll status until `inference_ready: true` and `alignment_status: "aligned"`.
4. Only then set the corresponding `NUGEN_TAX_MODEL_ID`, `NUGEN_INVESTMENT_MODEL_ID`, or `NUGEN_INSURANCE_MODEL_ID` to that returned, domain-tracked model ID. A base model such as `meta-llama/Llama-3.2-3B-Instruct` remains an unaligned fallback and must be explicitly selected as such.

The documented alignment-status operation does not include the detailed failure reason. FinPilot records that the job failed, but cannot show a provider failure explanation until Nugen's separate alignment-project detail response schema is available and verified.

The provider create schema supports optional `benchmark_id`; this API does not include one. Each domain is tracked as a separate alignment project. A submitted, `READY`, or `UNDEPLOYED` job is still not reported as aligned until Nugen confirms the matching model is `DEPLOYED` and serves inference.

FinPilot AI is for general financial education, not licensed advice. It will avoid unsupported personalized recommendations and point users to a qualified professional where appropriate.

## Release checks and operations

The API root serves the compiled React client when present. `GET /health` is process liveness only. `GET /ready` checks that the Nugen key is configured and asks MongoDB to ping and initialize the non-destructive unique document-ID indexes. It does not call Nugen, confirm that a model is inference-ready, or prove that any source document is active. A 503 response means the corresponding local dependency check did not pass.

From the repository root, run the local quality gates in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Push-Location frontend
npm ci
npm run test
npm run lint
npm run build
Pop-Location
git diff --check
```

GitHub Actions runs the same backend and frontend checks on pushes and pull requests. The CI workflow needs no application secrets and grants only `contents: read`.

### Knowledge source review

The local Tax source already contains both ordinary AY 2026–27 slab tables for individuals under 60. The broad “How do income tax slabs work?” retrieval regression test asserts that both regime tables rank first; generic introductory text alone is separately tested as insufficient. I rechecked the official [Income Tax Department AY 2026–27 page](https://www.incometax.gov.in/iec/foportal/help/individual/return-applicable-1) on 17 October 2026: its under-60 table shows the same ordinary old and new regime thresholds and rates, and its rebate text is conditional for resident individuals. The source file's own retrieval date remains 15 October 2026; this review did not change its bytes or provider registration. Its stated scope and warnings still apply, and the official page remains the authority for current use.

The Investment corpus covers risk, diversification, mutual funds, liquidity risk, and emergency reserves, but previously lacked a focused source comparing savings with investing. A supplemental SEBI source is prepared at [sebi_saving_investing_basics_2026_10_17.txt](knowledge_bases/prepared/sebi_saving_investing_basics_2026_10_17.txt). It records its official SEBI URLs and retrieval date and has not been uploaded, registered, or activated. The current active Investment corpus therefore remains unchanged until a human reviews and explicitly uploads, checks, registers, and activates the new document. The Insurance source remains the existing general IRDAI education set; policy-specific coverage still requires current insurer policy wording.

No provider operation was performed for this review. Active-source selections and live Mongo/Nugen document status were not queried, and no inference, upload, alignment, or deployment request was sent. Do not treat the local prepared Investment source or a passing mocked retrieval test as live corpus activation.

### GitHub and production deployment status

The repository now includes a Dockerfile for a one-service deployment. No production deployment or push was performed. Configure secrets in the chosen hosting provider's secret manager; do not place them in a workflow or image layer.

Production CORS is disabled because the React UI and API are served from the same origin. A hosting provider still needs the MongoDB connection, Nugen credentials and model IDs, optional reviewer token, persistent storage for uploaded source content, TLS, and public smoke checks.

## Multi-expert API, feedback, and evaluation

Open Swagger at http://127.0.0.1:8000/docs. A minimal ask request is:

~~~json
{
  "question": "How do saving and investing differ?",
  "applicable_date": "2026-10-17"
}
~~~

Omit experts to let the router choose, or supply an experts array with one or more of tax, investment, and insurance. The response includes the routing method and reason, individual expert results, final answer, actual source passages, gaps, and per-stage timings. An out-of-scope router result invokes no expert.

The response request_id is the query_id for POST /api/feedback. Feedback records can include an up/down rating, comment, and suggested experts; they are never used as verified truth. GET /api/feedback/summary exposes aggregate counts only. A separate reviewer can POST /api/verified-corrections with a correction, reviewer attestation, and HTTPS source references. That endpoint is disabled unless FINPILOT_CORRECTION_REVIEW_TOKEN is configured in the server environment and the request includes its bearer token. Ordinary feedback cannot create a correction.

The fixed benchmark is eval/questions.json. scripts/run_eval.py has two modes:

~~~powershell
.\.venv\Scripts\python.exe scripts\run_eval.py --routing-only
~~~

Router-only mode skips baseline and domain-expert answers, but can still call the configured router model. Full before/after evaluation makes a direct base-model call and full /api/ask requests for the same cases; it requires the deliberate --confirm-full-eval option and can consume Nugen credits. It was not run during this change. Results are written under eval/results_*.json and are ignored by Git.

| Metric | Baseline | Full system |
|---|---:|---:|
| Routing accuracy | Placeholder — not measured | Placeholder — not measured |
| Required-term coverage | Placeholder — not measured | Placeholder — not measured |
| Grounded-answer rate | Placeholder — not measured | Placeholder — not measured |
| Average latency | Placeholder — not measured | Placeholder — not measured |
| Failure cases | Placeholder — not measured | Placeholder — not measured |

## Corpus and source-content readiness

Run the corpus checks with:

~~~powershell
.\.venv\Scripts\python.exe scripts\check_corpus.py
~~~

The checker reports per-file word counts and fails for missing source/date metadata, fewer than 500 words, or known assistant-instruction and document-maintenance headings. The current Investment and Insurance active knowledge files contain such instruction headings and are flagged. Their knowledge-base contents were intentionally left unchanged; clean those files in a separately reviewed corpus edit before using this check as a deployment gate.

For the three legacy registrations, a fresh deployment needs the Mongo provenance and active-source records plus the matching committed `knowledge_bases/{tax,investment,insurance}_expert.txt` file. Legacy retrieval accepts a file only when its byte count matches registered metadata; missing content, invalid UTF-8, or mismatched bytes returns a clear source-content error. New uploads are stored under `knowledge_bases/source_documents/`, which is excluded from the image and Git. The local-only seeder copies the matching committed file to the hashed content-store path only for registered `READY` records, only when byte counts match, and only when content is missing. It never overwrites and never calls Nugen:

~~~powershell
.\.venv\Scripts\python.exe scripts\seed_content.py
~~~

To opt into the same idempotent local seed on API startup, set `SEED_CONTENT_ON_STARTUP` to `true`. It reads Mongo metadata and committed files only. Leave it unset/false if you want startup to avoid that Mongo read. The active-source registry and provider statuses remain independently required; seeding does not register, upload, activate, align, or deploy anything.

The API applies an in-memory per-process request limit to `/api/ask` and `/api/inference`, caps JSON inference bodies at 1 MiB, and caps questions at 2,000 characters. In-memory limits reset on restart and are not shared between multiple workers. Production CORS is disabled because the API and compiled frontend share one origin; local development allows the Vite origins.

## Docker deployment

Build and run locally from the repository root:

~~~powershell
docker build -t finpilot-ai .
docker run --rm -p 8000:8000 finpilot-ai
~~~

The image builds the existing React application in a Node stage, then serves its compiled files and FastAPI from one Python service. For Render, create a Docker web service from this repository, use `/health` for the health check, and let the service bind to the platform's `PORT`. Create a MongoDB Atlas cluster and database user, allow the Render outbound IP range (or use an approved network access policy), require TLS, and put the Atlas connection string in the Render secret manager as `MONGODB_URI`. Add `MONGODB_DATABASE`, `NUGEN_API_KEY`, `NUGEN_API_BASE_URL`, and the configured model ID variables there. Add `FINPILOT_CORRECTION_REVIEW_TOKEN` only when reviewer corrections are enabled. Attach a persistent disk at `/app/knowledge_bases/source_documents` so uploaded source text survives redeploys. Set `SEED_CONTENT_ON_STARTUP` only if you want the opt-in local seed from the image's committed corpus and existing READY Mongo records. Do not put secret values in Dockerfile, build arguments, Git, or README. After deployment, check `/health`, `/docs`, active-source selections, and provider alignment/deployment status before testing inference.

## Current verification and limitations

No model alignment, live provider state, production deployment, or benchmark result was checked during this work. Do not infer alignment from a configured model ID: the application should report aligned only after Nugen confirms completion, deployment, and inference readiness. Retrieval uses local BM25 with deterministic term expansion, not semantic embeddings. Without a configured verifier, claim status is limited to conservative source-text comparison and does not establish semantic entailment; even automated verification is not human or legal review. The corpus is educational and dated, and its retrieval date does not prove that a rule is current.

| Expert domain | Alignment verification in this run |
|---|---|
| Tax | Not queried; alignment is not established by this report |
| Investment | Not queried; alignment is not established by this report |
| Insurance | Not queried; alignment is not established by this report |

What I would do next:

1. Replace the flagged Investment and Insurance instruction text with reviewed official-source content; do not deploy while the corpus checker fails.
2. Create MongoDB Atlas, then migrate or re-register the existing document/provenance and active-source records and run the safe content seeder.
3. Set Render's environment variable names in its secret manager, check each provider alignment and deployment status, then deploy and smoke-test.
4. Run `scripts/run_eval.py --routing-only` and review the fixed benchmark before making performance claims.
5. Push the reviewed commits to GitHub, deploy, submit the hiring challenge, and book the interview. Any paid alignment or deployment request still requires explicit review and confirmation.
