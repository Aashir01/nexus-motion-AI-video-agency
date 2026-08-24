"""Character consistency is the product's core claim, so it is asserted directly:
the identity block must reach every prompt, unmodified, for every shot."""
from __future__ import annotations

from nexus.agents.visual import (
    build_keyframe_prompt,
    build_portrait_prompt,
    build_video_prompt,
    identity_clause,
)
from nexus.story.schemas import (
    CameraMove,
    CharacterAppearance,
    CharacterProfile,
    ContinuityState,
    LocationProfile,
    ReferenceImage,
    SeriesBible,
    Shot,
    ShotSize,
    VisualStyle,
)


def _character(cid="maya_rios", name="Maya Rios") -> CharacterProfile:
    return CharacterProfile(
        id=cid, name=name, role="protagonist", one_line="A fixer out of favours",
        appearance=CharacterAppearance(
            age_appearance="early 30s", build="lean, 5ft9",
            face="angular jaw, a nose broken once and set badly",
            eyes="dark brown, deep set", hair="black, cropped close",
            skin="warm brown", distinguishing_marks=["scar through the left eyebrow"],
            signature_wardrobe="rain-dark wool overcoat",
            wardrobe_variants={"soaked": "the same coat, soaked through"},
        ),
        reference_images=[
            ReferenceImage(kind="portrait_front", asset_key="k1",
                           url="https://cdn/x/front.png", is_canonical=True),
            ReferenceImage(kind="full_body", asset_key="k2", url="https://cdn/x/body.png"),
        ],
    )


def _bible() -> SeriesBible:
    return SeriesBible(
        title="Slack Water", logline="A harbour town keeps its debts",
        characters=[_character()],
        locations=[LocationProfile(id="precinct", name="Harbour Precinct",
                                   description="a tiled room that has never been warm",
                                   lighting_signature="cold fluorescents", palette="teal and rust")],
        visual_style=VisualStyle(),
    )


def _shot(**kw) -> Shot:
    defaults = {"id": "sht_1", "scene_id": "scn_1", "index": 1,
                "action": "she turns from the window", "subject_ids": ["maya_rios"],
                "shot_size": ShotSize.MEDIUM_CLOSE, "camera_move": CameraMove.DOLLY_IN}
    return Shot(**{**defaults, **kw})


def test_identity_clause_contains_every_immutable_trait():
    clause = identity_clause([_character()], None)
    for fragment in ("early 30s", "angular jaw", "dark brown", "black, cropped close",
                     "scar through the left eyebrow", "rain-dark wool overcoat"):
        assert fragment in clause, f"{fragment!r} missing from the identity clause"


def test_continuity_overrides_wardrobe_but_not_the_face():
    state = ContinuityState(
        wardrobe={"maya_rios": "the same coat, soaked through"},
        character_condition={"maya_rios": "bleeding from the left brow"},
    )
    clause = identity_clause([_character()], state)
    assert "soaked through" in clause
    assert "rain-dark wool overcoat" not in clause
    assert "bleeding from the left brow" in clause
    assert "angular jaw" in clause, "appearance must survive a wardrobe change"


def test_keyframe_prompt_leads_with_identity():
    prompt = build_keyframe_prompt(_shot(), "she stands off-centre right", _bible(),
                                   _bible().locations[0], None)
    assert "CHARACTERS IN FRAME" in prompt
    assert prompt.index("Maya Rios") < prompt.index("STAGING")
    assert "angular jaw" in prompt
    assert "Harbour Precinct".lower() in prompt.lower() or "tiled room" in prompt
    assert "no text or watermark" in prompt


def test_video_prompt_with_keyframe_defers_to_the_frame():
    """Re-describing a face over an image-to-video call fights the source frame."""
    prompt = build_video_prompt(_shot(), "she turns her head", _bible(),
                                _bible().locations[0], None, has_keyframe=True)
    assert "angular jaw" not in prompt
    assert "stay exactly as they appear in the source frame" in prompt
    assert "dolly in" in prompt


def test_video_prompt_without_keyframe_carries_full_identity():
    prompt = build_video_prompt(_shot(), "she turns her head", _bible(),
                                _bible().locations[0], None, has_keyframe=False)
    assert "angular jaw" in prompt


def test_portrait_prompt_is_a_neutral_anchor():
    prompt = build_portrait_prompt(_character(), _bible(), "portrait_front")
    assert "neutral expression" in prompt
    assert "plain neutral mid-grey background" in prompt
    assert "angular jaw" in prompt
    assert "No props" in prompt


def test_reference_urls_prefer_the_canonical_portrait():
    urls = _character().reference_urls(limit=2)
    assert urls[0] == "https://cdn/x/front.png"


def test_every_shot_in_a_scene_gets_the_same_identity_text():
    bible = _bible()
    shots = [_shot(id=f"s{i}", index=i) for i in range(6)]
    clauses = {
        build_keyframe_prompt(s, f"staging {s.index}", bible, bible.locations[0], None)
        .split("STAGING")[0]
        for s in shots
    }
    assert len(clauses) == 1, "identity text drifted between shots in the same scene"


def test_shot_duration_always_covers_its_dialogue():
    from nexus.story.schemas import DialogueLine

    shot = _shot(target_seconds=4.0, dialogue=[
        DialogueLine(character_id="maya_rios", text=" ".join(["word"] * 30)),
    ])
    assert shot.planned_seconds() > shot.dialogue_seconds()
    assert shot.planned_seconds() > 4.0
