FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=UTC
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt && useradd --system --uid 10001 app
COPY backend/ .
COPY reference/ /app/reference/
COPY LICENSE README.md docker-compose.yml .env.example .gitignore design_guidelines.json /app/
COPY deploy/ /app/deploy/
COPY docs/ /app/docs/
COPY frontend/package.json frontend/yarn.lock frontend/craco.config.js frontend/tailwind.config.js frontend/postcss.config.js frontend/jsconfig.json frontend/components.json /app/frontend/
COPY frontend/src/ /app/frontend/src/
COPY frontend/public/ /app/frontend/public/
USER app
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=4 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/health/deep', timeout=6).status == 200 else 1)"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--no-server-header"]
