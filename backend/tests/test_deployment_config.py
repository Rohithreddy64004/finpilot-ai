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
    "MONGODB_URI",
    "MONGODB_DATABASE",
    "FINPILOT_CORRECTION_REVIEW_TOKEN",
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


def test_container_binds_to_render_port_and_excludes_local_secrets() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--port $PORT" in dockerfile
    assert "/health" in dockerfile
    assert "HEALTHCHECK" in dockerfile

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in dockerignore
    assert "knowledge_bases/source_documents/" in dockerignore
    assert ".codex-backups/" in dockerignore


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
