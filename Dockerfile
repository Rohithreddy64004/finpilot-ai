FROM node:22-alpine AS frontend-build
WORKDIR /workspace/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000
WORKDIR /app

RUN groupadd --system --gid 10001 finpilot \
    && useradd --system --uid 10001 --gid finpilot \
        --home-dir /home/finpilot --create-home --shell /usr/sbin/nologin finpilot

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY --chown=root:root backend/ /app/backend/
COPY --chown=root:root scripts/ /app/scripts/
COPY --chown=root:root eval/ /app/eval/
COPY --chown=root:root knowledge_bases/tax_expert.txt /app/knowledge_bases/
COPY --chown=root:root knowledge_bases/investment_expert.txt /app/knowledge_bases/
COPY --chown=root:root knowledge_bases/insurance_expert.txt /app/knowledge_bases/
COPY --chown=root:root knowledge_bases/prepared/ /app/knowledge_bases/prepared/
COPY --from=frontend-build --chown=root:root /workspace/frontend/dist/ /app/frontend/dist/

RUN install -d --owner=finpilot --group=finpilot --mode=0750 \
    /app/knowledge_bases/source_documents

USER finpilot:finpilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=4)"]
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
