FROM public.ecr.aws/docker/library/python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

# Ship compiled bytecode: Lambda's file system is read-only, so Python cannot
# cache .pyc files there and would recompile on every cold start.
ENV UV_COMPILE_BYTECODE=1
RUN uv sync --no-dev


FROM public.ecr.aws/docker/library/python:3.12-slim

# AWS Lambda Web Adapter: on Lambda it turns each invocation into an HTTP
# request to uvicorn (PORT) once GET /health answers. Outside Lambda it never
# starts, so the image still runs as a plain container.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV PORT=8000
ENV AWS_LWA_READINESS_CHECK_PATH=/health

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH"
# Logs (print) reach CloudWatch at once, before Lambda freezes the process.
ENV PYTHONUNBUFFERED=1

# UV_COMPILE_BYTECODE compiled site-packages only: the app is installed
# editable, so compile its own modules too. unchecked-hash: the .pyc files are
# used as they are, whatever timestamps the image's files end up with.
RUN python -m compileall -q --invalidation-mode unchecked-hash /app/app

# app/duckdb.py runs INSTALL/LOAD httpfs (and its credential_chain secret
# loads the aws extension) on every connection. Both would be downloaded into
# $HOME/.duckdb, which is read-only on Lambda, so install them into the image:
# INSTALL is then a no-op and LOAD only reads the files.
ENV HOME=/app
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs; INSTALL aws; LOAD httpfs; LOAD aws')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
