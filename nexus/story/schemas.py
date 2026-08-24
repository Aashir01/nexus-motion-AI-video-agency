"""Domain model for long-form episodic drama.

The hierarchy is deliberate.  A 10-15 minute episode is far too much to ask any
model for in one shot, so the story is decomposed:

    SeriesBible → Episode → Act → Scene → Shot → (video clip + dialogue lines)

Character consistency is carried by `CharacterProfile.reference_images`: canonical
portraits generated once and then fed back as image conditioning for every single
keyframe.  `ContinuityState` tracks the mutable half (wardrobe, injuries, props,
emotional beat) so shot N+1 knows what shot N left behind.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

# ── Vocabulary ──────────────────────────────────────────────────────────────


class ShotSize(str, Enum):
    EXTREME_WIDE = "extreme_wide"
    WIDE = "wide"
    FULL = "full"
    MEDIUM_WIDE = "medium_wide"
    MEDIUM = "medium"
    MEDIUM_CLOSE = "medium_close"
    CLOSE_UP = "close_up"
    EXTREME_CLOSE_UP = "extreme_close_up"
    OVER_THE_SHOULDER = "over_the_shoulder"
    TWO_SHOT = "two_shot"
    INSERT = "insert"
    POV = "pov"


class CameraMove(str, Enum):
    STATIC = "static"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    TRACKING = "tracking"
    CRANE = "crane"
    HANDHELD = "handheld"
    ORBIT = "orbit"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"


class TransitionType(str, Enum):
    CUT = "cut"
    CROSSFADE = "crossfade"
    FADE_TO_BLACK = "fade_to_black"
    FADE_FROM_BLACK = "fade_from_black"
    MATCH_CUT = "match_cut"
    WHIP_PAN = "whip_pan"


class TimeOfDay(str, Enum):
    DAWN = "dawn"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    GOLDEN_HOUR = "golden_hour"
    DUSK = "dusk"
    NIGHT = "night"
    LATE_NIGHT = "late_night"


class ShotStatus(str, Enum):
    PENDING = "pending"
    KEYFRAME_READY = "keyframe_ready"
    VIDEO_READY = "video_ready"
    AUDIO_READY = "audio_ready"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Characters ────────────────────────────────────────────────────────────


class CharacterAppearance(BaseModel):
    """The immutable visual identity. This text is injected verbatim into every
    prompt that features the character, which is half of the consistency story."""

    age_appearance: str = Field(..., description="e.g. 'early 30s'")
    ethnicity_descriptor: str = Field("", description="Optional heritage/appearance descriptor")
    build: str = Field(..., description="e.g. 'lean, 5ft9, athletic shoulders'")
    face: str = Field(..., description="Face shape, jawline, cheekbones, nose, notable features")
    eyes: str = Field(..., description="Colour, shape, brow character")
    hair: str = Field(..., description="Colour, length, texture, styling")
    skin: str = Field("", description="Tone, texture, freckles, scars")
    distinguishing_marks: list[str] = Field(default_factory=list)
    signature_wardrobe: str = Field(..., description="Default costume seen most often")
    wardrobe_variants: dict[str, str] = Field(
        default_factory=dict, description="label → description, e.g. {'rain': 'soaked grey coat'}"
    )

    def to_prompt_fragment(self) -> str:
        bits = [
            self.age_appearance,
            self.ethnicity_descriptor,
            self.build,
            self.face,
            f"{self.eyes} eyes",
            f"{self.hair} hair",
            self.skin,
        ]
        if self.distinguishing_marks:
            bits.append(", ".join(self.distinguishing_marks))
        return ", ".join(b.strip() for b in bits if b and b.strip())


class ReferenceImage(BaseModel):
    """A canonical, reusable likeness anchor."""

    kind: Literal["portrait_front", "portrait_three_quarter", "portrait_profile",
                  "full_body", "wardrobe_variant", "expression", "location_plate"] = "portrait_front"
    label: str = ""
    asset_key: str
    url: str = ""
    prompt_used: str = ""
    model_id: str = ""
    is_canonical: bool = False


class VoiceProfile(BaseModel):
    provider_voice_id: str | None = None
    voice_name: str = ""
    gender: Literal["male", "female", "neutral"] = "neutral"
    age_bracket: str = "adult"
    accent: str = ""
    timbre: str = Field("", description="e.g. 'gravelly, low, unhurried'")
    default_pace: float = Field(1.0, ge=0.5, le=2.0)
    default_stability: float = Field(0.5, ge=0.0, le=1.0)


class CharacterProfile(BaseModel):
    id: str = Field(..., description="Stable slug, e.g. 'maya_rios'")
    name: str
    role: Literal["protagonist", "antagonist", "deuteragonist", "supporting", "minor"] = "supporting"
    one_line: str = Field(..., description="Logline for the character")
    backstory: str = ""
    motivation: str = ""
    flaw: str = ""
    arc: str = Field("", description="Where they start and where they end this episode")
    speech_style: str = Field("", description="Vocabulary, rhythm, verbal tics")
    appearance: CharacterAppearance
    voice: VoiceProfile = Field(default_factory=VoiceProfile)
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    consistency_token: str = Field(
        "", description="Short handle repeated in prompts, e.g. 'MAYA_RIOS'"
    )

    @computed_field  # type: ignore[misc]
    @property
    def canonical_reference(self) -> str | None:
        for ref in self.reference_images:
            if ref.is_canonical:
                return ref.url or ref.asset_key
        return (self.reference_images[0].url or self.reference_images[0].asset_key) if self.reference_images else None

    def reference_urls(self, limit: int = 3) -> list[str]:
        ordered = sorted(self.reference_images, key=lambda r: (not r.is_canonical,))
        return [r.url or r.asset_key for r in ordered[:limit] if (r.url or r.asset_key)]

    def identity_block(self) -> str:
        token = self.consistency_token or self.name.upper().replace(" ", "_")
        return f"{self.name} ({token}): {self.appearance.to_prompt_fragment()}. Wearing {self.appearance.signature_wardrobe}."


# ── World ─────────────────────────────────────────────────────────────────


class LocationProfile(BaseModel):
    id: str
    name: str
    description: str
    interior_exterior: Literal["INT", "EXT", "INT/EXT"] = "INT"
    architecture: str = ""
    palette: str = Field("", description="Dominant colours of the space")
    lighting_signature: str = ""
    props: list[str] = Field(default_factory=list)
    ambience_sound: str = Field("", description="Room tone / ambience description")
    reference_images: list[ReferenceImage] = Field(default_factory=list)

    def to_prompt_fragment(self) -> str:
        bits = [self.description, self.architecture, self.palette, self.lighting_signature]
        return ", ".join(b.strip() for b in bits if b and b.strip())


class VisualStyle(BaseModel):
    """One style contract applied to every single generated frame."""

    look: str = Field("cinematic live-action", description="e.g. 'cinematic live-action', '2D anime', 'stop motion'")
    film_stock: str = Field("digital Arri Alexa, subtle grain", description="Emulated capture medium")
    lens_language: str = Field("anamorphic 40mm, shallow depth of field", description="Default optics")
    color_grade: str = Field("teal-and-amber, lifted blacks", description="Grade description")
    lighting_style: str = Field("motivated practical lighting, strong key, soft fill")
    aspect_ratio: Literal["16:9", "9:16", "1:1", "2.39:1", "4:5"] = "16:9"
    resolution: Literal["480p", "720p", "1080p", "1440p", "4k"] = "1080p"
    frame_rate: int = 24
    negative_prompt: str = Field(
        "text, watermark, logo, subtitles, distorted hands, extra fingers, warped face, "
        "duplicate limbs, low resolution, blurry, oversaturated, plastic skin, uncanny",
        description="Appended to every image/video request that supports it",
    )

    def to_prompt_fragment(self) -> str:
        return (
            f"{self.look}, {self.film_stock}, {self.lens_language}, "
            f"{self.color_grade}, {self.lighting_style}"
        )


class AudioStyle(BaseModel):
    score_direction: str = "restrained orchestral tension with sparse piano"
    music_intensity_curve: Literal["flat", "rising", "arc", "pulsing"] = "arc"
    ambience_enabled: bool = True
    music_bed_gain_db: float = -18.0
    dialogue_gain_db: float = 0.0
    ambience_gain_db: float = -26.0
    duck_music_under_dialogue_db: float = -8.0


# ── Script structure ──────────────────────────────────────────────────────


class DialogueLine(BaseModel):
    character_id: str = Field(..., description="CharacterProfile.id, or 'NARRATOR'")
    text: str
    delivery: str = Field("", description="Parenthetical direction, e.g. '(barely audible)'")
    emotion: str = "neutral"
    estimated_seconds: float = 0.0
    audio_asset_key: str | None = None
    audio_url: str | None = None
    actual_seconds: float | None = None
    start_offset_s: float = 0.0

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    def duration(self) -> float:
        if self.actual_seconds:
            return self.actual_seconds
        if self.estimated_seconds:
            return self.estimated_seconds
        # ~2.6 words/second is a natural dramatic read
        return max(1.0, len(self.text.split()) / 2.6)


class ContinuityState(BaseModel):
    """The mutable world-state snapshot carried shot to shot."""

    story_clock: str = Field("", description="In-world time, e.g. 'Day 1, 21:40'")
    location_id: str = ""
    time_of_day: TimeOfDay = TimeOfDay.MIDDAY
    weather: str = ""
    characters_present: list[str] = Field(default_factory=list)
    wardrobe: dict[str, str] = Field(default_factory=dict, description="character_id → current outfit")
    character_condition: dict[str, str] = Field(
        default_factory=dict, description="character_id → 'bleeding from left brow, rain-soaked'"
    )
    props_in_play: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    notes: str = ""

    def fork(self) -> ContinuityState:
        return self.model_copy(deep=True)

    def to_prompt_fragment(self) -> str:
        bits = []
        if self.time_of_day:
            bits.append(f"time of day: {self.time_of_day.value.replace('_', ' ')}")
        if self.weather:
            bits.append(f"weather: {self.weather}")
        for cid, cond in self.character_condition.items():
            if cond:
                bits.append(f"{cid} is {cond}")
        if self.props_in_play:
            bits.append("visible props: " + ", ".join(self.props_in_play[:6]))
        return "; ".join(bits)


class Shot(BaseModel):
    """The atomic generation unit. One shot == one model call == 4-10 seconds."""

    id: str
    scene_id: str
    index: int = Field(..., description="Global shot index within the episode")
    scene_shot_index: int = Field(0, description="Index within the parent scene")

    # Direction
    shot_size: ShotSize = ShotSize.MEDIUM
    camera_move: CameraMove = CameraMove.STATIC
    subject_ids: list[str] = Field(default_factory=list, description="Characters in frame")
    action: str = Field(..., description="What physically happens, present tense")
    emotional_beat: str = ""
    transition_in: TransitionType = TransitionType.CUT

    # Timing
    target_seconds: float = Field(6.0, ge=1.0, le=20.0)
    actual_seconds: float | None = None

    # Dialogue carried by this shot
    dialogue: list[DialogueLine] = Field(default_factory=list)

    # Generated prompts
    keyframe_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = ""
    reference_image_urls: list[str] = Field(default_factory=list)

    # Produced assets
    keyframe_asset_key: str | None = None
    keyframe_url: str | None = None
    last_frame_asset_key: str | None = Field(
        None, description="Tail frame, used to chain the next shot for continuity"
    )
    video_asset_key: str | None = None
    video_url: str | None = None
    audio_asset_key: str | None = None
    sfx_prompt: str = ""

    # Bookkeeping
    status: ShotStatus = ShotStatus.PENDING
    continuity_in: ContinuityState | None = None
    attempts: int = 0
    failure_reason: str | None = None
    models_used: dict[str, str] = Field(default_factory=dict)
    cost_usd: float = 0.0
    critic_score: float | None = None
    critic_notes: str = ""

    def dialogue_seconds(self) -> float:
        return sum(line.duration() for line in self.dialogue)

    def planned_seconds(self) -> float:
        """A shot must be at least long enough to carry its dialogue."""
        return max(self.target_seconds, self.dialogue_seconds() + 0.8)


class Scene(BaseModel):
    id: str
    episode_id: str = ""
    index: int
    act_number: int = 1
    slug_line: str = Field(..., description="INT. PRECINCT BULLPEN - NIGHT")
    location_id: str
    time_of_day: TimeOfDay = TimeOfDay.MIDDAY
    synopsis: str
    dramatic_function: str = Field("", description="Why this scene exists in the story engine")
    characters_present: list[str] = Field(default_factory=list)
    target_seconds: float = 45.0
    shots: list[Shot] = Field(default_factory=list)
    continuity_in: ContinuityState | None = None
    continuity_out: ContinuityState | None = None

    def planned_seconds(self) -> float:
        return sum(s.planned_seconds() for s in self.shots) or self.target_seconds


class Act(BaseModel):
    number: int
    title: str
    purpose: str = Field("", description="Setup / confrontation / resolution beat")
    scene_indices: list[int] = Field(default_factory=list)
    target_seconds: float = 240.0


class StoryBeat(BaseModel):
    index: int
    act_number: int
    title: str
    description: str
    tension: int = Field(5, ge=1, le=10)
    target_seconds: float = 60.0


class SeriesBible(BaseModel):
    """Persistent, reusable show context. Episode 2 costs far less than episode 1
    because everything here is already locked."""

    id: str = ""
    title: str
    logline: str
    genre: str = "drama"
    tone: str = ""
    themes: list[str] = Field(default_factory=list)
    setting: str = ""
    audience: str = "adult"
    content_rating: Literal["G", "PG", "PG-13", "R"] = "PG-13"
    language: str = "en"
    characters: list[CharacterProfile] = Field(default_factory=list)
    locations: list[LocationProfile] = Field(default_factory=list)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)
    audio_style: AudioStyle = Field(default_factory=AudioStyle)
    series_arc: str = ""

    def character(self, cid: str) -> CharacterProfile | None:
        return next((c for c in self.characters if c.id == cid), None)

    def location(self, lid: str) -> LocationProfile | None:
        return next((loc for loc in self.locations if loc.id == lid), None)

    def cast_block(self, ids: list[str] | None = None) -> str:
        chosen = [c for c in self.characters if ids is None or c.id in ids]
        return "\n".join(f"- {c.identity_block()}" for c in chosen)


class Episode(BaseModel):
    id: str = ""
    series_id: str = ""
    number: int = 1
    title: str = ""
    premise: str = ""
    target_seconds: float = Field(720.0, description="10-15 min dramas: 600-900s")
    logline: str = ""
    acts: list[Act] = Field(default_factory=list)
    beats: list[StoryBeat] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    cold_open: str = ""
    cliffhanger: str = ""
    score_asset_keys: list[str] = Field(default_factory=list)
    ambience_asset_keys: dict[str, str] = Field(
        default_factory=dict, description="scene index (as string) → ambience asset key"
    )
    score_asset_key: str | None = None
    final_video_asset_key: str | None = None
    final_video_url: str | None = None
    thumbnail_asset_key: str | None = None
    subtitle_asset_key: str | None = None
    actual_seconds: float | None = None

    def all_shots(self) -> list[Shot]:
        return [shot for scene in self.scenes for shot in scene.shots]

    def planned_seconds(self) -> float:
        return sum(scene.planned_seconds() for scene in self.scenes)

    def shot_count(self) -> int:
        return sum(len(s.shots) for s in self.scenes)

    def completion_ratio(self) -> float:
        shots = self.all_shots()
        if not shots:
            return 0.0
        done = sum(1 for s in shots if s.status in (ShotStatus.COMPLETE, ShotStatus.VIDEO_READY, ShotStatus.AUDIO_READY))
        return done / len(shots)


class ProductionPlan(BaseModel):
    """Everything a job needs to run, checkpointed between pipeline stages."""

    bible: SeriesBible
    episode: Episode
    continuity_log: list[ContinuityState] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
