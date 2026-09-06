FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
WORKDIR /app
COPY pyproject.toml README.md constraints.lock ./
COPY app ./app
RUN pip install --no-cache-dir --constraint constraints.lock . \
    && pip check \
    && mkdir -p /data \
    && chown -R 10001:10001 /data
ENV HOME=/tmp
USER 10001:10001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
