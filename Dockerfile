# 采价台 Web 工作台容器镜像（受治理、人在环路的采购比价 agent）
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    AGENTHARNESS_NO_DOTENV=1

WORKDIR /app

# Dependencies + project (web_dist 由 uv_build 打包进 wheel，无需单独 COPY 构建产物)
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /data /workspace

EXPOSE 8741

# 容器内非回环绑定必须显式 --allow-remote-execution；数据与工作区建议挂载卷。
CMD ["uv", "run", "agentharness", "--host", "0.0.0.0", "--port", "8741", "--workspace", "/workspace", "--data-dir", "/data", "--allow-remote-execution", "--no-open"]
