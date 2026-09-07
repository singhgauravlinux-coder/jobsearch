FROM python:3.12-slim

# bun is needed to run the repo's portal-search CLIs (.agents/skills/*/cli)
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://bun.sh/install | bash
ENV PATH="/root/.bun/bin:${PATH}"

# naukri-search's CLI drives a real headless Chromium (Playwright) because
# Naukri's public search API is bot-protected - see
# .agents/skills/naukri-search/SKILL.md. Install Chromium's OS-level shared
# library dependencies here, at image-build time (needs apt + root), so the
# sync-repo initContainer - which reuses this image at pod startup - doesn't
# need either. The Chromium *binary* itself is downloaded later by that
# portal's own `bun install` and cached on a persistent volume (see
# PLAYWRIGHT_BROWSERS_PATH in the k8s manifests) so it isn't re-fetched on
# every pod restart.
RUN apt-get update \
    && bunx playwright@1.48.0 install-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_DIR=/data
ENV REPO_PATH=/repo
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
