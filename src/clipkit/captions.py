from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from clipkit.models import Word
from clipkit.text import clamp_words
from clipkit.util import atomic_write_text, format_ass_time


@dataclass
class CaptionStyle:
    font: str = "Montserrat ExtraBold"
    font_size: int = 82
    primary: str = "#FFFFFF"
    highlight: str = "#00FF66"
    outline: str = "#000000"
    outline_width: int = 6
    shadow: int = 3
    position_y: int = 1450
    max_words: int = 3
    max_chars: int = 22
    uppercase: bool = True
    pop: bool = True
    margin_h: int = 40
    margin_v: int = 60
    canvas_width: int = 1080
    canvas_height: int = 1920

    @classmethod
    def from_dict(cls, data: dict | None) -> CaptionStyle:
        if not data:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in allowed})


def bundled_font_dir() -> Path | None:
    path = Path(__file__).parent / "assets" / "fonts"
    return path if path.is_dir() else None


def ensure_caption_font(destination: Path) -> Path | None:
    """Copy the bundled OFL font next to the render so libass can find it."""
    source_dir = bundled_font_dir()
    if source_dir is None:
        return None
    source = source_dir / "Montserrat-ExtraBold.ttf"
    if not source.exists():
        return None
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    license_file = source_dir / "OFL.txt"
    if license_file.exists() and not (destination / "OFL.txt").exists():
        shutil.copy2(license_file, destination / "OFL.txt")
    return target


@dataclass
class Phrase:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end


def hex_to_ass(value: str, alpha: str = "00") -> str:
    value = value.strip().lstrip("#")
    if len(value) == 8:  # AARRGGBB
        alpha, value = value[:2], value[2:]
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        value = "FFFFFF"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha}{blue}{green}{red}".upper()


def _escape(text: str) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def group_words(
    words: list[Word],
    *,
    clip_start: float,
    clip_end: float,
    style: CaptionStyle,
) -> list[Phrase]:
    clipped = clamp_words(words, clip_start, clip_end)
    phrases: list[Phrase] = []
    current: list[Word] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            phrases.append(Phrase(words=current))
            current = []
            length = 0

    for word in clipped:
        text = _escape(word.text)
        if not text:
            continue
        if current and word.start - current[-1].end > 0.6:
            flush()
        addition = len(text) + (1 if current else 0)
        if current and (len(current) >= style.max_words or length + addition > style.max_chars):
            flush()
        current.append(
            Word(text=text, start=max(clip_start, word.start), end=min(clip_end, word.end), speaker=word.speaker)
        )
        length += addition
        if re.search(r"[.!?]$", text):
            flush()
    flush()
    return phrases


def _fit_size(phrase: Phrase, style: CaptionStyle) -> int:
    estimated = 0.0
    for word in phrase.words:
        estimated += len(word.text) * style.font_size * 0.60
    estimated += max(0, len(phrase.words) - 1) * style.font_size * 0.28
    available = style.canvas_width - 2 * style.margin_h
    if estimated <= available:
        return style.font_size
    return max(44, int(style.font_size * available / estimated))


def _phrase_event_text(phrase: Phrase, active_index: int, style: CaptionStyle, font_size: int) -> str:
    primary = hex_to_ass(style.primary) + "&"
    highlight = hex_to_ass(style.highlight) + "&"
    parts: list[str] = []
    for index, word in enumerate(phrase.words):
        text = word.text.upper() if style.uppercase else word.text
        if index == active_index:
            if style.pop:
                tag = (
                    f"{{\\c{highlight}\\fscx112\\fscy112"
                    f"\\t(0,60,\\fscx112\\fscy112)\\t(60,140,\\fscx100\\fscy100)}}"
                )
            else:
                tag = f"{{\\c{highlight}}}"
            parts.append(f"{tag}{text}{{\\c{primary}\\fscx100\\fscy100}}")
        else:
            parts.append(text)
    body = " ".join(parts)
    return f"{{\\an2\\pos({style.canvas_width // 2},{style.position_y})\\fs{font_size}}}{body}"


def build_ass(
    words: list[Word],
    *,
    clip_start: float,
    clip_end: float,
    style: CaptionStyle | None = None,
) -> str:
    style = style or CaptionStyle()
    primary = hex_to_ass(style.primary)
    outline = hex_to_ass(style.outline)
    back = hex_to_ass(style.outline, alpha="80")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {style.canvas_width}
PlayResY: {style.canvas_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font},{style.font_size},{primary},{primary},{outline},{back},-1,0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},2,{style.margin_h},{style.margin_h},{style.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    phrases = group_words(words, clip_start=clip_start, clip_end=clip_end, style=style)
    for phrase in phrases:
        font_size = _fit_size(phrase, style)
        for index, word in enumerate(phrase.words):
            start = max(clip_start, word.start)
            if index + 1 < len(phrase.words):
                end = max(start + 0.05, phrase.words[index + 1].start)
            else:
                end = max(start + 0.05, min(clip_end, phrase.end))
            if start >= clip_end:
                continue
            end = min(end, clip_end)
            if end <= start:
                continue
            text = _phrase_event_text(phrase, index, style, font_size)
            lines.append(
                f"Dialogue: 0,{format_ass_time(start - clip_start)},{format_ass_time(end - clip_start)},"
                f"Default,,0,0,0,,{text}"
            )

    if not lines:
        return header
    return header + "\n".join(lines) + "\n"


def write_ass(
    path: Path,
    words: list[Word],
    *,
    clip_start: float,
    clip_end: float,
    style: CaptionStyle | None = None,
) -> Path:
    atomic_write_text(path, build_ass(words, clip_start=clip_start, clip_end=clip_end, style=style))
    return path
