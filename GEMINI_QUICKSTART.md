# Quickstart — Gemini edition (free tier, local k3s)

This is the full stack: deployment wrapper + scraper CLIs (`.agents/skills/`)
+ Gemini-backed LLM client, wired to deploy on a single-node k3s box with no
container registry and no GitHub required.

## 1. Prereqs on the node

```bash
sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker $USER && newgrp docker
```

## 2. Get a free Gemini key

https://aistudio.google.com/apikey — no credit card required.

## 3. Configure

```bash
cp setup.env.example setup.env
nano setup.env
```

Set at minimum:
```
DEPLOY_MODE=local
NAMESPACE=ai-job-search
INGRESS_HOST=jobsearch.local
GEMINI_API_KEY=your-free-gemini-key-here
GEMINI_MODEL=gemini-2.5-flash
```

For `GIT_REPO_URL`, see the commented options in `setup.env.example` — a
real GitHub URL, a local bare repo (`file:///local-repos/...`), or blank to
skip repo sync (Scrape tab unavailable, everything else works).

If you use the local bare repo option, set it up once **before** running
`setup.sh`:
```bash
mkdir -p /home/ubuntu/local-repos
git init --bare /home/ubuntu/local-repos/ai-job-search.git
git init . && git add . && git commit -m "initial" && git branch -M main
git remote add local /home/ubuntu/local-repos/ai-job-search.git
git push local main
sudo git config --global --add safe.directory /home/ubuntu/local-repos/ai-job-search.git
sudo git -C /home/ubuntu/local-repos/ai-job-search.git symbolic-ref HEAD refs/heads/main
```

## 4. Deploy

```bash
./setup.sh
```

## 5. Verify

```bash
kubectl -n ai-job-search get pods
kubectl -n ai-job-search logs deploy/backend -c sync-repo
kubectl -n ai-job-search logs deploy/backend -f
```

Open `http://<INGRESS_HOST>` (make sure it resolves to the node's IP).

## What's already fixed in this bundle vs. the original wrapper zip

- `backend/requirements.txt` — uses `google-genai` (the official Gemini SDK)
- `backend/app/gemini_client.py` (renamed from `claude_client.py`) — calls
  the Gemini API directly, same function signatures as the original,
  reads `GEMINI_API_KEY`/`GEMINI_MODEL`
  env vars (just repurposed to hold Gemini values, no manifest changes needed)
- `.agents/skills/*` — merged in from the upstream project source, so the
  Scrape tab's portal CLIs (jobindex, jobbank, jobdanmark, jobnet, linkedin,
  freehire) actually exist and `bun install` has something to install
- `backend/app/scraper.py` — fixed a per-portal CLI flag mismatch: the
  original code sent `--query` to every portal, but jobnet/jobbank/jobdanmark
  each use a different flag name (`--search-string`/`--key`/`--text`), and
  their CLIs hard-reject unknown flags. Also adds a `location` parameter
  (mapped per-portal to the right flag: `--region`/`--location`/
  `--municipality`/`--city`), enforces linkedin-search's required
  `--location`, and returns non-fatal `warnings` for filters a portal
  doesn't support instead of failing the whole search.
- `backend/app/main.py` — `/api/scrape` now accepts `location`; 
  `/api/jobs/{id}/evaluate` accepts an optional `experience_note` appended
  to the profile context for that one evaluation only.
- `frontend/index.html` — Location field on the Scrape tab (with a
  required-for-this-portal indicator for linkedin-search), an experience/
  context textarea on the job detail page above Evaluate fit, and
  client-side Download buttons for the drafted CV/cover letter (no backend
  round-trip - generates a `.md` file via a Blob).
- `k8s/templates/04-backend.yaml` — `sync-repo` initContainer now runs
  `git config --global --add safe.directory "*"` (fixes "dubious ownership"
  errors when cloning a mounted local repo), and a `local-repo` hostPath
  volume (`DirectoryOrCreate`, so it's a no-op if you don't use it) is
  mounted at `/local-repos` for the `file:///local-repos/...` option above
- `.github/workflows/ci.yml` — CI only, no deploy (see below)

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR to `main`:

- **backend** — installs `requirements.txt`, byte-compiles `backend/app`,
  does an import smoke test of the FastAPI app (catches wiring errors like
  a bad import or a broken route), and lints with `ruff`
- **cli-tests** — a matrix job across all 6 portal CLIs; each one runs its
  own `bun install`, `bun run typecheck`, and `bun test` (using the test
  suites already shipped under `.agents/skills/*/cli/tests/`)
- **docker-build** — builds both the backend and frontend Docker images
  (no push - just confirms the Dockerfiles and dependencies still resolve)
