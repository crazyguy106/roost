# =============================================================================
# Roost — Self-hosted AI Productivity Platform
# Multi-stage build with feature flags
# =============================================================================

FROM python:3.12-slim AS base

# Feature flags (default all off for minimal build)
ARG ENABLE_GOOGLE=false
ARG ENABLE_MICROSOFT=false
ARG ENABLE_AI=false
ARG ENABLE_TELEGRAM=false
ARG ENABLE_NOTION=false
ARG ENABLE_INFRA=false

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    openssh-server \
    curl \
    jq \
    git \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) + npm
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install ttyd for web terminal
RUN curl -fsSL -o /usr/local/bin/ttyd \
    https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
    && chmod +x /usr/local/bin/ttyd

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
RUN groupadd -g 1000 dev \
    && useradd -m -u 1000 -g dev -s /bin/bash dev \
    && mkdir -p /app/data /app/config \
    && chown -R dev:dev /app

# Configure sshd
RUN mkdir -p /run/sshd \
    && sed -i 's/#PermitUserEnvironment no/PermitUserEnvironment yes/' /etc/ssh/sshd_config \
    && sed -i 's/#Port 22/Port 22/' /etc/ssh/sshd_config

WORKDIR /app

# Install base Python dependencies
COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir -r requirements/base.txt

# Conditionally install feature dependencies
COPY requirements/ requirements/
RUN if [ "$ENABLE_GOOGLE" = "true" ] && [ -f requirements/google.txt ]; then \
        pip install --no-cache-dir -r requirements/google.txt; \
    fi
RUN if [ "$ENABLE_MICROSOFT" = "true" ] && [ -f requirements/microsoft.txt ]; then \
        pip install --no-cache-dir -r requirements/microsoft.txt; \
    fi
RUN if [ "$ENABLE_AI" = "true" ] && [ -f requirements/ai.txt ]; then \
        pip install --no-cache-dir -r requirements/ai.txt; \
    fi
RUN if [ "$ENABLE_TELEGRAM" = "true" ] && [ -f requirements/telegram.txt ]; then \
        pip install --no-cache-dir -r requirements/telegram.txt; \
    fi
RUN if [ "$ENABLE_NOTION" = "true" ] && [ -f requirements/notion.txt ]; then \
        pip install --no-cache-dir -r requirements/notion.txt; \
    fi
RUN if [ "$ENABLE_INFRA" = "true" ] && [ -f requirements/infra.txt ]; then \
        pip install --no-cache-dir -r requirements/infra.txt; \
    fi

# Copy source code
COPY roost/ roost/
COPY pyproject.toml setup.py setup.cfg* ./

# Editable install
RUN pip install --no-cache-dir -e .

# Copy entrypoint
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Fix ownership
RUN chown -R dev:dev /app

EXPOSE 8080 22

USER dev

ENTRYPOINT ["./entrypoint.sh"]
