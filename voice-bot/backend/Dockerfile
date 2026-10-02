# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# DSA Document AI - Voice backend (linux/amd64 or linux/arm64).

# ---- build: compilers only here, in case a dependency has no wheel for the platform
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --prefix=/install -r /tmp/requirements.txt
# Pipecat splits sentences for TTS with NLTK punkt: fetch it at build time, not on the first call.
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    python -c "import nltk; nltk.download('punkt_tab', download_dir='/install/share/nltk_data', quiet=True)"

# ---- runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NLTK_DATA=/usr/local/share/nltk_data
# libgomp1 for onnxruntime (Silero VAD and the smart-turn model)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /install /usr/local

WORKDIR /app
COPY *.py ./

# Non-root; recordings live only in a private temp dir until they are uploaded (then deleted).
RUN useradd --system --uid 10001 --home-dir /app voicebot \
    && mkdir -p /tmp/voicebot-recordings \
    && chown voicebot /tmp/voicebot-recordings \
    && chmod 700 /tmp/voicebot-recordings
USER voicebot

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"

CMD ["python", "-u", "main.py"]
