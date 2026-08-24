# Architecture

## Shape of the problem

A 12-minute drama is ~110–160 shots. That number drives every structural decision:

| Consequence | Response |
|---|---|
| A job runs 20–60 minutes | Durable queue, worker processes, checkpoint after every stage |
| Any of 130 calls can fail | Per-call fallback chains and circuit breakers, not job-level retry |
| Cost scales with shot count | Budget enforced *before* each call, not reconciled afterwards |
| Progress must be legible | Weighted stage model + SSE, so the bar tracks wall-clock, not step count |
| Identity must not drift | Invariant prompt assembly in code + image conditioning (see CONSISTENCY.md) |

## Layers

```
nexus/
  config.py          one Settings object, env-driven, no import-time I/O
  story/schemas.py   the domain: SeriesBible → Episode → Scene → Shot
  routing/           catalog · profiles · router · health · ledger
  providers/         one adapter per API + the offline engine
  agents/            schema-bound specialists + deterministic prompt assembly
  pipeline/          context · orchestrator · 14 stages
  render/            ffmpeg wrapper · assembly · subtitles · PNG/WAV synth
  db/  billing/  api/  worker/  storage/
```

Dependencies point one way: `api` and `worker` depend on `pipeline`, which depends on `agents` and `routing`, which depend on `providers` and `story`. Nothing below `pipeline` knows the SaaS exists — the whole engine runs standalone from the CLI.

## Domain model

`SeriesBible` is the durable artefact. It holds the cast (with appearance blocks, voices and reference images), locations, and the visual/audio style contract, and it is written **once per series**. Episode 2 reuses it, which is why it costs less and looks more consistent than episode 1.

`Shot` is the atomic unit — one model call, 3–9 seconds, carrying its own prompts, references, dialogue lines, produced assets, status, attempt count and cost. Shot-level state is what makes resume work.

## Model routing

`ModelSpec` declares capability and price. `RoutingProfile` maps a plan onto preferences and a tier ceiling. `Requirements` is the hard filter. `ModelRouter` composes them:

1. Filter the catalog by modality, capability, plan ceiling, credentials, and per-org allow/block lists.
2. Rank by profile preference → quality → tier cost → recent health → latency; simulation is always weighted last.
3. Estimate the call's cost and check the ledger *before* dispatch.
4. Execute; on a `ProviderError`, record the failure and try the next candidate.
5. On success, record cost and latency against the role.

If nothing matches, `Requirements.relaxed()` drops one soft constraint and the search repeats. Every relaxation is recorded on the decision, so a job can explain why it used a weaker model.

**Circuit breaker.** Three failures inside the health window opens a model's breaker with exponential backoff (capped at 15 minutes). An open breaker demotes rather than removes — if it is the only candidate left, it still gets tried.

**Cost ledger.** Per-job, with an optional hard budget. `BudgetExceededError` is raised before the call, not after.

## Provider adapters

Two base classes. `Provider` declares the five generation methods; unimplemented ones raise a non-retryable error so the router skips instantly. `HttpProvider` adds a shared `httpx` client, jittered exponential backoff, retry-after handling, and `poll_until_done` for the async job APIs (fal, Replicate, Runway).

One adapter (`OpenAICompatProvider`) covers ten providers that speak the OpenAI wire format. Anthropic gets its own adapter on the official SDK — streaming (long screenwriting turns need it), adaptive thinking with `output_config.effort`, prompt caching on the system block (the bible is resent on every agent call, so this is the biggest single cost lever), and server-side refusal fallbacks, because a drama legitimately writes conflict and a policy decline is a normal operating event rather than a bug.

The **offline engine** implements every modality locally and is registered like any other provider.

## Agents

An agent is one system prompt, one Pydantic output type, one job. The runtime:

- converts the model to a JSON Schema, flattened and stripped of keywords that trip strict validators (ten providers, ten levels of schema support);
- uses native structured output where available, instruction + recovery parsing where not;
- on a validation failure, runs **one repair pass** with the specific errors attached before failing.

The prompts are a real part of the product. The showrunner prompt specifies that the appearance block is a *production specification*, not flavour text — concrete, stable, free of mood and camera language — because that text is pasted into hundreds of downstream prompts. The storyboard prompt enforces the 3–9 second physical limit of video models and budgets dialogue at ~2.6 words/second.

## Pipeline

Fourteen stages, weighted by real wall-clock share so the progress bar is honest (shot generation is 32% of the weight; casting is 1%).

Every stage begins by inspecting the plan and doing only what is missing. That single property gives:

- **resume** — rerun the whole list, and completed stages no-op;
- **partial failure tolerance** — non-critical stages (continuity, casting, score, critique) log and continue;
- **safe retries** — a redelivered job cannot duplicate work.

Checkpoints are the serialised `ProductionPlan` written to object storage after each stage and every few shots.

Shot-level work fans out under a semaphore sized by plan tier. Storyboarding stays sequential — each scene needs the previous scene's tail for continuity.

## Rendering

Four passes:

1. **Conform** — each clip scaled/cropped to one resolution, fps and pixel format; retimed to the duration its dialogue actually needs (short clips hold on the last frame via `tpad` rather than speeding up the performance); dialogue laid on at computed offsets.
2. **Concat** — the concat demuxer, stream-copy, because pass 1 made every clip identical. Transitions are baked into pass 1 as fades; an xfade graph over 130 inputs is slow and fragile.
3. **Mix** — score laid under the cut and side-chain compressed against the dialogue bus, then `loudnorm` to broadcast level.
4. **Finish** — soft subtitles (`mov_text`) or burned-in, plus a thumbnail.

## SaaS layer

Multi-tenant by organisation. `owned_or_404` is the single scoped-read helper, so another tenant's row is indistinguishable from a missing one.

Credits use reserve → settle. The quote is held at submit; settlement charges actual spend and refunds the rest; a failed job refunds in full. The ledger is append-only and the cached balance always reconstructs from it — a test asserts exactly that.

Plan limits are enforced twice: at the API boundary for a good error message, and again in the worker because a job row can outlive a downgrade.

## Operations

- `/health` is cheap and dependency-free; `/ready` reports the true state of database, queue, ffmpeg, storage and providers.
- Structured JSON logs with request-id and job-id correlation via `contextvars`.
- `/capabilities` reports what this specific deployment can do right now.
- `/api/v1/models/routing/health` exposes circuit-breaker state, which is how you answer "why did my job use the cheap model".
- Workers stop accepting on SIGTERM and drain in-flight jobs.

## Deliberate omissions

- **No CrewAI/LangGraph.** In-process and conversational; this needs distributed, durable, schema-validated execution.
- **No MoviePy.** Collapses at this scale.
- **No Celery.** A Redis list plus a semaphore is ~200 lines, has no broker semantics to fight, and degrades to in-process for single-node deploys.
- **No ORM for the plan.** The production plan is a JSON document in object storage; only queryable metadata lives in Postgres.
