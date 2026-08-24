"""Deterministic JSON-Schema-driven content generator for the offline engine.

Given the same schema and the same prompt it always returns the same document,
which is what makes CI runs and demo videos reproducible.  Values are drawn from
a screenwriting phrase bank keyed on the property name, so the output reads like
a (thin) drama rather than lorem ipsum.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any

_PROPER_NOUN = re.compile(r"\b([A-Z][a-z]{2,})\b")

# Capitalised words that are grammar, not characters. Without this the offline
# engine happily casts "Your" and "Every" as the leads.
_NOT_A_NAME = {
    "The", "This", "That", "These", "Those", "There", "Their", "They", "Them",
    "Your", "You", "Our", "Every", "Each", "Some", "Any", "All", "Both", "Which",
    "What", "When", "Where", "While", "With", "Without", "From", "Into", "Over",
    "Under", "After", "Before", "Because", "Return", "Respond", "Write", "Create",
    "Give", "Make", "Break", "Keep", "Show", "Tell", "Take", "Never", "Always",
    "Episode", "Scene", "Shot", "Shots", "Act", "Acts", "Beat", "Beats", "Series",
    "Story", "Cast", "Character", "Characters", "Location", "Locations", "Dialogue",
    "Camera", "Staging", "Motion", "Style", "Tone", "Genre", "Runtime", "Target",
    "Schema", "Json", "Rules", "Craft", "Constraints", "Note", "Notes", "Brief",
    "Night", "Day", "Dusk", "Dawn", "Morning", "Afternoon", "Evening",
}

_FIRST_NAMES = ["Maya", "Idris", "Noor", "Elias", "Rania", "Kofi", "Ines", "Dmitri",
                "Amara", "Soren", "Yara", "Tomas", "Leila", "Anders", "Priya", "Malik"]
_LAST_NAMES = ["Rios", "Okonkwo", "Haddad", "Vance", "Marsh", "Boateng", "Lindqvist",
               "Serrano", "Novak", "Abara", "Kestrel", "Duval"]

_LOCATIONS = [
    ("Harbour Precinct", "INT"), ("Rooftop Water Tower", "EXT"), ("Night Bus Depot", "INT"),
    ("Riverside Allotment", "EXT"), ("Basement Archive", "INT"), ("Motel Corridor", "INT"),
    ("Storm Drain Outflow", "EXT"), ("Twenty-Fourth Floor Office", "INT"),
]

_ACTIONS = [
    "sets the envelope down without letting go of it",
    "turns from the window as the door closes",
    "counts the money a second time, slower",
    "stops in the doorway and does not come in",
    "wipes the rain off the photograph with a thumb",
    "waits for the lift and watches the numbers fall",
    "puts the phone face down on the table",
    "steps into the light and lets them see the bruise",
    "pours two drinks and drinks neither",
    "hands over the key and keeps the fob",
]

_DIALOGUE = [
    "You said this was finished.", "I never said that. I said it was quiet.",
    "Whatever you think you owe me, it isn't this.", "Sit down. Please.",
    "They know about the harbour.", "Then we move tonight.",
    "I'm not asking you to forgive it.", "You keep saying we like it's still a word.",
    "Give me one hour.", "It was never one hour.",
    "Look at me when you lie, at least.", "That's the part I can't take back.",
]

_CHARACTER_LINES = [
    "A fixer who stopped being able to tell clients from family.",
    "The only honest person in the room, and everyone resents it.",
    "Came back to bury someone and stayed to find out why they died.",
    "Owns half the harbour and none of his own decisions.",
    "Keeps the ledger nobody else will admit exists.",
    "Would burn it all down if anyone would just ask her to.",
]

_TITLES = ["The Quiet Hour", "Everything We Owe", "Slack Water", "Nightside",
           "What the Tide Keeps", "Small Mercies", "The Long Way Back"]

_THEMES = ["loyalty and its price", "the cost of staying", "inherited debt",
           "what people do to be believed", "the difference between guilt and grief"]

_EMOTIONS = ["guarded", "furious", "exhausted", "tender", "cold", "frightened",
             "resolved", "grieving", "amused", "desperate"]

_FEELING_ADJ = ["restrained", "unsentimental", "close and humid", "cold-blue and quiet",
                "tense", "aching", "sharp"]


class SimContext:
    """Names lifted out of the user's own prompt keep the demo feeling personal."""

    def __init__(self, prompt: str, seed: int):
        self.rng = random.Random(seed)
        found = [
            n for n in _PROPER_NOUN.findall(prompt)
            if n not in _NOT_A_NAME
        ]
        seen: list[str] = []
        for n in found:
            if n not in seen:
                seen.append(n)
        # "Maya Rios" arrives as two capitalised tokens; rejoin the pairs that
        # actually sat next to each other in the prompt.
        self.prompt_names: list[str] = []
        i = 0
        while i < len(seen):
            if i + 1 < len(seen) and f"{seen[i]} {seen[i + 1]}" in prompt:
                self.prompt_names.append(f"{seen[i]} {seen[i + 1]}")
                i += 2
            else:
                self.prompt_names.append(seen[i])
                i += 1
        self.prompt_names = self.prompt_names[:6]

    def person(self, index: int) -> str:
        if index < len(self.prompt_names):
            return self.prompt_names[index]
        first = _FIRST_NAMES[(index * 7) % len(_FIRST_NAMES)]
        last = _LAST_NAMES[(index * 5) % len(_LAST_NAMES)]
        return f"{first} {last}"


