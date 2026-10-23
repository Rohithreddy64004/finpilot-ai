import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
RENDER_ENVIRONMENT_NAMES = {
    "NUGEN_API_KEY",
    "NUGEN_API_BASE_URL",
    "NUGEN_TEST_MODEL_ID",
    "NUGEN_TAX_MODEL_ID",
    "NUGEN_INVESTMENT_MODEL_ID",
    "NUGEN_INSURANCE_MODEL_ID",
    "NUGEN_CLAIM_VERIFIER_MODEL_ID",
    "MONGODB_URI",
    "MONGODB_DATABASE",
    "FRONTEND_ORIGINS",
    "FINPILOT_CORRECTION_REVIEW_TOKEN",
    "NUGEN_TIMEOUT_SECONDS",
    "SEED_CONTENT_ON_STARTUP",
}


def test_render_blueprint_is_one_manual_docker_service_with_healthcheck() -> None:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert text.count("  - type: web") == 1
    assert "runtime: docker" in text
    assert "healthCheckPath: /health" in text
    assert "autoDeployTrigger: off" in text
    keys = set(re.findall(r"^\s+- key: ([A-Z0-9_]+)\s*$", text, re.MULTILINE))
    assert keys == RENDER_ENVIRONMENT_NAMES
    assert text.count("sync: false") == len(RENDER_ENVIRONMENT_NAMES)
    assert not re.search(r"^\s+value:", text, re.MULTILINE)


def test_container_is_non_root_uses_render_port_and_excludes_local_secrets() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-alpine AS frontend-build" in dockerfile
    assert "FROM python:3.12-slim AS runtime" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "pip install --no-cache-dir -r /app/backend/requirements.txt" in dockerfile
    assert "COPY --chown=root:root scripts/ /app/scripts/" in dockerfile
    assert "COPY --chown=root:root eval/ /app/eval/" in dockerfile
    assert "knowledge_bases/tax_expert.txt" in dockerfile
    assert "knowledge_bases/investment_expert.txt" in dockerfile
    assert "knowledge_bases/insurance_expert.txt" in dockerfile
    assert "knowledge_bases/prepared/" in dockerfile
    assert "COPY --from=frontend-build --chown=root:root /workspace/frontend/dist/ /app/frontend/dist/" in dockerfile
    assert "--port ${PORT:-8000}" in dockerfile
    assert "PYTHONUNBUFFERED=1" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "USER finpilot:finpilot" in dockerfile
    assert "install -d --owner=finpilot --group=finpilot --mode=0750" in dockerfile
    health_start_period = re.search(r"--start-period=(\d+)s", dockerfile)
    assert health_start_period and int(health_start_period.group(1)) >= 30
    assert "HEALTHCHECK " in dockerfile
    assert "--timeout=5s" in dockerfile
    assert "/health" in dockerfile
    assert not re.search(r"^\s*COPY\s+\.env", dockerfile, re.MULTILINE)

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (
        ".env*",
        ".venv/",
        "**/node_modules/",
        "**/__pycache__/",
        "**/.pytest_cache/",
        ".git/",
        ".codex-backups/",
        "knowledge_bases/source_documents/",
        "**/tests/",
        "data/",
    ):
        assert pattern in dockerignore


def test_gitignore_excludes_secrets_caches_and_generated_source_bytes() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".env",
        ".venv/",
        "node_modules/",
        "__pycache__/",
        ".pytest_cache/",
        ".codex-backups/",
        "*.backup-*",
        "data/",
        "eval/results_*.json",
        "knowledge_bases/source_documents/",
        "frontend/.vite/",
        "frontend/.turbo/",
    ):
        assert pattern in text


def test_firebase_hosting_serves_dist_with_spa_rewrite_and_cache_headers() -> None:
    frontend = ROOT / "frontend"
    firebase = json.loads((frontend / "firebase.json").read_text(encoding="utf-8"))
    hosting = firebase["hosting"]
    assert hosting["public"] == "dist"
    assert hosting["ignore"] == ["firebase.json", "**/.*", "**/node_modules/**"]
    assert hosting["rewrites"] == [{"source": "**", "destination": "/index.html"}]
    headers = {item["source"]: item["headers"][0]["value"] for item in hosting["headers"]}
    assert headers["/assets/**"] == "public, max-age=31536000, immutable"
    assert headers["/index.html"] == "no-cache"
    firebaserc = json.loads((frontend / ".firebaserc").read_text(encoding="utf-8"))
    assert firebaserc["projects"]["default"] == "finpilot-ai-nugen"
    assert ".firebase/" in (frontend / ".gitignore").read_text(encoding="utf-8")
    assert not (frontend / "public" / "index.html").exists()
