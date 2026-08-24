# Deployment

## What the platform actually needs

| | Required | Notes |
|---|---|---|
| **ffmpeg + ffprobe** | yes | the render engine; in the image already |
| **Postgres** | production | sqlite is fine for local and single-user |
| **Redis** | multi-worker | without it the queue is in-process (one container only) |
| **Object storage** | production | S3/R2/B2/MinIO; local disk works behind one node |
| **Provider keys** | no | with none, everything runs on the offline engine |

Sizing: workers are the expensive part and are mostly *waiting* on provider APIs, so they are I/O-bound except during assembly. 2 vCPU / 4 GB per worker handles 6 concurrent shots comfortably; give the assembly step headroom for ffmpeg.

## One command

```bash
cp .env.example .env
# set SECRET_KEY, DATABASE_URL, REDIS_URL, storage, provider keys
make up                                   # migrations, api, 2 workers, postgres, redis
docker compose up -d --scale worker=6     # scale the part that does the work
```

The API serves the built dashboard at `/app` and the docs at `/docs`, so a single container is a complete deployment.

## Production checklist

**Security**
- [ ] `SECRET_KEY` set to 32+ random bytes — the app refuses to start in production with the default
- [ ] `ENVIRONMENT=production` (suppresses internal error detail in responses)
- [ ] `CORS_ORIGINS` set to your real frontend origin, not `*`
- [ ] TLS terminated upstream; run uvicorn with `--proxy-headers` (the image does)
- [ ] Rate limiting at the edge — the app enforces plan concurrency, not request rate
- [ ] Provider keys in a secret manager, not the image

**Data**
- [ ] `DATABASE_URL` → managed Postgres with automated backups and PITR
- [ ] `alembic upgrade head` runs on deploy (the `migrate` compose service does this)
- [ ] `STORAGE_BACKEND=s3` with a lifecycle rule — a 12-minute episode with all
      intermediates is 1–3 GB, and intermediates are disposable after assembly
- [ ] `S3_PUBLIC_BASE_URL` pointed at a CDN so the API is not in the video path

**Reliability**
- [ ] More than one worker; they coordinate through Redis
- [ ] `terminationGracePeriodSeconds` ≥ 300 so workers drain rather than abandon jobs
- [ ] Liveness → `/health`, readiness → `/ready`
- [ ] Alert on: queue depth, job failure rate, `fallback_rate` from `/billing/usage`,
      and open circuit breakers at `/api/v1/models/routing/health`

**Billing**
- [ ] `BILLING_ENABLED=true`, Stripe keys and the three price IDs set
- [ ] Webhook endpoint → `POST /api/v1/billing/webhook`, subscribed to
      `checkout.session.completed`, `customer.subscription.*`, `invoice.paid`
- [ ] `CREDIT_MARKUP_MULTIPLIER` set to your margin
- [ ] Verify prices in the catalog against your providers' current rates, or point
      `NEXUS_MODEL_PRICING_FILE` at an override file

## Kubernetes sketch

The image is the same for both roles; only the command differs.

```yaml
# api
command: ["uvicorn","nexus.api.app:app","--host","0.0.0.0","--port","8000","--proxy-headers"]
readinessProbe: { httpGet: { path: /ready, port: 8000 } }
livenessProbe:  { httpGet: { path: /health, port: 8000 } }
resources: { requests: { cpu: 500m, memory: 512Mi } }
---
# worker
command: ["python","-m","nexus.worker.main"]
terminationGracePeriodSeconds: 600
resources: { requests: { cpu: 2, memory: 4Gi } }
# scale on Redis queue depth (KEDA / custom metric), not CPU — workers are I/O-bound
```

Run migrations as a Job or an initContainer, never from multiple replicas at once.

## Cost control

Three independent brakes:

1. **Plan ceilings** — profile, runtime, resolution and concurrency per tier.
2. **Per-job budget** — `budget_usd` on the request; the router refuses calls that would exceed it and the job finishes on whatever it has.
3. **Credit reservations** — a job cannot start unless the quote is covered, and the unused portion comes back on settlement.

Watch `fallback_rate` in `/api/v1/billing/usage`: a rising rate means your preferred models are failing and jobs are quietly degrading.

## Operating notes

**A job is stuck.** Check `/api/v1/models/routing/health` for open breakers, then the worker logs filtered by `job_id`. Cancellation is cooperative: `POST /productions/{id}/cancel` sets a flag the worker checks between shots.

**A worker died mid-episode.** Nothing is lost. Re-enqueue the job id; stages are idempotent and it resumes at the last checkpoint.

**Costs came in high.** `/api/v1/billing/usage` breaks spend down by model and by role. Shot generation is normally 70–85% of it; if writing is a large share, the profile is using a flagship model for prompt expansion — override `prompt_smith` to a cheaper model.

**Output quality dropped.** Almost always fallback: a premium video model was unavailable and the job ran on the next rung. `job.result.routes` records the exact chain each role used.