def _index_from_path(path: str) -> int:
    """Stable per-slot index so repeated entries get distinct names."""
    digits = re.findall(r"\[(\d+)\]", path)
    return int(digits[-1]) if digits else 0


def _seed_of(*parts: str) -> int:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _string_for(name: str, ctx: SimContext, rng: random.Random, path: str) -> str:
    n = name.lower()

    slot = _index_from_path(path)

    def pick(seq):
        # Offsetting by the array slot stops sibling entries from colliding on
        # the same phrase, which is the giveaway that output is synthetic.
        return seq[(rng.randrange(len(seq)) + slot) % len(seq)]

    if "slug" in n:
        place, io_ = pick(_LOCATIONS)
        return f"{io_}. {place.upper()} - {pick(['NIGHT', 'DAY', 'DUSK', 'LATE NIGHT'])}"
    if n.endswith("_id") or n == "id":
        base = pick(_FIRST_NAMES).lower()
        return f"{base}_{rng.randrange(100, 999)}"
    if "action" in n:
        return pick(_ACTIONS)
    if "one_line" in n or "tagline" in n:
        return pick(_CHARACTER_LINES)
    if "logline" in n or "premise" in n or "hook" in n:
        who = ctx.person(0)
        return (f"{who} has one night to undo a favour that was never a favour, "
                f"and the only person who can help is the one it was done to.")
    if n == "title" or n.endswith("_title"):
        return pick(_TITLES)
    if "name" in n:
        return ctx.person(_index_from_path(path))
    if "dialogue" in n or n == "text" or n.endswith("_line") or n == "line":
        return pick(_DIALOGUE)
    if "synopsis" in n or "description" in n or "summary" in n:
        return (f"{ctx.person(rng.randrange(0, 3))} pushes for an answer; the room gives up "
                f"something smaller and worse than the truth.")
    if "theme" in n:
        return pick(_THEMES)
    if "emotion" in n or "beat" in n or "delivery" in n:
        return pick(_EMOTIONS)
    if "tone" in n or "mood" in n:
        return pick(_FEELING_ADJ)
    if "prompt" in n:
        return ("cinematic live-action, anamorphic 40mm, shallow depth of field, "
                "motivated practical lighting, teal and amber grade, film grain")
    if "backstory" in n or "arc" in n or "motivation" in n or "flaw" in n:
        return ("Grew up paying other people's debts; now cannot tell the difference "
                "between owing and belonging.")
    if "wardrobe" in n or "costume" in n:
        return pick(["rain-dark wool overcoat over a grey crew neck",
                     "creased linen shirt, sleeves pushed up",
                     "black work jacket with a torn cuff"])
    if "hair" in n:
        return pick(["black, cropped close", "auburn, shoulder length, always half-tied",
                     "silver at the temples, swept back"])
    if "eyes" in n:
        return pick(["dark brown, deep set", "pale grey, heavy lidded", "green, direct"])
    if "face" in n:
        return pick(["angular jaw, high cheekbones, a nose broken once and set badly",
                     "soft square face, deep laugh lines, tired around the mouth"])
    if "build" in n:
        return pick(["lean, 5ft9, wiry", "broad shouldered, 6ft1, heavy through the chest"])
    if "skin" in n:
        return pick(["warm brown, faint scarring along the left jaw", "olive, sun-worn"])
    if "voice" in n or "timbre" in n or "speech" in n:
        return pick(["low, unhurried, drops to a whisper when angry",
                     "clipped and precise, never repeats herself"])
    if "camera" in n:
        return pick(["slow dolly in", "locked off, no movement", "handheld, breathing with him"])
    if "feedback" in n or "note" in n or "reason" in n:
        return "Reads clean. Identity anchors present in every shot; continuity holds."
    if "purpose" in n or "function" in n:
        return pick(["escalate the debt", "reveal the lie", "close the door on retreat",
                     "give the audience hope, then take it"])
    if "cliffhanger" in n:
        return "The lift doors open on the one person who was supposed to be gone."
    if "weather" in n:
        return pick(["steady rain", "clear and cold", "wind, no rain yet"])
    if "url" in n or "asset" in n or "path" in n or "key" in n:
        return ""
    return f"{name.replace('_', ' ')} placeholder"


