# File Guide

This guide describes every file tracked by Git after it is added. Each entry summarizes the file's role based on its contents.

## Root

.dockerignore - Excludes local secrets, caches, tests, and source documents from Docker build contexts.
.gitattributes - Keeps committed knowledge text files in LF line-ending format.
.gitignore - Excludes secrets, local environments, caches, build output, and generated source content from Git.
Dockerfile - Builds the frontend and runs the FastAPI application as a non-root container.
README.md - Documents FinPilot's architecture, trust design, setup, deployment, tests, and limitations.
render.yaml - Defines the Render Docker service, health check, and environment variable names.

## .github/workflows

.github/workflows/ci.yml - Runs backend tests, frontend tests, linting, and the frontend build on GitHub Actions.

## backend

backend/__init__.py - Marks the backend directory as the FinPilot Python package.
backend/requirements.txt - Lists the Python packages needed by the API and its tests.

## backend/app

backend/app/__init__.py - Marks the application directory as the FastAPI package.
backend/app/alignment_jobs.py - Defines storage interfaces and MongoDB persistence for submitted alignment jobs.
backend/app/alignment_workflow.py - Refreshes alignment records using provider status, aligned-model lists, and deployment status.
backend/app/ask_api.py - Runs multi-expert requests, maps citations, validates synthesis, and builds the final answer.
backend/app/claim_verification.py - Checks individual claims against their cited passages or an optional verifier.
backend/app/config.py - Loads and validates application settings from environment variables.
backend/app/content_seeding.py - Copies matching READY registered corpus text into local retrieval storage.
backend/app/corpus_quality.py - Checks source text for provenance, useful length, and instruction-style headings.
backend/app/evidence_retrieval.py - Loads domain-specific source passages and ranks them with BM25 retrieval.
backend/app/inference_api.py - Retrieves evidence, checks model readiness, generates domain answers, and validates citations.
backend/app/interaction_api.py - Exposes endpoints for feedback and reviewer-authorized corrections.
backend/app/interactions.py - Defines repository interfaces and MongoDB storage for queries, feedback, and corrections.
backend/app/main.py - Creates the FastAPI app, configures middleware and routes, and serves health and frontend files.
backend/app/rate_limit.py - Limits costly requests with a process-local sliding time window.
backend/app/request_limits.py - Rejects oversized inference request bodies before normal request parsing.
backend/app/router.py - Routes questions to finance experts with a model prompt and deterministic fallback rules.
backend/app/schemas.py - Defines typed request, response, source, alignment, and persistence records.
backend/app/source_content_store.py - Stores source text locally by domain and document ID without replacing existing bytes.
backend/app/source_diagnostics.py - Reports active source status, local byte counts, and corpus quality issues.
backend/app/source_documents.py - Defines source-document repository interfaces and MongoDB metadata storage.
backend/app/stage_legacy_source.py - Copies a verified legacy corpus file into storage under its registered document ID.
backend/app/synthesizer.py - Combines expert answers using only the supplied evidence and a verified inference model.
backend/app/workflow_api.py - Provides endpoints for source documents, activation, diagnostics, and alignment workflows.

## backend/app/nugen

backend/app/nugen/__init__.py - Re-exports the Nugen client and its safe API exception types.
backend/app/nugen/alignment_service.py - Wraps documented Nugen document, alignment, aligned-model, and deployment operations.
backend/app/nugen/claim_verifier.py - Uses Nugen to assess claims and requires exact quotes from supplied evidence.
backend/app/nugen/client.py - Sends authenticated HTTP requests to the documented Nugen API and parses responses.
backend/app/nugen/errors.py - Defines credential-safe exception classes for Nugen configuration and API failures.
backend/app/nugen/inference_check.py - Manually checks whether a configured Nugen model is inference-ready.
backend/app/nugen/schemas.py - Defines typed request and response models for Nugen API operations.

## backend/tests

