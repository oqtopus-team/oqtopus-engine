FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Create appuser with configurable UID and GID to avoid permission issues
# You can override these values at build time like this:
#   docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
# This ensures that files created in volumes or bind mounts are owned by the correct host user
ARG UID=1000
ARG GID=1000

# Install the project into `/app`
WORKDIR /app

# Install tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    less \
    gcc \
    g++ \
    cargo \
    iptables \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Add user
RUN groupadd -g ${GID} appuser \
    && useradd -m -u ${UID} -g ${GID} -s /bin/bash -c "" appuser \
    && chown -R appuser:appuser /app
USER appuser

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=~/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Add modules to provide interfaces for SSE
# Copy only the minimum necessary files for security purposes
COPY src/ /app/src/
COPY pyproject.toml uv.lock /app
RUN --mount=type=cache,target=~/.cache/uv \
    uv sync --locked --no-dev

# Set environment variables
ENV PYTHONPATH=/app/src/sse_runtime/
ENV OQTOPUS_ENV=sse_container

# Add initialization script for security and other settings
COPY --chmod=100 init.sh /root/init.sh

# Isolate user's program to tmpfs
WORKDIR /sse

CMD ["uv", "run", "python"]
