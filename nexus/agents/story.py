"""Writing-room agents: showrunner, screenwriter, storyboard artist."""
from __future__ import annotations

import json

from nexus.agents.base import Agent
from nexus.agents.io import BibleDraft, EpisodeOutline, SceneGrid, SceneScript
from nexus.story.schemas import Episode, SeriesBible

_CRAFT_RULES = """
Craft rules you never break:
- Show, don't tell. If a line explains a feeling, cut it and stage the feeling instead.
- Every scene must change something. If the situation at the end equals the situation at
  the start, the scene does not exist.
- Dialogue is what people say to get something, not what they say to inform the audience.
  No character explains what another character already knows.
- Subtext over statement. The most important thing in a scene is usually the thing nobody says.
- Enter late, leave early. Start scenes after the small talk, cut before the resolution lands.
- Give every character one thing they want in every scene, and one reason they can't say it.
"""


class ShowrunnerAgent(Agent[BibleDraft]):
    """Builds the show bible: cast, world, look and sound.

    This is the highest-leverage call in the system. Everything downstream —
    every prompt, every portrait, every voice — is derived from what it decides,
    so it gets the strongest model in the profile.
    """

    role = "showrunner"
    output_model = BibleDraft
    max_tokens = 20000
    effort = "high"
    system_prompt = f"""You are the showrunner and creator of a serialised drama. You are building \
the show bible that an entire production will work from for the rest of the series.

Your cast must be small and sharp: 3 to 5 characters carry a 10-15 minute episode, no more. \
Every character needs a want, a wound and a way of speaking that is theirs alone.

The appearance block is not flavour text — it is a production specification. It will be pasted \
verbatim into every image and video prompt for the entire series, so it must be:
- Concrete and visual. "A nose broken once and set badly" is usable; "handsome" is not.
- Stable. Describe what does not change: bone structure, eye colour, hair, height, build, marks.
- Free of mood, action, lighting and camera language. Those change shot to shot; this does not.
- Specific enough that two different artists would draw recognisably the same person.

Signature wardrobe is the outfit the character is in for most of the episode. Wardrobe variants \
cover states the story forces on them (soaked, bloodied, formal, disguised).

Locations get the same treatment: a handful of places, described so precisely that every shot \
set there matches.
{_CRAFT_RULES}
Write the drama you would actually want to watch. Avoid the obvious version of the premise."""

    async def create(self, brief: str, *, target_minutes: float, language: str = "en",
                     content_rating: str = "PG-13") -> BibleDraft:
        prompt = f"""Create the show bible for this drama.

CREATOR'S BRIEF
{brief}

CONSTRAINTS
- Episodes run {target_minutes:.0f} minutes and are produced entirely with generative video,
  so favour intimate, performance-driven scenes over crowds, vehicles and large-scale action.
- Content rating: {content_rating}. Language: {language}.
- 3 to 5 named characters. 3 to 6 recurring locations.
- The visual style block is a contract every shot must satisfy — commit to a real look.

Return the complete bible."""
        return await self.run(prompt, label="show-bible")


class ScreenwriterAgent(Agent[EpisodeOutline]):
    """Turns the bible into an episode: act structure and beat sheet."""

    role = "screenwriter"
    output_model = EpisodeOutline
    max_tokens = 16000
    effort = "high"
    system_prompt = f"""You are the screenwriter breaking a single episode of an established drama.

You work in beats. A beat is one unit of change: someone wants something, tries something, and \
the situation is different afterwards. Your beat sheet is the spine the whole episode hangs on.

Structure a {{minutes}}-minute episode as:
- A cold open that starts mid-problem and earns the next 30 seconds.
- Act I: the situation destabilises and a decision becomes unavoidable.
- Act II: the attempt to fix it makes it worse; the midpoint reverses what the audience assumed.
- Act III: the cost lands, and the last beat opens a door instead of closing one.

Escalate tension across the beat sheet — not monotonically, but with the low points making the \
high points hurt more. Assign each beat a target duration; they must sum to roughly the episode
length.
{_CRAFT_RULES}"""

    def build_system(self, minutes: float = 12.0) -> str:  # type: ignore[override]
        return self.system_prompt.replace("{minutes}", f"{minutes:.0f}")

    async def outline(self, bible: SeriesBible, *, episode_number: int, premise: str,
                      target_seconds: float, previous_recap: str = "") -> EpisodeOutline:
        prompt = f"""SHOW: {bible.title}
LOGLINE: {bible.logline}
TONE: {bible.tone}
THEMES: {', '.join(bible.themes)}
SETTING: {bible.setting}
SERIES ARC: {bible.series_arc}

CAST
{bible.cast_block()}

LOCATIONS
{chr(10).join(f"- {loc.id}: {loc.name} ({loc.interior_exterior}) — {loc.description}" for loc in bible.locations)}

{f'PREVIOUSLY: {previous_recap}' if previous_recap else ''}

Break episode {episode_number}. Target runtime: {target_seconds:.0f} seconds \
({target_seconds / 60:.1f} minutes).

EPISODE PREMISE
{premise or 'Choose the strongest next episode for this series.'}

Give me the act structure and a beat sheet whose target_seconds sum to about {target_seconds:.0f}."""
        return await self.run(prompt, system=self.build_system(target_seconds / 60),
                              label=f"outline-ep{episode_number}")