backend/tests/conftest.py - Disables optional startup seeding so tests do not require external services.
backend/tests/test_alignment_jobs.py - Tests domain-scoped alignment-job queries and result limits.
backend/tests/test_ask_api.py - Tests routing, expert orchestration, citation mapping, errors, excerpts, and rate limits.
backend/tests/test_claim_verification.py - Tests claim segmentation, citation ownership, numeric checks, and verifier failures.
backend/tests/test_content_seeding.py - Tests safe, idempotent seeding of matching registered source content.
backend/tests/test_corpus_check.py - Tests corpus file discovery and actionable quality warnings.
backend/tests/test_cors.py - Tests allowed origins, denied origins, preflight requests, and CORS error responses.
backend/tests/test_deployment_config.py - Checks Docker, Render, ignore rules, and Firebase deployment configuration.
backend/tests/test_evaluation_data.py - Checks evaluation dataset coverage, corpus terms, and evaluation safeguards.
backend/tests/test_evidence_retrieval.py - Tests source provenance, BM25 ranking, domain separation, and retrieval edge cases.
backend/tests/test_grounded_ask.py - Tests a mocked end-to-end ask using committed corpora and source references.
backend/tests/test_health.py - Tests health, readiness, frontend serving, and same-origin CORS behavior.
backend/tests/test_inference_api.py - Tests retrieval-grounded inference, model readiness, citations, fallbacks, and errors.
backend/tests/test_interaction_api.py - Tests feedback storage and authorization for verified corrections.
backend/tests/test_nugen_claim_verifier.py - Tests verifier readiness, exact evidence quotes, and malformed output handling.
backend/tests/test_nugen_client.py - Tests documented Nugen requests, response parsing, retries, and safe errors.
backend/tests/test_nugen_config.py - Tests required settings, URL validation, timeouts, CORS origins, and seeding defaults.
backend/tests/test_nugen_inference_check.py - Tests inference readiness checks and safe diagnostic output.
backend/tests/test_operational_scripts.py - Tests script help, corpus registration safeguards, smoke checks, and alignment reports.
backend/tests/test_rate_limit.py - Tests sliding-window request limits and expiration boundaries.
backend/tests/test_request_limits.py - Tests request length validation and oversized-body rejection.
backend/tests/test_router.py - Tests expert decisions, routing examples, keyword fallbacks, and out-of-scope handling.
backend/tests/test_schemas.py - Tests request parsing, default alignment labels, and source URL validation.
backend/tests/test_source_content_store.py - Tests domain-scoped local storage and protection against content replacement.
backend/tests/test_source_diagnostics.py - Tests active-source byte counts, quality findings, and missing local content.
backend/tests/test_source_documents.py - Tests source metadata persistence, active-source switching, and failure handling.
backend/tests/test_stage_legacy_source.py - Tests copying legacy source bytes only when registered sizes match.
backend/tests/test_synthesizer.py - Tests evidence-limited synthesis and refusal of unverified models or empty output.
backend/tests/test_workflow_api.py - Tests document upload, source activation, health, alignment, and deployment endpoints.

## eval

eval/questions.json - Lists fixed evaluation questions, expected experts, and selected answer terms.
eval/router_examples.json - Supplies few-shot finance and out-of-scope examples to the question router.

## frontend (config)

frontend/.firebaserc - Selects the default Firebase project for Hosting commands.
frontend/.gitignore - Excludes Firebase state, frontend build output, dependencies, and local environment files.
frontend/README.md - Explains frontend installation, local development, and the public API base URL setting.
frontend/eslint.config.js - Configures ESLint rules for the TypeScript and React frontend.
frontend/firebase.json - Configures Firebase Hosting output, SPA rewrites, and cache headers.
frontend/index.html - Provides the browser document, page metadata, favicon, and React mount element.
frontend/package-lock.json - Locks frontend package versions and their dependency graph for npm installs.
frontend/package.json - Declares frontend dependencies and npm development, test, lint, and build commands.
frontend/tsconfig.app.json - Configures TypeScript checking for the React application source.
frontend/tsconfig.json - Connects the frontend application and tooling TypeScript projects.
frontend/tsconfig.node.json - Configures TypeScript checking for Vite and Node-based frontend tooling.
frontend/vite.config.ts - Configures React development, local API proxies, and Vitest settings.

## frontend (src)

frontend/src/App.css - Styles the application layout, chat view, answer cards, sources, and responsive states.
frontend/src/App.tsx - Manages the chat interface, asks questions, handles errors, and submits feedback.
frontend/src/index.css - Defines global colors, typography, focus outlines, and base page styles.
frontend/src/main.tsx - Mounts the React application and loads its global stylesheet.

