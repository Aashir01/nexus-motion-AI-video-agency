# Character consistency

The single hardest requirement in long-form generative video: the same person, recognisably, across 130 independently generated shots.

## Why the obvious approaches fail

**Seeds.** A fixed seed reproduces an *image* given an identical prompt. Change the prompt — which every shot does — and the seed guarantees nothing about the face. The v1 of this project carried a `seed_id` string between scenes; it did nothing.

**Describing the character in every prompt.** Necessary but not sufficient. Text alone underdetermines a face: "angular jaw, dark brown eyes, black cropped hair" describes thousands of different people, and the model picks a different one each call.

**Asking the LLM to repeat the description.** This is the trap. The moment a model is asked to restate an appearance, it paraphrases — "cropped black hair" becomes "short dark hair" becomes "close-cropped brown hair" over 40 scenes. Drift is guaranteed because paraphrase is what language models do.

## What actually works

Three mechanisms, layered.

### 1. Invariant text, assembled in code

`nexus/agents/visual.py` splits prompt content by who writes it:

- **Invariant** — the appearance block, the style contract, the location plate. Concatenated *verbatim* by `build_keyframe_prompt()`. No model touches this text, so it cannot drift.
- **Variant** — staging and motion for this specific shot. A model writes only this, and is explicitly forbidden from describing faces, wardrobe or naming characters.

The identity clause leads the prompt, because models weight the head of a prompt most heavily.

```python
def test_every_shot_in_a_scene_gets_the_same_identity_text():
    clauses = {build_keyframe_prompt(s, ...).split("STAGING")[0] for s in shots}
    assert len(clauses) == 1
```

### 2. Canonical reference images

Text underdetermines a face; an image does not. Each character gets a reference sheet generated once per series:

1. **Front portrait** — text-to-image from the appearance block. Neutral expression, flat studio light, plain background. Its only job is to be an unambiguous likeness anchor.
2. **Three-quarter, profile, full body** — each generated as an **edit of the front portrait**, not a fresh text-to-image. This is what makes the set look like one person photographed four times rather than four siblings.

Locations get the same treatment as plates. Both are stored on the series and reused by every later episode, which is why episode 2 is cheaper and more consistent than episode 1.

### 3. The conditioning chain

```
canonical portraits ──┐
                      ├──→ shot keyframe ──→ shot clip
location plate ───────┘    (image edit)      (image-to-video)
```

Each shot's keyframe is an **edit** conditioned on the portraits of whoever is in frame, plus the location plate. Each clip is **image-to-video anchored on that keyframe**.

The second step matters more than it looks. When a keyframe exists, `build_video_prompt()` deliberately **omits** the appearance text and instead says *"the people, wardrobe, set and lighting stay exactly as they appear in the source frame"* — re-describing a face over an image-to-video call fights the source frame and measurably degrades likeness. Only in the fallback case, where keyframe generation failed, does the full description come back.

## The mutable half: continuity

Identity is what must not change. Everything else must change *correctly*: wardrobe, injuries, props, time of day, what characters know.

`ContinuityState` carries that between scenes, and a continuity agent audits each scene against the state it inherited, reporting violations and the corrected end state. Because wardrobe lives in continuity and appearance lives in the character profile, a character can be soaked, bloodied or in a disguise while her bone structure stays fixed:

```python
def test_continuity_overrides_wardrobe_but_not_the_face():
    clause = identity_clause([character], soaked_state)
    assert "soaked through" in clause
    assert "angular jaw" in clause      # appearance survives a wardrobe change
```

## Model selection

Consistency is a *capability*, so the catalog declares it and the router filters on it. `supports_reference_images` and `max_reference_images` are first-class fields, and when constraints have to be relaxed to find any working model, reference support is the **last** thing surrendered — after native audio, after first/last-frame, after quality floor:

```python
for attr, floor in (
    ("needs_native_audio", False),
    ("needs_first_last_frame", False),
    ("min_quality", 0),
    ("duration_s", None),
    ("needs_reference_images", False),   # last
):
```

A shot generated without identity conditioning is worse than a shot generated at lower quality, so the ordering encodes that.

## Where this comes from

- [Runway Gen-4](https://runway.com/research/introducing-runway-gen-4) — consistent characters and locations across scenes from a single reference image.
- Kling 3.0 multi-reference and Veo 3.1 "ingredients to video" — several reference images per generation; independent [comparisons](https://www.elser.ai/blog/best-ai-video-model-character-consistency-2026) put Kling ahead on facial identity and wardrobe retention across cuts.
- [MovieAgent](https://github.com/showlab/MovieAgent) (showlab) — multi-agent CoT planning over a script plus a **character bank**, reporting state-of-the-art character consistency for multi-scene film generation.
- Working microdrama pipelines — [invideo](https://invideo.io/blog/ai-micro-drama-script-to-episode/), [MinionArts](https://www.minionarts.com/blogs/24-hour-microdrama-same-day-production-pipeline), and the [open micro-drama generator](https://github.com/Anil-matcha/Open-AI-Micro-Drama-Generator) — all converge on: extract characters → generate reference portraits → condition frames on them → image-to-video. They also converge on **3–5 characters maximum**, which is why the showrunner prompt enforces it.

## Known limits

- Likeness degrades in extreme close-up on models with weak reference adherence. Prefer medium and medium-close for character-critical beats.
- Two characters in frame is the practical ceiling; three regularly produces face-swapping.
- Profile shots are the weakest angle across every model tested — the reference sheet includes one specifically to give the keyframe model something to work from.
- Wardrobe changes mid-scene are unreliable; the continuity agent tracks them, but the image models will sometimes revert to the signature outfit.
