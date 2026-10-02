FROM --platform=linux/amd64 public.ecr.aws/docker/library/python:3.12-slim

# System dependencies for PaddleOCR (OpenCV, libGL, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        fontconfig \
        fonts-noto-cjk \
        fonts-nanum && \
    fc-cache -f && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Lambda only has /tmp writable
ENV HOME=/tmp
# Skip model hoster connectivity check
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# Lambda Runtime Interface Client
RUN pip install --no-cache-dir awslambdaric

WORKDIR /var/task

COPY preprocessing/ocr-lambda-processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared modules
COPY shared/ ./shared/

# Handler
COPY preprocessing/ocr-lambda-processor/index.py .

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["index.handler"]
