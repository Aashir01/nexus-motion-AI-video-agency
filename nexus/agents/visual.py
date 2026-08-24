"""Visual agents and the prompt-assembly layer that enforces character identity.

The consistency mechanism
-------------------------
Nothing here trusts a language model to remember what a character looks like.
The pipeline separates two kinds of prompt content:

* **Invariant** — the character's appearance block, the show's style contract,
  the location plate description.  These are assembled *in code*, verbatim, for
  every single shot.  They cannot drift because no model rewrites them.
* **Variant** — staging, blocking, motion for this specific shot.  A model writes
  only this part.

On top of the text, every keyframe request carries the character's canonical
reference portraits as image conditioning, and every video request is anchored
to the keyframe we just produced.  That chain — portrait → keyframe → clip — is
what the research consensus (Runway Gen-4 references, Kling multi-ref, Veo
"ingredients") converges on, and it is why the same face survives 130 shots.
"""
from __future__ import annotations

from nexus.agents.base import Agent
from nexus.agents.io import ScenePromptSheet
from nexus.story.schemas import (
    CharacterProfile,
    ContinuityState,
    LocationProfile,
    Scene,
    SeriesBible,
    Shot,
    ShotSize,
)

_SHOT_SIZE_LANGUAGE = {
    ShotSize.EXTREME_WIDE: "extreme wide shot, subject small in a dominant environment",
    ShotSize.WIDE: "wide shot, full environment visible around the subject",
    ShotSize.FULL: "full shot, subject head to toe",
    ShotSize.MEDIUM_WIDE: "medium wide shot, subject from the knees up",
    ShotSize.MEDIUM: "medium shot, subject from the waist up",
    ShotSize.MEDIUM_CLOSE: "medium close-up, subject from the chest up",
    ShotSize.CLOSE_UP: "close-up, face fills the frame",
    ShotSize.EXTREME_CLOSE_UP: "extreme close-up on the eyes",
    ShotSize.OVER_THE_SHOULDER: "over-the-shoulder shot, foreground shoulder soft and out of focus",
    ShotSize.TWO_SHOT: "two shot, both subjects sharing the frame",
    ShotSize.INSERT: "insert shot, tight on a detail",
    ShotSize.POV: "point-of-view shot from the subject's eyeline",
}

_CAMERA_LANGUAGE = {
    "static": "locked-off camera, no movement",
    "pan_left": "slow pan left", "pan_right": "slow pan right",
    "tilt_up": "slow tilt up", "tilt_down": "slow tilt down",
    "dolly_in": "slow dolly in, pushing toward the subject",
    "dolly_out": "slow dolly out, pulling away from the subject",
    "tracking": "tracking shot moving with the subject",
    "crane": "crane move rising over the scene",
    "handheld": "handheld camera, subtle organic movement",
    "orbit": "slow orbit around the subject",
    "zoom_in": "slow zoom in", "zoom_out": "slow zoom out",
}


class PromptSmithAgent(Agent[ScenePromptSheet]):
    """Writes only the *variant* half of each shot prompt: staging and motion."""

    role = "prompt_smith"
    output_model = ScenePromptSheet
    max_tokens = 8000
    system_prompt = """You are the visual director writing generation prompts for a drama.

For each shot you write exactly two things:

STAGING — how the still frame is composed. Where the subject sits in the frame, what is in the
foreground and background, how the light falls, what the depth of field does, the physical
posture and expression. Be concrete and photographic.

MOTION — what moves during the clip and how the camera moves with it. One clear physical action
per shot. Motion must be plausible over 3-9 seconds: a head turn, a step, a hand set down, rain
falling, a door closing. Never write a sequence of events; that is a cut, not a shot.

Hard rules:
- NEVER describe a character's face, hair, build, age, ethnicity or clothing. That description
  is injected automatically from the show bible and yours would contradict it.
- NEVER name a character. Refer to them by role in the frame ("the subject", "the woman in the
  foreground") — the identity block handles naming.
- No text, signage, screens or written words in frame; generative video renders them as garbage.
- No hand-detail actions (writing, typing, threading a needle), no driving, no crowds.
- Keep each field under 60 words. Dense and specific beats long and vague."""

    async def write(self, bible: SeriesBible, scene: Scene) -> ScenePromptSheet:
        shots = "\n".join(
            f"{i}. [{s.shot_size.value} / {s.camera_move.value} / {s.planned_seconds():.1f}s] "
            f"{s.action}"
            + (f" | beat: {s.emotional_beat}" if s.emotional_beat else "")
            + (f" | speaking: {len(s.dialogue)} line(s)" if s.dialogue else " | no dialogue")
            for i, s in enumerate(scene.shots)
        )
        location = bible.location(scene.location_id)
        prompt = f"""SHOW LOOK: {bible.visual_style.to_prompt_fragment()}
ASPECT: {bible.visual_style.aspect_ratio}

SCENE {scene.index}: {scene.slug_line}
{scene.synopsis}

LOCATION: {location.to_prompt_fragment() if location else scene.location_id}

SHOTS (return one prompt entry per shot, shot_index matching the number)
{shots}

Write staging and motion for every shot."""
        return await self.run(prompt, label=f"prompts-s{scene.index}")


# ── Deterministic prompt assembly ─────────────────────────────────────────────