## frontend (components)

frontend/src/components/AskResponsePanel.tsx - Displays the final answer, trust labels, sources, feedback, and expandable details.
frontend/src/components/ChatInput.tsx - Renders the controlled question composer and send button.
frontend/src/components/ChatMessage.tsx - Displays user, assistant, loading, and error messages with retry actions.
frontend/src/components/MarkdownContent.tsx - Renders safe Markdown and turns evidence citations into source links.
frontend/src/components/Sidebar.tsx - Shows FinPilot branding and the New chat action.
frontend/src/components/SourceList.tsx - Displays ranked source links and expands the source list on request.
frontend/src/components/StatusBadge.tsx - Renders a small status label with a selected visual tone.

## frontend (services)

frontend/src/services/api.ts - Sends backend requests and validates API responses, citations, and safe error messages.

## frontend (types)

frontend/src/types/api.ts - Defines TypeScript contracts for inference, sources, answers, experts, and feedback.
frontend/src/types/chat.ts - Defines frontend chat entries and conversation message collections.

## frontend (tests)

frontend/src/App.test.tsx - Tests the answer interface, details, badges, examples, loading, feedback, and errors.
frontend/src/components/MarkdownContent.test.tsx - Tests Markdown rendering, citation links, escaping, and unsafe-link rejection.
frontend/src/services/api.test.ts - Tests API requests, response validation, citations, feedback, and error mapping.
frontend/src/test/setup.ts - Resets timers, mocks, environment overrides, and rendered components after each test.

## frontend (public)

frontend/public/favicon.svg - Supplies the FinPilot browser icon copied into the built frontend.

## knowledge_bases

knowledge_bases/insurance_expert.txt - Stores sourced insurance education passages used by insurance retrieval.
knowledge_bases/investment_expert.txt - Stores sourced investment education passages used by investment retrieval.
knowledge_bases/prepared/sebi_saving_investing_basics_2026_10_17.txt - Holds a reviewed SEBI savings and investing summary prepared for possible corpus registration.
knowledge_bases/prepared/tax_regime_comparison_ay_2026_27.txt - Holds an Income Tax Department regime comparison prepared for manual review and upload.
knowledge_bases/tax_expert.txt - Stores sourced Indian tax education passages used by tax retrieval.

## scripts

scripts/alignment_report.py - Refreshes provider status and reports tracked alignment and deployment state by domain.
scripts/check_corpus.py - Scans committed expert and prepared text for provenance, length, and prompt leakage.
scripts/diagnose.py - Compares active registered source records with local content and corpus-quality checks.
scripts/register_corpus.py - Previews corpus registration plans and optionally uploads and activates corpus files.
scripts/run_eval.py - Runs the fixed router and answer evaluation through routing-only or explicit provider-backed modes.
scripts/seed_content.py - Seeds missing local retrieval bytes from registered READY source records.
scripts/smoke_test.py - Checks live API routing and cited evidence without printing generated answer text.

## docs

docs/FILE_GUIDE.md - Lists tracked project files, grouped local items, and the main request path through the application.

## Untracked local items

.env, frontend/.env.local - Local environment files; .env is the local secrets file, never committed, and neither file was opened.
knowledge_bases/source_documents/* (6 files) - Local source-document content files; their names and contents were not opened.
.venv/ - Local Python virtual environment used for project commands and excluded from Git.
frontend/node_modules/ - Installed frontend dependencies used by npm commands and excluded from Git.
frontend/dist/ - Generated frontend output consumed by Firebase Hosting and the single-service Docker build.
.pytest_cache/ - Ignored local pytest cache generated by backend test runs.

## How a request flows

1. frontend/src/services/api.ts sends the question to FastAPI.
2. backend/app/ask_api.py accepts it and coordinates the expert request.
3. backend/app/router.py selects the relevant experts or refuses out-of-scope questions.
4. backend/app/inference_api.py uses backend/app/evidence_retrieval.py to retrieve passages and produce grounded expert answers.
5. backend/app/nugen/client.py makes provider requests; backend/app/synthesizer.py combines answers for multi-expert questions.
6. backend/app/ask_api.py returns the result, and frontend/src/services/api.ts validates it for the UI.