class SceneBreakdownAgent(Agent[SceneGrid]):
    """Converts the beat sheet into a scene grid with real durations."""

    role = "screenwriter"
    output_model = SceneGrid
    max_tokens = 16000
    system_prompt = f"""You are the screenwriter converting a beat sheet into a scene grid.

Rules for the grid:
- One scene = one location, one continuous slice of time. If the location or the time jumps,
  it is a new scene.
- A {{minutes}}-minute episode wants 10 to 18 scenes. Scenes run 25-75 seconds; a scene over
  90 seconds is two scenes wearing a coat.
- Vary the rhythm deliberately: a long pressured two-hander, then a 20-second hinge scene.
- Reuse locations. Every new location costs the production; a returning location that has
  changed is worth more dramatically anyway.
- Keep scenes to 2-3 characters. Generative video cannot stage a crowd convincingly.
- target_seconds across all scenes must sum to within 10% of the episode target.
{_CRAFT_RULES}"""

    def build_system(self, minutes: float = 12.0) -> str:  # type: ignore[override]
        return self.system_prompt.replace("{minutes}", f"{minutes:.0f}")

    async def breakdown(self, bible: SeriesBible, outline: EpisodeOutline,
                        target_seconds: float) -> SceneGrid:
        beats = "\n".join(
            f"{b.index}. [Act {b.act_number}, ~{b.target_seconds:.0f}s, tension {b.tension}/10] "
            f"{b.title} — {b.description}"
            for b in outline.beats
        )
        prompt = f"""SHOW: {bible.title} — {bible.tone}
EPISODE: {outline.title}
LOGLINE: {outline.logline}
COLD OPEN: {outline.cold_open}
CLIFFHANGER: {outline.cliffhanger}

CAST (use these exact ids)
{chr(10).join(f"- {c.id}: {c.name}, {c.role}" for c in bible.characters)}

LOCATIONS (use these exact ids)
{chr(10).join(f"- {loc.id}: {loc.name} ({loc.interior_exterior})" for loc in bible.locations)}

BEAT SHEET
{beats}

Lay out the scene grid for a {target_seconds:.0f}-second episode. Number scenes from 1."""
        return await self.run(prompt, system=self.build_system(target_seconds / 60),
                              label="scene-grid")


class StoryboardAgent(Agent[SceneScript]):
    """Breaks one scene into shots and writes its dialogue.

    Per-scene rather than per-episode: the model keeps full attention on the
    continuity that matters inside a scene, and a failure costs one retry
    instead of the whole script.
    """

    role = "storyboard"
    output_model = SceneScript
    max_tokens = 12000
    system_prompt = f"""You are the director and storyboard artist breaking a single scene into shots.

Each shot is one continuous camera take of 3-9 seconds — that is the hard limit of the video
models this production uses. Plan inside that limit, not around it.

Coverage discipline:
- Open a scene with a shot that establishes geography, then move in as pressure rises.
- Cut on action or on a change in who holds power in the scene, never on a fixed rhythm.
- A line of dialogue lives in the shot where its delivery matters. Reaction beats deserve
  their own shot; that is where drama actually lands.
- Vary shot size between adjacent shots. Two identical mediums in a row is a mistake.
- One camera move per shot at most. Static shots are a choice, and often the right one.
- Keep 1-2 people in frame. Never write a shot needing precise hand work, readable text,
  driving, or more than two speaking characters.

The `action` field is what the camera sees, present tense, purely visual. No interior
monologue, no character appearance (that is injected automatically), no camera terms.

Dialogue must be sayable out loud in the shot's duration: roughly 2.6 words per second.
A 6-second shot holds about 15 words of speech, less if you want silence in it — and you
usually do.
{_CRAFT_RULES}"""

    async def board(self, bible: SeriesBible, scene, previous_scene_tail: str = "",
                    continuity: str = "") -> SceneScript:
        present = [c for c in bible.characters if c.id in (scene.characters_present or [])]
        location = bible.location(scene.location_id)
        prompt = f"""SHOW: {bible.title} — {bible.tone}

SCENE {scene.index}: {scene.slug_line}
Function: {scene.dramatic_function}
Synopsis: {scene.synopsis}
Target duration: {scene.target_seconds:.0f} seconds

CHARACTERS IN THIS SCENE (use these exact ids in subject_ids and dialogue)
{chr(10).join(f"- {c.id} ({c.name}): wants {c.motivation}. Speaks: {c.speech_style}" for c in present) or '- none'}

LOCATION
{location.name if location else scene.location_id}: {location.description if location else ''}
{f'Lighting: {location.lighting_signature}' if location else ''}
{f'Props available: {", ".join(location.props)}' if location and location.props else ''}

{f'CONTINUITY CARRIED IN: {continuity}' if continuity else ''}
{f'PREVIOUS SCENE ENDED: {previous_scene_tail}' if previous_scene_tail else ''}

Break this scene into shots totalling about {scene.target_seconds:.0f} seconds. \
Write the dialogue as you go."""
        return await self.run(prompt, label=f"storyboard-s{scene.index}")


def recap_from_episode(episode: Episode) -> str:
    """A short 'previously on' used to seed the next episode of a series."""
    beats = [b.description for b in episode.beats[-4:]]
    return " ".join(beats) + (f" It ended on: {episode.cliffhanger}" if episode.cliffhanger else "")


def bible_context_json(bible: SeriesBible) -> str:
    return json.dumps(
        {
            "title": bible.title, "tone": bible.tone, "themes": bible.themes,
            "characters": [{"id": c.id, "name": c.name, "role": c.role} for c in bible.characters],
            "locations": [{"id": loc.id, "name": loc.name} for loc in bible.locations],
        },
        separators=(",", ":"),
    )