def identity_clause(characters: list[CharacterProfile], continuity: ContinuityState | None) -> str:
    """The invariant description of everyone in frame, plus their current state."""
    if not characters:
        return ""
    parts = []
    for c in characters:
        wardrobe = c.appearance.signature_wardrobe
        condition = ""
        if continuity:
            wardrobe = continuity.wardrobe.get(c.id) or wardrobe
            condition = continuity.character_condition.get(c.id, "")
        clause = f"{c.name}: {c.appearance.to_prompt_fragment()}; wearing {wardrobe}"
        if condition:
            clause += f"; currently {condition}"
        parts.append(clause)
    return " | ".join(parts)


def build_keyframe_prompt(
    shot: Shot,
    staging: str,
    bible: SeriesBible,
    location: LocationProfile | None,
    continuity: ContinuityState | None,
) -> str:
    """Assemble the still-frame prompt. Invariant blocks first — models weight
    the head of a prompt most heavily, and identity is what must not drift."""
    subjects = [c for c in (bible.character(cid) for cid in shot.subject_ids) if c]
    blocks = [
        _SHOT_SIZE_LANGUAGE.get(shot.shot_size, "medium shot"),
        f"CHARACTERS IN FRAME — {identity_clause(subjects, continuity)}" if subjects else "",
        f"LOCATION — {location.to_prompt_fragment()}" if location else "",
        f"STAGING — {staging}",
        f"MOOD — {shot.emotional_beat}" if shot.emotional_beat else "",
        f"STYLE — {bible.visual_style.to_prompt_fragment()}",
        f"CONTINUITY — {continuity.to_prompt_fragment()}" if continuity and continuity.to_prompt_fragment() else "",
        "single frame, no text or watermark anywhere in the image",
    ]
    return ". ".join(b for b in blocks if b).replace("..", ".")


def build_video_prompt(
    shot: Shot,
    motion: str,
    bible: SeriesBible,
    location: LocationProfile | None,
    continuity: ContinuityState | None,
    *,
    has_keyframe: bool,
) -> str:
    """Assemble the clip prompt.

    When a keyframe exists the model is doing image-to-video, so the frame
    already carries identity and set dressing — the prompt then only needs to
    describe movement, and repeating appearance actively degrades the result.
    When there is no keyframe we fall back to the full description.
    """
    camera = _CAMERA_LANGUAGE.get(shot.camera_move.value, "locked-off camera, no movement")
    if has_keyframe:
        blocks = [
            f"MOTION — {motion}",
            f"CAMERA — {camera}",
            "the people, wardrobe, set and lighting stay exactly as they appear in the "
            "source frame; only the described motion changes",
            "natural human motion, consistent physics, no morphing faces, no jump cuts",
        ]
    else:
        subjects = [c for c in (bible.character(cid) for cid in shot.subject_ids) if c]
        blocks = [
            _SHOT_SIZE_LANGUAGE.get(shot.shot_size, "medium shot"),
            f"CHARACTERS — {identity_clause(subjects, continuity)}" if subjects else "",
            f"LOCATION — {location.to_prompt_fragment()}" if location else "",
            f"MOTION — {motion}",
            f"CAMERA — {camera}",
            f"STYLE — {bible.visual_style.to_prompt_fragment()}",
            "single continuous take, no cuts, no text or watermark",
        ]
    return ". ".join(b for b in blocks if b)


def build_portrait_prompt(character: CharacterProfile, bible: SeriesBible, kind: str) -> str:
    """The canonical reference sheet. Neutral light, neutral pose — a plate whose
    only job is to be an unambiguous likeness anchor for every later shot."""
    framing = {
        "portrait_front": "head-and-shoulders portrait, facing camera directly, neutral expression",
        "portrait_three_quarter": "head-and-shoulders portrait, three-quarter angle, neutral expression",
        "portrait_profile": "head-and-shoulders portrait, full profile view, neutral expression",
        "full_body": "full-body standing portrait, arms relaxed at sides, facing camera",
    }.get(kind, "head-and-shoulders portrait, facing camera")

    return (
        f"Character reference sheet. {framing}. "
        f"{character.name}: {character.appearance.to_prompt_fragment()}. "
        f"Wearing {character.appearance.signature_wardrobe}. "
        f"Clean even studio lighting, plain neutral mid-grey background, sharp focus on the face, "
        f"{bible.visual_style.look}, photorealistic skin texture. "
        f"No props, no text, no watermark, no stylisation."
    )


def build_wardrobe_variant_prompt(character: CharacterProfile, bible: SeriesBible,
                                  label: str, description: str) -> str:
    return (
        f"The same person, unchanged face and build. {character.name}: "
        f"{character.appearance.to_prompt_fragment()}. "
        f"Now wearing {description}. Full-body standing portrait, neutral pose, "
        f"clean studio lighting, plain neutral background, {bible.visual_style.look}. "
        f"Keep the face identical to the reference. No text, no watermark."
    )


def build_location_plate_prompt(location: LocationProfile, bible: SeriesBible) -> str:
    return (
        f"Establishing plate of a film location, no people in frame. "
        f"{location.interior_exterior}. {location.name}: {location.description}. "
        f"{location.architecture}. Colour palette: {location.palette}. "
        f"Lighting: {location.lighting_signature}. "
        f"{bible.visual_style.to_prompt_fragment()}. "
        f"Wide establishing composition, no text, no watermark."
    )
