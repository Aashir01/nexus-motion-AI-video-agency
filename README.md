# Nexus Motion

**An agentic studio that turns one paragraph into a 10–15 minute drama with a cast that stays the same person from shot 1 to shot 130 — and a SaaS around it so other people can pay you to do it.**

```bash
make setup && make demo     # a full episode, no API keys, no network
```

---

## The two hard problems, and how this solves them

### 1. Length

A 12-minute episode is **~110–160 shots**. Every video model on the market caps out around 10 seconds, so the episode is not one generation — it is a hundred and thirty of them that have to agree with each other. The pipeline decomposes accordingly:

```
brief → show bible → episode outline → beat sheet → scene grid → shot list → shots
```

Each level is a separate, schema-validated model call. Durations are budgeted top-down and then **renormalised in code** at the scene grid, because language models cannot reliably make 14 numbers sum to 720. Shot length is then re-derived from the *actual* length of the synthesised dialogue, so the cut lands on its target runtime instead of 40% short.

### 2. Character consistency

This is where most attempts fall apart, and the fix is architectural rather than prompt-engineering. Prompt content is split in two:

| | Written by | Changes per shot? |
|---|---|---|
| **Identity** — appearance block, style contract, location plate | assembled **in code**, verbatim | never |
| **Staging & motion** — composition, blocking, what moves | a model, per shot | always |

No model is ever asked to remember what a character looks like. On top of the text, each character gets a **canonical reference sheet** generated once — a front portrait, then three-quarter/profile/full-body produced as *edits of that first image* — and those references are fed as image conditioning into every keyframe. Each shot then runs **image-to-video from its own keyframe**, so the clip inherits the face rather than re-inventing it.

```
appearance block ─┐
                  ├─→ canonical portraits ─→ shot keyframe ─→ image-to-video clip
location plate ───┘         (reused across every episode of the series)
```

