# FinPilot AI frontend

Responsive React and TypeScript chat UI for the existing FinPilot FastAPI backend. The frontend sends real requests; it has no mock-answer or provider-credential path.

## Prerequisites

- Node.js 22 or newer and npm
- The FinPilot backend running on `http://127.0.0.1:8000`
- Backend MongoDB/source configuration and Nugen model configuration when you want real inference

## Install and configure

From the repository root, open a PowerShell terminal:

```powershell
Set-Location frontend
npm ci
```

The frontend uses same-origin API paths by default, and Vite proxies them to the local backend during development. For a separate frontend deployment, set `VITE_API_BASE_URL` to the backend origin before building; for example, use `https://finpilot-ai-t691.onrender.com` for the current Render backend. The value is embedded in the public browser bundle. No frontend environment file is required.

`VITE_*` values are compiled into browser code and are public. Never put Nugen keys, MongoDB credentials, or other secrets in the frontend environment. Keep `NUGEN_API_KEY` and model IDs in the backend `.env`.

## Start the application

In one PowerShell terminal at the repository root, start FastAPI:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

In another PowerShell terminal:

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1
```

Open `http://localhost:5173`. Vite proxies API requests to the local backend, so normal local development does not need CORS. If the browser calls the backend directly, include `http://localhost:5173` in `FRONTEND_ORIGINS`. Other configured origins must use HTTPS and be listed exactly. Wildcards are rejected, and credentials are disabled.

## Backend integration

The UI uses `POST /api/inference` with the actual request fields:

```json
{
  "domain": "tax",
  "question": "What is the difference between a tax deduction and a rebate?"
}
```

Accepted domains are `tax`, `investment`, and `insurance`. The optional backend fields `model_id` and `applicable_as_of` are omitted by the UI so server-side configuration controls model selection and the default applicable date.

The response is rendered from the backend's `DomainInferenceResponse`: Markdown answer, model/alignment status, grounding note, retrieved excerpts and source references, per-claim results, limitations, applicable date, and educational disclaimer. Assistant Markdown uses `react-markdown` with GFM support; raw HTML is skipped, external links are restricted to HTTP(S), and `[S1]` citations link to the matching evidence card. Missing optional fields render safely. A citation or `source_grounding_supported` indicator is not presented as proof that a claim is true. Per-claim labels distinguish exact source-text matches from assessments by a separate verifier. `independent_verification_performed` is the authoritative UI flag for whether that separate verifier actually ran.

The backend currently sends each question as a standalone inference request. Previous turns remain visible in the current page session but are not sent as conversation context. Starting a new chat keeps prior chats available in the sidebar until the page is reloaded.

For a production frontend on another origin, set `FRONTEND_ORIGINS` on FastAPI to the precise trusted HTTPS origin used by the frontend, then restart the API. Do not use `*`.

## Checks

Run the frontend checks from `frontend/`:

```powershell
npm run test
npm run lint
npm run build
```

The API-service and UI tests mock `fetch`; they do not call Nugen, MongoDB, or the live inference API. `npm run build` runs the strict TypeScript project build followed by Vite's production bundle.

## Troubleshooting

- **“Cannot reach the FinPilot backend”**: check that Uvicorn is running and the Vite development server is proxying `/api` requests to it.
- **CORS preflight failure**: verify the browser origin exactly matches one entry in backend `FRONTEND_ORIGINS`, then restart FastAPI.
- **HTTP 422**: the backend rejected the request shape or selected domain; the UI displays FastAPI's validation summary.
- **HTTP 409/503**: check that the selected domain has active READY sources and a configured inference-ready model in the backend.
- **No grounded answer**: the API intentionally withholds generated wording when citations are missing or invalid, and it does not invent missing source evidence.

Live inference may use provider credits. Frontend tests and builds do not make live inference requests.
