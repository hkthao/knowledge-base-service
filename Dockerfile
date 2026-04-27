# syntax=docker/dockerfile:1.7
#
# Multi-stage build cho kb-indexer.
#
#  - builder: build-essential + pip cache mount để compile wheels khi cần
#  - runtime: slim, chỉ giữ venv + tooling cần (ripgrep, git)
#
# Build:
#     docker compose build kb-indexer
#
# Cache key cho lần build sau: chỉ requirements.txt thay đổi mới reinstall;
# code thay đổi chỉ COPY lại layer cuối — vài giây.

# ── Stage 1: builder ─────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential cần cho wheel nào không có pre-built (vd: tree-sitter
# native build trên một số arch). Drop ở stage runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Tách deps vào venv để stage runtime copy gọn.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .

# Cài torch CPU-only TRƯỚC. Mặc định pip pull torch CUDA build (kéo
# theo 2.8GB nvidia/* + 600MB triton) — vô dụng với inference cross-
# encoder rerank trên CPU. Khi sentence-transformers cài sau thấy torch
# đã có, không reinstall — image gọn ~5GB → ~1.5GB.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip wheel && \
    pip install --index-url https://download.pytorch.org/whl/cpu \
        'torch>=2.0,<3.0'

# BuildKit cache mount — pip wheels giữ giữa các lần build.
# Lần đầu vẫn 3-4 phút; lần sau khi requirements.txt không đổi: vài giây.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# git cho change/detector + commit_extractor. ripgrep cho relinker.
# Cả hai có wheel binary trong apt — nhỏ và stable.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY kb_indexer ./kb_indexer
COPY scripts ./scripts

# Non-root cho production hardening.
RUN useradd -r -u 1001 -d /app kb && chown -R kb /app
USER kb

EXPOSE 8000
CMD ["uvicorn", "kb_indexer.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