That chain is what the field has converged on — [Runway Gen-4 references](https://runway.com/research/introducing-runway-gen-4), Kling multi-reference, Veo 3.1 "ingredients", [MovieAgent](https://github.com/showlab/MovieAgent)'s character bank — and `tests/test_consistency.py` asserts it directly: the identity clause must be byte-identical across every shot in a scene.

A **continuity ledger** carries the mutable half — wardrobe, injuries, props, story clock — scene to scene, so a character who gets soaked in scene 4 is still wet in scene 5 without her face changing.

---

## Models: flagship down to free, in one router

Every generation call goes through `ModelRouter`. It builds a candidate chain from the job's profile, then walks down it on failure, rate limit, or budget exhaustion.

```
premium job:  Kling 3.0 Pro → Runway Gen-4.5 → Seedance 2.5 → Kling 2.5 → Wan 2.5 → offline
```

**66 models across 7 modalities and 18 providers**, each declaring what it can actually do — reference-image support, first/last-frame, native audio, duration limits, real pricing:

| Modality | Flagship | Standard | Free |
|---|---|---|---|
| Writing | Claude Opus 5, Fable 5 | Sonnet 5, GPT-5.2, Gemini 3 Pro | Gemini 3 Flash, Groq Llama 4, OpenRouter free pool |
| Keyframes | Nano Banana Pro | Seedream 4, FLUX Kontext | HF FLUX, Pollinations |
| Shots | Veo 3.1, Seedance 2.5 | Kling 3.0/2.5, Runway Gen-4.5, Wan 2.5 | HF LTX |
| Voice | ElevenLabs v3 | Turbo v2.5, Cartesia Sonic 3, Kokoro | Edge neural TTS |
| Score | ElevenLabs Music | Stable Audio 2.5 | — |

Three properties make this more than a model list:

- **Capability-aware.** Asking for a 9-second shot with 3 reference images silently excludes every model that cannot do it, rather than failing at the API.
- **Graceful degradation with a floor.** Constraints are relaxed in a deliberate order — native audio first, identity conditioning *last*, because losing identity is what breaks a drama.
- **It always terminates.** The final rung of every chain is a built-in offline engine, so a production never dies because a vendor is down.

Prices drift; point `NEXUS_MODEL_PRICING_FILE` at a JSON file to correct any of them without a deploy.

### The offline engine

`simulation/*` implements every modality locally: schema-valid story documents, readable shot slates drawn with a dependency-free PNG writer, Ken Burns clips via ffmpeg, correctly-timed placeholder dialogue, a procedural score. An episode produced this way is a **watchable animatic** — it exercises every line of the assembly path, costs nothing, and runs in CI. It is why `make demo` works on a laptop with no accounts.

---

## The SaaS

| | Free | Starter $39 | Studio $149 | Scale $549 |
|---|---|---|---|---|
| Episode length | 3 min | 8 min | **15 min** | 20 min |
| Models | free tiers | budget → balanced | + premium | + flagship |
| Concurrent productions | 1 | 2 | 4 | 10 |
| Credits/mo | 300 | 4,000 | 18,000 | 75,000 |
| API access | — | ✓ | ✓ | ✓ |

- **Credits with reservations.** Every job is quoted, the quote is held against the balance, and settlement refunds whatever was not spent — a job that falls back to cheaper models, or dies at shot 3, is not billed for what it didn't do. The ledger is append-only and the cached balance is always reconstructible from it.
- **Multi-tenant by organisation.** Every scoped read goes through one helper that makes another tenant's row indistinguishable from a missing one.
- **Stripe** subscriptions, credit top-ups, and idempotent webhooks (`webhook_events` makes at-least-once delivery exactly-once).
- **Two auth paths:** JWT for the dashboard, API keys for programmatic use.
- **Plan limits are enforced twice** — at the API boundary and again in the worker, because a job row can outlive a downgrade.

---

## Architecture

```
                    ┌──────────────┐        ┌──────────────┐
   React studio ───▶│  FastAPI     │───────▶│  Postgres    │
   (SSE progress)   │  auth·quota  │        │  orgs·jobs   │
                    │  billing     │        │  credits     │
                    └──────┬───────┘        └──────────────┘
                           │ enqueue
                    ┌──────▼───────┐        ┌──────────────┐
                    │  Redis       │◀──────▶│  Workers ×N  │
                    │  queue + bus │        │  (the work)  │
                    └──────────────┘        └──────┬───────┘
                                                   │
              ┌────────────────────────────────────┼────────────────────┐
              │            Orchestrator — 14 idempotent stages          │
              │  bible · outline · scenes · shots · continuity · casting│
              │  references · prompts · keyframes · dialogue · clips    │
              │  score · assembly · review                              │
              └────────────────────┬───────────────────────────────────┘
                    ┌──────────────▼──────────────┐   ┌────────────────┐
                    │  ModelRouter (66 models)     │   │  ffmpeg render │
                    │  budget · health · fallback  │   │  mix · subs    │
                    └──────────────┬───────────────┘   └────────────────┘
                                   ▼
                       18 providers  ·  offline engine
```

**Every stage is idempotent and checkpointed.** A worker killed at shot 96 restarts at shot 96, not at the show bible. That is not a nicety at this scale — a flagship 15-minute episode is a 40-minute job, and losing it to a pod eviction is unacceptable.

Deliberate choices worth flagging:

- **No CrewAI / LangGraph.** Both are in-process and conversational; this needs durable, distributed, resumable execution with per-turn schema validation. The agent runtime here is ~150 lines and does exactly that, including a repair pass that feeds validation errors back to the model.
- **ffmpeg, not MoviePy.** A 130-shot, 12-minute cut is precisely where MoviePy's in-memory model collapses. Assembly is four inspectable passes: conform → concat (stream-copy) → score mix with side-chain ducking → subtitles + thumbnail.
- **Redis is optional.** No Redis means an in-process queue and bus, so one container is a complete deployment. Multi-worker deployments get Redis and nothing else changes.

---

## Quick start

```bash
cp .env.example .env
make setup
make doctor          # checks ffmpeg, database, queue, configured providers
make demo            # 2-minute episode, offline engine, zero cost

make dev             # API  → http://localhost:8000/docs
make worker          # in another shell
make ui              # dashboard → http://localhost:5173
```

Add any provider key to `.env` and the router picks it up on the next call — no code change, no restart of anything but the process.

```bash
python -m nexus.cli produce \
  --brief "A harbour-town drama about a fixer who has run out of favours." \
  --minutes 12 --profile premium

python -m nexus.cli routes --profile premium    # see the resolved fallback chains
python -m nexus.cli quote --minutes 12          # cost across all profiles
```

### Docker

```bash
make up                          # postgres + redis + api + 2 workers + migrations
docker compose up -d --scale worker=6   # scale the part that does the work
```

### API

```bash
curl -X POST localhost:8000/api/v1/productions \
  -H "X-API-Key: nmk_..." -H 'Content-Type: application/json' \
  -d '{"series_id":"srs_...","target_minutes":12,"profile":"premium"}'
```

Full reference at `/docs`. Progress streams over SSE at `/api/v1/productions/{id}/events`.

---

## What it costs to run

Measured against the pipeline's own shot maths (~6.5s per shot, one keyframe and one clip each):

| Profile | 12-min episode | Retail at 1.6× |
|---|---|---|
| Offline | $0.00 | — |
| Budget | ~$33 | $53 |
| Balanced | ~$60 | $96 |
| Premium | ~$159 | $255 |
| Flagship | ~$353 | $565 |

`CREDIT_MARKUP_MULTIPLIER` sets the margin. Set a `budget_usd` on any job and the router refuses calls that would exceed it rather than discovering the overrun on the invoice.

---

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — subsystem by subsystem, and why each is built the way it is
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — production checklist, scaling, storage, backups
- [`docs/CONSISTENCY.md`](docs/CONSISTENCY.md) — the character-consistency mechanism in detail, with the research it is based on

## Tests

```bash
make test        # 52 fast tests
make test-all    # + two end-to-end runs that render real video
```

The end-to-end test produces a complete episode with no API keys and asserts the output is a playable file whose duration matches what the pipeline planned. CI runs it on every push.

## Status

Production-shaped and deployment-ready: migrations, health/readiness probes, structured logs with request and job correlation, circuit breakers, graceful worker shutdown, container images, CI.

Not yet built: shot-level regeneration from the UI (the critic already flags which shots need it), webhook delivery to customers, and a real admin console.
