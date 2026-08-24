"""Casting, continuity and critic agents."""
from __future__ import annotations

from nexus.agents.base import Agent
from nexus.agents.io import CastingSheet, ContinuityAudit, EpisodeCritique
from nexus.story.schemas import ContinuityState, Episode, Scene, SeriesBible, TimeOfDay


class CastingAgent(Agent[CastingSheet]):
    """Assigns a voice to each character from whatever the active TTS provider offers."""

    role = "casting"
    output_model = CastingSheet
    max_tokens = 4000
    system_prompt = """You are the casting director assigning voices.

Match voice to character, not to demographics: a soft voice on a dangerous person is more
interesting than the obvious pairing. Two characters who share scenes must not sound alike —
the audience has to tell them apart with their eyes closed.

Pace: 0.9 for a heavy, deliberate speaker; 1.0 default; 1.1 for someone who talks fast because
they are nervous or clever. Stability: low (0.3) for volatile characters, high (0.7) for
controlled ones."""

    async def cast(self, bible: SeriesBible, available_voices: list[str]) -> CastingSheet:
        cast = "\n".join(
            f"- {c.id} ({c.name}, {c.role}): {c.one_line} "
            f"Voice brief: {c.voice.gender}, {c.voice.age_bracket}, {c.voice.timbre}. "
            f"Speech: {c.speech_style}"
            for c in bible.characters
        )
        prompt = f"""SHOW: {bible.title} — {bible.tone}

CAST
{cast}

AVAILABLE VOICE IDS (choose only from this list; each character gets a different one)
{chr(10).join(f"- {v}" for v in available_voices)}

Assign a voice to every character id above."""
        return await self.run(prompt, label="casting")


class ContinuityAgent(Agent[ContinuityAudit]):
    """Audits one scene against the state it inherited and reports what changed."""

    role = "continuity"
    output_model = ContinuityAudit
    max_tokens = 4000
    system_prompt = """You are the continuity supervisor.

You track the physical truth of the production across scenes: what each character is wearing,
what has happened to their body, what objects are in play, what time it is in the story, and
what the audience has been told.

Report violations plainly — a character who arrives soaked and is dry two minutes later, a
weapon that vanishes, a wound that heals, a phone that was destroyed and is back. Then state
the corrected state as it stands at the END of this scene, so the next scene inherits truth."""

    async def audit(self, bible: SeriesBible, scene: Scene, state_in: ContinuityState,
                    scene_notes: str = "") -> ContinuityAudit:
        shots = "\n".join(
            f"  {s.scene_shot_index}. [{s.shot_size.value}] {s.action}"
            + ("".join(f"\n     {d.character_id}: \"{d.text}\"" for d in s.dialogue))
            for s in scene.shots
        )
        prompt = f"""STATE ENTERING THIS SCENE
Story clock: {state_in.story_clock or 'unspecified'}
Location: {state_in.location_id}
Time of day: {state_in.time_of_day.value}
Weather: {state_in.weather or 'unspecified'}
Wardrobe: {state_in.wardrobe or '{}'}
Condition: {state_in.character_condition or '{}'}
Props in play: {', '.join(state_in.props_in_play) or 'none'}
Open threads: {'; '.join(state_in.unresolved_threads) or 'none'}

SCENE {scene.index}: {scene.slug_line}
{scene.synopsis}

SHOTS
{shots}

{f'DIRECTOR NOTES: {scene_notes}' if scene_notes else ''}

DEFAULT WARDROBE (when nothing has changed it)
{chr(10).join(f"- {c.id}: {c.appearance.signature_wardrobe}" for c in bible.characters)}

Audit this scene and report the state at the end of it."""
        return await self.run(prompt, label=f"continuity-s{scene.index}")


class CriticAgent(Agent[EpisodeCritique]):
    """Reviews the finished cut and flags shots worth regenerating."""

    role = "critic"
    output_model = EpisodeCritique
    max_tokens = 8000
    system_prompt = """You are a demanding script editor and post supervisor reviewing a finished cut.

Judge what is actually on the page, not what was intended. Be specific: "scene 7 resolves a
conflict the audience never saw start" is useful, "pacing could be tighter" is not.

Flag a shot for regeneration only when there is a concrete defect: the wrong character in
frame, a continuity break, dialogue that cannot be spoken in the shot's duration, or staging
that contradicts the scene.

Verdict is 'ship' when the cut works, 'revise' when specific fixable shots hold it back, and
'reject' only when the structure itself is broken."""

    async def review(self, bible: SeriesBible, episode: Episode) -> EpisodeCritique:
        lines = []
        for scene in episode.scenes:
            lines.append(f"\nSCENE {scene.index} — {scene.slug_line} ({scene.planned_seconds():.0f}s)")
            for shot in scene.shots:
                lines.append(
                    f"  #{shot.index} [{shot.shot_size.value}/{shot.camera_move.value}/"
                    f"{shot.planned_seconds():.1f}s] {shot.action}"
                )
                for d in shot.dialogue:
                    lines.append(f"      {d.character_id}: \"{d.text}\"")
        prompt = f"""SHOW: {bible.title} — {bible.tone}
EPISODE: {episode.title} ({episode.planned_seconds():.0f}s over {episode.shot_count()} shots)
LOGLINE: {episode.logline}
CLIFFHANGER: {episode.cliffhanger}

CAST
{chr(10).join(f"- {c.id}: {c.name} — wants {c.motivation}; flaw: {c.flaw}" for c in bible.characters)}

THE CUT
{chr(10).join(lines)}

Review it."""
        return await self.run(prompt, label="episode-critique", max_tokens=8000)


def apply_audit(state: ContinuityState, audit: ContinuityAudit, scene: Scene) -> ContinuityState:
    """Fold a continuity audit into the running world state."""
    nxt = state.fork()
    nxt.location_id = scene.location_id
    nxt.time_of_day = scene.time_of_day if isinstance(scene.time_of_day, TimeOfDay) else state.time_of_day
    nxt.characters_present = list(scene.characters_present or [])
    nxt.wardrobe.update({k: v for k, v in audit.corrected_wardrobe.items() if v})
    nxt.character_condition.update({k: v for k, v in audit.corrected_conditions.items() if v})
    if audit.props_in_play:
        nxt.props_in_play = audit.props_in_play[:12]
    if audit.unresolved_threads:
        nxt.unresolved_threads = audit.unresolved_threads[:12]
    if audit.violations:
        nxt.notes = "; ".join(audit.violations[:5])
    return nxt


def seed_continuity(bible: SeriesBible, first_scene: Scene | None) -> ContinuityState:
    """Initial world state: everyone in their signature wardrobe, nothing broken yet."""
    return ContinuityState(
        story_clock="Day 1, 00:00",
        location_id=first_scene.location_id if first_scene else "",
        time_of_day=first_scene.time_of_day if first_scene else TimeOfDay.MIDDAY,
        characters_present=list(first_scene.characters_present) if first_scene else [],
        wardrobe={c.id: c.appearance.signature_wardrobe for c in bible.characters},
        character_condition={c.id: "" for c in bible.characters},
    )
