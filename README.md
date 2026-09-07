# AI Job Search — web dashboard on Kubernetes

A web UI + API layered on top of [ai-job-search](https://github.com/MadsLorentzen/ai-job-search),
so you can scrape postings, evaluate fit, draft/review a CV + cover letter, track
applications through a pipeline, and see funnel analytics — from a browser instead
instead of the ai-job-search CLI. Built for a k3s/Rancher cluster.

**What it does**
- `frontend/` — a single-page dashboard (no build step, plain HTML/JS) with Board,
  Jobs list, Scrape, Analytics, and Profile views.
- `backend/` — a FastAPI service that stores jobs/documents in SQLite and calls the
  **Google Gemini API directly** (your own free API key) to reproduce what `/apply` and
  `/rank` do inside Claude Code: score a posting against your profile, then draft
  and critique a CV + cover letter.
- The backend's init container clones your fork of the repo and runs `bun install`
  for the portal-search CLIs, so `/api/scrape` can shell out to the same
  `jobindex-search`, `jobnet-search`, etc. tools the CLI workflow uses.

**What it deliberately doesn't do**: compile LaTeX PDFs. Drafts come back as
markdown, editable in the UI. If you want the polished LaTeX CV/cover letter,
copy the markdown into the existing `cv/` and `cover_letters/` templates and
compile locally, or extend `backend/app/gemini_client.py` — the repo's own
`.claude/skills/job-application-assistant/05-cv-templates.md` and `06-cover-letter-templates.md`
document the LaTeX conventions to follow.

## One-command deploy

```bash
cp setup.env.example setup.env
```

Edit `setup.env`. First decide `DEPLOY_MODE`:

- **`registry`** (default) — build, push to `REGISTRY`, cluster pulls from there.
  Works for any cluster/node count. Needs `docker login` to that registry.
- **`local`** — build locally and `docker save | k3s ctr images import` straight
  into k3s's containerd. No registry, no push, no GHCR account needed. Only
  makes sense for a **single-node** k3s box (the image only exists on whichever
  node you imported it to) — set `K3S_SSH_HOST` if that node isn't the machine
  running `setup.sh`, otherwise leave it blank.

Other values regardless of mode:

- `NAMESPACE`, `INGRESS_HOST` — cluster namespace and the hostname the
  dashboard will be served on (a real domain, or a `nip.io` address like
  `jobsearch.192.168.1.50.nip.io` for local testing)
- `GIT_REPO_URL` — your fork of `ai-job-search` (see below). Leave blank to
  skip repo sync entirely and paste your profile into the UI instead.
- `GIT_TOKEN` — only needed if that fork is private (a read-only fine-grained PAT)
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `BUILD_IMAGES` — `true` to build (and push/import) images this run, `false`
  to just re-apply manifests against images already in place

Then:

```bash
./setup.sh
```

This builds both images, pushes or imports them depending on `DEPLOY_MODE`,
renders `k8s/templates/*.yaml` into `k8s/rendered/` with your values filled in
(namespace, image refs, pull policy, ingress host, model, secret values),
applies them in order, and waits for both Deployments to roll out. Safe to
re-run any time you change `setup.env` or rebuild images (`BUILD_IMAGES=false`
for a fast manifest-only update). `setup.env` and `k8s/rendered/` are
gitignored; only `k8s/templates/` (no secrets) is meant to be committed.

In `registry` mode with a private registry, also add `imagePullSecrets` to the
backend/frontend Deployments in `k8s/templates/04-backend.yaml` and
`05-frontend.yaml` before running.

In `local` mode, `setup.sh` needs passwordless `sudo` on the k3s node (directly,
or via `K3S_SSH_HOST` over SSH with a key) to run `k3s ctr images import`.

To deploy via Rancher's UI instead of the CLI, run `./setup.sh` with
`BUILD_IMAGES=false` once images are in place, then **Cluster → Apps →
Import YAML** the contents of `k8s/rendered/`.

### Fork the repo (recommended)

Fork `ai-job-search` so `GIT_REPO_URL` points at your own copy, and run `/setup`
inside Claude Code locally at least once so `CLAUDE.md` has your real profile
instead of `[PLACEHOLDER]` tokens — the backend reads that file for fit
evaluation and drafting.

### Check status

```bash
kubectl -n ai-job-search get pods
kubectl -n ai-job-search logs deploy/backend -c sync-repo   # repo clone / bun install
kubectl -n ai-job-search logs deploy/backend                # API logs
```

## Use it

Open the ingress host in a browser. First visit: **Profile** tab to confirm your
CLAUDE.md loaded (or paste one), **Scrape** tab to pull in postings, then open a
job from **Jobs** or **Board** to evaluate fit and draft documents.

## Backups

Everything (jobs, drafted CV/cover letters, your profile) lives in one SQLite
file on the `ai-job-search-data` PVC. The backend runs as a single replica —
SQLite isn't safe for concurrent writers, so don't scale that Deployment past 1.

**From the dashboard (Backups tab)** — no kubectl needed:
- **Create backup now** — snapshots the live DB onto the same volume using
  SQLite's online backup API (safe to run while the app is in use).
- **Download** / **Delete** any stored snapshot.
- **Restore** a stored snapshot, or **upload a `.db` file** and restore
  directly from it — both ask for confirmation first since they overwrite
  everything currently in the dashboard.
- **Export all data (JSON)** — a human-readable dump for spot-checking or
  migrating data elsewhere. Not a restore mechanism; use the snapshots above
  for that.

**From the command line** — for pulling a copy onto your own machine, or if
you'd rather not expose restore controls in the UI at all:
- `./backup.sh` copies the live `jobsearch.db` file out of the running pod
  into `./backups/jobsearch-<timestamp>.db`.
- `./restore.sh backups/jobsearch-<timestamp>.db` copies a backup back into
  the pod and restarts the backend to pick it up. Asks for confirmation first.

Both scripts read `NAMESPACE` from `setup.env` if present, or default to
`ai-job-search`. Run `./backup.sh` on a cron/schedule on your own machine if
you want backups automatic — there's no in-cluster scheduling for it here,
and snapshots made from the UI stay on the same PVC as the live DB (fine for
undoing a mistake, not a substitute for an off-cluster copy).

## Notes / things to adjust for your setup

- **Model**: `GEMINI_MODEL` in `02-configmap.yaml` defaults to `gemini-2.5-flash`.
  Check [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) for current model names if you
  want a different one.
- **Storage**: two 1Gi PVCs (SQLite DB, repo checkout) using your cluster's default
  StorageClass. On bare k3s with local-path-provisioner that's fine for single-node;
  for multi-node, use a StorageClass that supports the pod's node, or switch to
  `ReadWriteMany` if you plan to scale replicas past 1 (this app assumes 1 backend
  replica — SQLite isn't safe for concurrent writers).
- **TLS**: not configured. Add a `tls:` block to the Ingress plus cert-manager, or
  put this behind Rancher's own ingress/cert setup, before exposing it outside your
  home network.
- **Costs**: every "Evaluate fit" and "Draft" click is a real Gemini API call
  (draft+review is two calls). Keep an eye on usage if you scrape a lot of postings.
