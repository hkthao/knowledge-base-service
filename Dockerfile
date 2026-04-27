FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY kb_indexer ./kb_indexer
COPY scripts ./scripts

EXPOSE 8000
CMD ["uvicorn", "kb_indexer.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
