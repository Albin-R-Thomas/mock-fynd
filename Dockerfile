FROM python:3.11-slim-bookworm
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app && mkdir -p /data && chown app:app /data /app
COPY --chown=app:app app ./app
COPY --chown=app:app tests ./tests
COPY --chown=app:app scripts ./scripts
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