def generate_from_schema(
    schema: dict,
    prompt: str,
    *,
    seed: int | None = None,
    path: str = "$",
    ctx: SimContext | None = None,
    depth: int = 0,
) -> Any:
    seed = seed if seed is not None else _seed_of(prompt, str(schema.get("title", "")))
    ctx = ctx or SimContext(prompt, seed)
    rng = random.Random(_seed_of(path, str(seed)))

    if schema.get("enum"):
        return schema["enum"][rng.randrange(len(schema["enum"]))]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf", "allOf"):
        if schema.get(key):
            branches = [b for b in schema[key] if b.get("type") != "null"] or schema[key]
            return generate_from_schema(branches[0], prompt, seed=seed, path=path, ctx=ctx, depth=depth)

    stype = schema.get("type")
    if isinstance(stype, list):
        stype = next((t for t in stype if t != "null"), "string")

    if stype == "object" or "properties" in schema:
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", list(props)))
        out: dict[str, Any] = {}
        for name, sub in props.items():
            if name not in required and depth > 2:
                continue
            out[name] = generate_from_schema(
                sub, prompt, seed=seed, path=f"{path}.{name}", ctx=ctx, depth=depth + 1
            )
            if isinstance(out[name], str) and not out[name] and name in required:
                out[name] = _string_for(name, ctx, rng, path)
        return out

    if stype == "array":
        items = schema.get("items", {"type": "string"})
        lo = int(schema.get("minItems", 3))
        hi = int(schema.get("maxItems", max(lo, lo + 2)))
        count = max(1, min(hi, max(lo, lo)))
        return [
            generate_from_schema(items, prompt, seed=seed, path=f"{path}[{i}]", ctx=ctx, depth=depth + 1)
            for i in range(count)
        ]

    if stype == "integer":
        lo = int(schema.get("minimum", 1))
        hi = int(schema.get("maximum", max(lo + 8, 10)))
        return rng.randint(lo, hi)

    if stype == "number":
        lo = float(schema.get("minimum", 1.0))
        hi = float(schema.get("maximum", max(lo + 8.0, 10.0)))
        return round(rng.uniform(lo, hi), 2)

    if stype == "boolean":
        return rng.random() > 0.4

    if stype == "null":
        return None

    field_name = path.rsplit(".", 1)[-1].split("[")[0]
    return _string_for(field_name, ctx, rng, path)
