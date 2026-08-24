"""Dependency-free PNG writer with a tiny bitmap font.

The offline simulator needs to produce *readable* placeholder plates — a shot
slate that names the scene, the characters in frame and the shot size is far
more useful for reviewing a cut than a grey rectangle.  Pillow is optional in
this project, so this module writes PNGs directly with zlib.
"""
from __future__ import annotations

import struct
import zlib

# 5x7 bitmap font, uppercase + digits + a little punctuation.
_FONT: dict[str, tuple[int, ...]] = {
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11), "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E), "D": (0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F), "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F), "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E), "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11), "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11), "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E), "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D), "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E), "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E), "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11), "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04), "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E), "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F), "3": (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02), "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E), "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E), "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    " ": (0, 0, 0, 0, 0, 0, 0), ".": (0, 0, 0, 0, 0, 0x0C, 0x0C), ",": (0, 0, 0, 0, 0x0C, 0x04, 0x08),
    "-": (0, 0, 0, 0x1F, 0, 0, 0), ":": (0, 0x0C, 0x0C, 0, 0x0C, 0x0C, 0), "/": (0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10),
    "'": (0x04, 0x04, 0, 0, 0, 0, 0), "!": (0x04, 0x04, 0x04, 0x04, 0x04, 0, 0x04),
    "?": (0x0E, 0x11, 0x01, 0x02, 0x04, 0, 0x04), "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08), "#": (0x0A, 0x1F, 0x0A, 0x0A, 0x1F, 0x0A, 0x00),
    "+": (0, 0x04, 0x04, 0x1F, 0x04, 0x04, 0), "=": (0, 0, 0x1F, 0, 0x1F, 0, 0),
    "_": (0, 0, 0, 0, 0, 0, 0x1F), "*": (0, 0x0A, 0x04, 0x1F, 0x04, 0x0A, 0),
}
_GLYPH_W, _GLYPH_H = 5, 7


class Canvas:
    """A tiny RGB raster you can draw rectangles, gradients and text onto."""

    def __init__(self, width: int, height: int, background: tuple[int, int, int] = (18, 20, 26)):
        self.width = width
        self.height = height
        self.pixels = bytearray(width * height * 3)
        self.fill(background)

    def fill(self, color: tuple[int, int, int]) -> None:
        self.pixels[:] = bytes(color) * (self.width * self.height)

    def set_pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.pixels[i : i + 3] = bytes(color)

    def rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
        row = bytes(color) * max(0, min(w, self.width - x))
        for yy in range(max(0, y), min(y + h, self.height)):
            i = (yy * self.width + max(0, x)) * 3
            self.pixels[i : i + len(row)] = row

    def vertical_gradient(
        self, top: tuple[int, int, int], bottom: tuple[int, int, int], *, y0: int = 0, y1: int | None = None
    ) -> None:
        y1 = self.height if y1 is None else y1
        span = max(1, y1 - y0 - 1)
        for y in range(y0, min(y1, self.height)):
            t = (y - y0) / span
            color = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
            self.rect(0, y, self.width, 1, color)  # type: ignore[arg-type]

    def vignette(self, strength: float = 0.55) -> None:
        cx, cy = self.width / 2, self.height / 2
        max_d = (cx**2 + cy**2) ** 0.5
        for y in range(0, self.height, 2):
            for x in range(0, self.width, 2):
                d = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) / max_d
                factor = max(0.0, 1.0 - strength * d * d)
                for dy in range(2):
                    for dx in range(2):
                        px, py = x + dx, y + dy
                        if px < self.width and py < self.height:
                            i = (py * self.width + px) * 3
                            for c in range(3):
                                self.pixels[i + c] = int(self.pixels[i + c] * factor)

    def text(
        self,
        s: str,
        x: int,
        y: int,
        color: tuple[int, int, int] = (235, 238, 245),
        scale: int = 2,
    ) -> int:
        """Draw uppercase text; returns the x cursor after the last glyph."""
        cursor = x
        for ch in s.upper():
            glyph = _FONT.get(ch)
            if glyph is None:
                cursor += (_GLYPH_W + 1) * scale
                continue
            for row, bits in enumerate(glyph):
                for col in range(_GLYPH_W):
                    if bits & (1 << (_GLYPH_W - 1 - col)):
                        self.rect(cursor + col * scale, y + row * scale, scale, scale, color)
            cursor += (_GLYPH_W + 1) * scale
        return cursor

    def text_centered(self, s: str, y: int, color=(235, 238, 245), scale: int = 2) -> None:
        width = len(s) * (_GLYPH_W + 1) * scale
        self.text(s, max(4, (self.width - width) // 2), y, color, scale)

    def wrap_text(self, s: str, x: int, y: int, max_chars: int, color=(200, 206, 218),
                  scale: int = 2, line_gap: int = 6, max_lines: int = 6) -> int:
        words, line, lines = s.split(), "", []
        for word in words:
            candidate = f"{line} {word}".strip()
            if len(candidate) > max_chars and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        for i, text_line in enumerate(lines[:max_lines]):
            self.text(text_line, x, y + i * (_GLYPH_H * scale + line_gap), color, scale)
        return y + min(len(lines), max_lines) * (_GLYPH_H * scale + line_gap)

    def to_png(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)  # filter type 0
            raw.extend(self.pixels[y * stride : (y + 1) * stride])

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + tag
                + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b"")
        )


def palette_from_seed(seed: int) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """Deterministic, cinematic-ish three-colour palette."""
    hue = (seed % 360) / 360.0
    def hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
        i = int(h * 6) % 6
        f = h * 6 - int(h * 6)
        p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
        r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
        return int(r * 255), int(g * 255), int(b * 255)

    return hsv(hue, 0.45, 0.30), hsv((hue + 0.08) % 1.0, 0.55, 0.12), hsv((hue + 0.5) % 1.0, 0.60, 0.85)