- **k8s-manifests** — renders `k8s/templates/*.yaml` with dummy placeholder
  values (mirroring `setup.sh`'s own render step) and validates the output
  is well-formed YAML with no leftover unfilled placeholders

There is intentionally **no CD/deploy job** - this repo deploys to a private,
single-node k3s box via `./setup.sh` run manually on that node. A
GitHub-hosted runner has no network path to that box, and the "local" deploy
mode only makes sense run directly on the node anyway.

To use it: push this repo to an actual GitHub remote (not the local bare
repo used for `k3s` sync) and the workflow runs automatically. It needs no
secrets - nothing in it touches your live Gemini key or
your cluster.

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` on every push/PR to `main`:

- **backend** — installs `requirements.txt`, byte-compiles `backend/app`,
  does an import smoke test, lints with `ruff`
- **cli-tests** — matrix across all 6 portal CLIs, each running its own
  `bun install` / `bun run typecheck` / `bun test`
- **k8s-manifests** — renders `k8s/templates/*.yaml` with dummy values and
  validates the resulting YAML

Then, **only on a push to `main`, only after every check above passes**:

- **build-and-push** — builds both Docker images and pushes them to GHCR
  (`ghcr.io/<you>/ai-job-search-backend` and `...-frontend`), tagged both
  `:latest` and `:<git-sha>`. Runs on a normal GitHub-hosted runner - no
  access to your cluster needed for this part.
- **update-deployment** — runs `kubectl set image` against your live
  `backend`/`frontend` Deployments, pointing them at the `:<git-sha>` tag
  just pushed (the immutable tag, not `:latest`, so it's unambiguous which
  build is running and safe to re-run), then waits for the rollout.

This workflow does **not** run `./setup.sh` and does not touch manifests,
secrets, or PVCs - it's scoped to exactly build → push → bump the running
image, nothing else. You still run `./setup.sh` yourself for anything that
changes the manifests themselves (a new env var, a new volume, etc.).

### One-time setup

**1. Switch to registry mode** in `setup.env` (see the comments in
`setup.env.example`):
```
DEPLOY_MODE=registry
REGISTRY=ghcr.io/yourusername
```
Run `./setup.sh` once by hand after this change, so the cluster's initial
Deployments exist and point at GHCR. After that, CI keeps them updated.

**2. Make the GHCR packages pullable by your cluster.** GHCR packages
inherit your repo's visibility by default. Simplest path for a personal
project: after the first push, go to your GitHub profile → **Packages** →
open each of `ai-job-search-backend` / `ai-job-search-frontend` → **Package
settings → Change visibility → Public**. No pull secret needed on the
cluster side.

If you'd rather keep them private, create a pull secret once instead:
```bash
kubectl -n ai-job-search create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=yourusername \
  --docker-password=<a GitHub PAT with read:packages scope> \
  --docker-email=you@example.com
```
then add to both `k8s/templates/04-backend.yaml` and `05-frontend.yaml`'s
pod spec, alongside `containers:`:
```yaml
      imagePullSecrets:
        - name: ghcr-pull
```

**3. Register a self-hosted runner** on the k3s node (needed only for the
`update-deployment` job's `kubectl` access - the build itself runs on
GitHub's own runners):
```bash
mkdir ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L <URL FROM GITHUB - repo → Settings → Actions → Runners → New self-hosted runner>
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/yourusername/ai-job-search --token <TOKEN FROM GITHUB>
sudo ./svc.sh install
sudo ./svc.sh start
```
Run this as whichever user already has a working `kubectl` context (usually
`ubuntu`) - the service inherits that user's permissions.

After that: every push to `main` that passes CI builds, pushes, and updates
your running pods automatically - no manual steps.

## Known limits

- `DEPLOY_MODE=local` and the local bare-repo option both assume a
  **single-node** k3s cluster — the node running `setup.sh` has to be the
  same node the pods actually schedule on.
- `linkedin-search` is the least reliable portal in practice (LinkedIn
  actively blocks scraping).
- `naukri-search` drives a real headless Chromium (Playwright) instead of a
  plain fetch like every other portal, because Naukri's public search API
  is bot-protected — see `.agents/skills/naukri-search/SKILL.md`. First pod
  startup after this is added downloads a ~150MB Chromium build (cached at
  `PLAYWRIGHT_BROWSERS_PATH` on the `/data` PVC after that, so it isn't
  re-fetched on every restart). Searches on this portal are also noticeably
  slower than the others (real browser navigation vs. a bare HTTP request)
  — the backend gives it a longer timeout (90s search / 45s detail) to
  account for this.
- Free Gemini tier is rate-limited (~1,500 req/day on `gemini-2.5-flash`) —
  plenty for testing, not for heavy production use.
