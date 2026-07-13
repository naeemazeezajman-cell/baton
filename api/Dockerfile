FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

# Migrations run on every start (idempotent), then uvicorn takes over PID 1.
# Container Apps ingress targets $PORT (default 8000); bind all interfaces.
# Inline sh -c instead of a start.sh so a Windows checkout can't ship CRLF into the entrypoint.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
