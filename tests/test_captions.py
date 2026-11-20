from clipkit.captions import (
    CaptionStyle,
    build_ass,
    ensure_caption_font,
    group_words,
    hex_to_ass,
)
from clipkit.models import Word


def _words():
    return [
        Word(text="Most", start=10.0, end=10.3),
        Word(text="founders", start=10.3, end=10.8),
        Word(text="ignore", start=10.8, end=11.2),
        Word(text="this.", start=11.2, end=11.6),
    ]


def test_hex_to_ass_is_bgr():
    assert hex_to_ass("#00FF66") == "&H0066FF00"
    assert hex_to_ass("#FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass("bad") == "&H00FFFFFF"


def test_group_words_respects_max_words():
    phrases = group_words(
        _words(), clip_start=10.0, clip_end=12.0, style=CaptionStyle(max_words=2, max_chars=100)
    )
    assert all(len(phrase.words) <= 2 for phrase in phrases)


def test_build_ass_has_karaoke_events_and_relative_times():
    ass = build_ass(_words(), clip_start=10.0, clip_end=12.0)
    assert "[Script Info]" in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Dialogue:" in ass
    assert "0:00:00.00" in ass
    assert "0:00:01.60" in ass
    assert "MOST" in ass
    assert "\\pos(540,1450)" in ass
    assert "\\c&H0066FF00&" in ass


def test_build_ass_escapes_override_characters():
    words = [Word(text="{hello}", start=0.0, end=1.0)]
    ass = build_ass(words, clip_start=0.0, clip_end=1.0)
    assert "{hello}" not in ass
    assert "(HELLO)" in ass


def test_style_can_be_loaded_from_dict_ignoring_unknown_keys():
    style = CaptionStyle.from_dict({"font_size": 64, "position_y": 1200, "bogus": True})
    assert style.font_size == 64
    assert style.position_y == 1200


def test_bundled_font_is_copied_next_to_the_render(tmp_path):
    target = ensure_caption_font(tmp_path)
    assert target is not None
    assert target.exists()
    assert target.stat().st_size > 100_000
    assert (tmp_path / "OFL.txt").exists()
