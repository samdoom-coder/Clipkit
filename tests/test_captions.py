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


def test_hook_banner_renders_above_the_captions():
    ass = build_ass(
        _words(),
        clip_start=10.0,
        clip_end=12.0,
        hook_text="Most founders ignore this",
    )
    assert "Style: Hook," in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Hook" in ass
    assert "\\an8\\pos(540,90)" in ass
    assert "MOST FOUNDERS IGNORE THIS" in ass


def test_hook_banner_respects_duration_and_style():
    style = CaptionStyle(hook_duration=1.25, hook_position_y=110, hook_font_size=48)
    ass = build_ass(_words(), clip_start=0.0, clip_end=5.0, style=style, hook_text="Watch this")
    assert "Dialogue: 0,0:00:00.00,0:00:01.25,Hook" in ass
    assert "\\pos(540,110)" in ass
    assert ",48," in ass


def test_hook_banner_wraps_long_text():
    style = CaptionStyle(hook_max_chars=18, hook_max_lines=2)
    hook = "one two three four five six seven eight nine ten eleven twelve"
    ass = build_ass(_words(), clip_start=0.0, clip_end=5.0, style=style, hook_text=hook)
    assert "\\N" in ass
    assert "..." in ass


def test_no_hook_style_without_hook_text():
    ass = build_ass(_words(), clip_start=0.0, clip_end=2.0)
    assert "Style: Hook," not in ass


def test_hook_banner_survives_captions_off():
    ass = build_ass(
        _words(),
        clip_start=0.0,
        clip_end=2.0,
        hook_text="Watch this",
        captions=False,
    )
    assert "Style: Hook," in ass
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,Hook" in ass
    assert ",Default," not in ass
