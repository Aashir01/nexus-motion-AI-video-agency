"""Typed inputs/outputs for every agent call."""
from __future__ import annotations

from pydantic import BaseModel, Field

from nexus.story.schemas import (
    Act,
    AudioStyle,
    CameraMove,
    CharacterAppearance,
    ShotSize,
    StoryBeat,
    TimeOfDay,
    TransitionType,
    VisualStyle,
)


class CharacterDraft(BaseModel):
    id: str = Field(..., description="lower_snake_case slug, e.g. 'maya_rios'")
    name: str
    role: str = Field(..., description="protagonist | antagonist | deuteragonist | supporting | minor")
    one_line: str
    backstory: str
    motivation: str
    flaw: str
    arc: str = Field(..., description="Where they start this episode and where they end it")
    speech_style: str
    appearance: CharacterAppearance
    voice_gender: str = Field("neutral", description="male | female | neutral")
    voice_age_bracket: str = Field("adult", description="young | adult | senior")
    voice_accent: str = ""
    voice_timbre: str


class LocationDraft(BaseModel):
    id: str
    name: str
    description: str
    interior_exterior: str = Field("INT", description="INT | EXT | INT/EXT")
    architecture: str
    palette: str
    lighting_signature: str
    props: list[str] = Field(default_factory=list)
    ambience_sound: str


class BibleDraft(BaseModel):
    """The show bible. Written once, reused by every episode."""

    title: str
    logline: str
    genre: str
    tone: str
    themes: list[str] = Field(..., min_length=2, max_length=5)
    setting: str
    series_arc: str
    characters: list[CharacterDraft] = Field(..., min_length=2, max_length=7)
    locations: list[LocationDraft] = Field(..., min_length=2, max_length=8)
    visual_style: VisualStyle
    audio_style: AudioStyle


class EpisodeOutline(BaseModel):
    title: str
    logline: str
    premise: str
    cold_open: str = Field(..., description="The first 20 seconds, before the title")
    cliffhanger: str = Field(..., description="The final beat that makes them watch the next one")
    acts: list[Act] = Field(..., min_length=3, max_length=5)
    beats: list[StoryBeat] = Field(..., min_length=8, max_length=18)


class SceneDraft(BaseModel):
    index: int
    act_number: int
    slug_line: str = Field(..., description="INT. LOCATION NAME - NIGHT")
    location_id: str
    time_of_day: TimeOfDay
    synopsis: str
    dramatic_function: str
    characters_present: list[str] = Field(default_factory=list)
    target_seconds: float = Field(..., ge=10.0, le=180.0)


class SceneGrid(BaseModel):
    scenes: list[SceneDraft] = Field(..., min_length=6, max_length=30)


class DialogueDraft(BaseModel):
    character_id: str
    text: str
    delivery: str = ""
    emotion: str = "neutral"


class ShotDraft(BaseModel):
    shot_size: ShotSize
    camera_move: CameraMove
    transition_in: TransitionType = TransitionType.CUT
    subject_ids: list[str] = Field(default_factory=list)
    action: str = Field(..., description="Present tense, purely visual — what the camera sees")
    emotional_beat: str = ""
    target_seconds: float = Field(6.0, ge=2.0, le=12.0)
    dialogue: list[DialogueDraft] = Field(default_factory=list)
    sfx_prompt: str = ""


class SceneScript(BaseModel):
    """A single scene broken into shots. One call per scene keeps the model's
    attention on continuity within the scene, where it matters most."""

    shots: list[ShotDraft] = Field(..., min_length=2, max_length=12)
    continuity_out_notes: str = Field(
        "", description="What changed by the end of this scene: injuries, wardrobe, props, knowledge"
    )


class ShotPromptDraft(BaseModel):
    shot_index: int
    staging: str = Field(
        ..., description="Composition, blocking and lighting for the still keyframe — no character "
                         "descriptions, those are injected automatically"
    )
    motion: str = Field(..., description="What moves during the clip, and how the camera moves with it")
    sfx_prompt: str = ""


class ScenePromptSheet(BaseModel):
    prompts: list[ShotPromptDraft] = Field(..., min_length=1, max_length=12)


class VoiceAssignment(BaseModel):
    character_id: str
    voice_name: str = Field(..., description="Provider voice id, chosen from the offered list")
    default_pace: float = Field(1.0, ge=0.7, le=1.3)
    default_stability: float = Field(0.5, ge=0.0, le=1.0)
    rationale: str = ""


class CastingSheet(BaseModel):
    assignments: list[VoiceAssignment]


class ShotCritique(BaseModel):
    shot_index: int
    identity_consistency: int = Field(..., ge=1, le=10)
    staging_quality: int = Field(..., ge=1, le=10)
    continuity_ok: bool = True
    notes: str = ""
    should_regenerate: bool = False


class EpisodeCritique(BaseModel):
    narrative_coherence: int = Field(..., ge=1, le=10)
    character_consistency: int = Field(..., ge=1, le=10)
    pacing: int = Field(..., ge=1, le=10)
    dialogue_quality: int = Field(..., ge=1, le=10)
    strengths: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    shot_flags: list[ShotCritique] = Field(default_factory=list)
    verdict: str = Field(..., description="ship | revise | reject")


class ContinuityAudit(BaseModel):
    violations: list[str] = Field(default_factory=list)
    corrected_wardrobe: dict[str, str] = Field(default_factory=dict)
    corrected_conditions: dict[str, str] = Field(default_factory=dict)
    props_in_play: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
