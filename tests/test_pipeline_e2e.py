import json
import shutil
import subprocess

import pytest

from clipkit.media import contact_sheet, probe_media, render_clip
from clipkit.models import RenderOptions
from clipkit.pipeline import (
    ensure_audio,
    ensure_candidates,
    ensure_features,
    ensure_probe,
    ensure_transcript,
    render_candidates,
)
from clipkit.workspace import Workspace


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_full_pipeline_with_mock_transcript(tmp_path):
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )

    texts = [
        "The biggest mistake founders make is ignoring the obvious signal.",
        "They chase a new feature instead of talking to ten users this week.",
        "That sounds boring but it is the fastest way to find product market fit.",
        "Most teams learn this lesson after burning a year and a lot of money.",
        "So here is the rule I use before building anything new.",
        "Talk to users first and the roadmap writes itself.",
    ]
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        json.dumps(
            {
                "language": "en",
                "segments": [
                    {"start": index * 4.0, "end": index * 4.0 + 3.95, "text": text}
                    for index, text in enumerate(texts)
                ],
            }
        ),
        encoding="utf-8",
    )

    ws = Workspace.create(tmp_path / "ws", video)
    info = ensure_probe(ws)
    assert info.duration > 20
    ensure_audio(ws)
    transcript = ensure_transcript(ws, backend="mock", transcript_path=transcript_path)
    assert transcript.word_count() > 20

    features = ensure_features(ws, transcript=transcript)
    assert features.duration > 20
    assert features.speech_regions

    candidates = ensure_candidates(ws, min_seconds=10, max_seconds=20, top_n=3)
    assert candidates

    results = render_candidates(
        ws,
        candidates[:1],
        options=RenderOptions(layout="crop", captions=True, normalize_audio=True),
        force=True,
    )
    assert len(results) == 1
    result = results[0]
    clip = ws.root / result.path
    ass = ws.root / result.ass_path
    assert clip.exists()
    assert ass.exists()
    assert "Dialogue:" in ass.read_text(encoding="utf-8")

    output = probe_media(clip)
    assert (output.width, output.height) == (1080, 1920)
    assert 9 <= output.duration <= 21
    assert output.has_audio

    # A second render without --force must report the existing clip as skipped
    # instead of counting it as freshly rendered.
    again = render_candidates(
        ws,
        candidates[:1],
        options=RenderOptions(layout="crop", captions=True, normalize_audio=True),
        force=False,
    )
    assert again[0].skipped is True
    assert again[0].id == result.id

    # Hook banner text is written to the ASS file and recorded on the clip.
    hooked = render_candidates(
        ws,
        candidates[:1],
        options=RenderOptions(
            layout="crop",
            captions=True,
            normalize_audio=False,
            hooks={candidates[0].id: "Watch this before your next talk"},
        ),
        force=True,
    )
    assert hooked[0].hook_text == "Watch this before your next talk"
    assert "Style: Hook," in (ws.root / hooked[0].ass_path).read_text(encoding="utf-8")
    assert "WATCH THIS BEFORE YOUR NEXT TALK" in (ws.root / hooked[0].ass_path).read_text(encoding="utf-8")

    # Blur-pad layout must also resolve to 1080x1920 (a mapping bug once
    # silently returned the source resolution).
    blur = render_candidates(
        ws,
        candidates[:1],
        options=RenderOptions(layout="blur", captions=True, normalize_audio=False),
        force=True,
    )
    blur_output = probe_media(ws.root / blur[0].path)
    assert (blur_output.width, blur_output.height) == (1080, 1920)

    sheet = contact_sheet(ws.root / blur[0].path, ws.root / "nested" / "inspect" / "clip.jpg")
    assert sheet.exists()

    # Split-screen renderer: two stacked panels, still 1080x1920.
    split_path = ws.root / "clips" / "split_test.mp4"
    render_clip(
        video,
        split_path,
        start=0,
        duration=4,
        layout="split",
        panels=[[0, 0, 405, 360], [235, 0, 405, 360]],
        has_audio=True,
        source_width=640,
        source_height=360,
        normalize_audio=False,
    )
    split_output = probe_media(split_path)
    assert (split_output.width, split_output.height) == (1080, 1920)

    # Dynamic split panels: two labeled crop filters, each with its own sendcmd.
    dynamic_split_path = ws.root / "clips" / "dynamic_split_test.mp4"
    render_clip(
        video,
        dynamic_split_path,
        start=0,
        duration=4,
        layout="split",
        panels=[[0, 0, 405, 360], [235, 0, 405, 360]],
        panel_paths=[[[0.0, 202], [4.0, 202]], [[0.0, 437], [4.0, 437]]],
        has_audio=True,
        source_width=640,
        source_height=360,
        normalize_audio=False,
    )
    dynamic_split_output = probe_media(dynamic_split_path)
    assert (dynamic_split_output.width, dynamic_split_output.height) == (1080, 1920)
    p0_commands = (ws.root / "clips" / "dynamic_split_test.p0.cmd").read_text(encoding="utf-8")
    p1_commands = (ws.root / "clips" / "dynamic_split_test.p1.cmd").read_text(encoding="utf-8")
    assert "crop@panel0 x" in p0_commands
    assert "crop@panel1 x" in p1_commands

    # Dynamic crop path via sendcmd.
    dynamic_path = ws.root / "clips" / "dynamic_test.mp4"
    render_clip(
        video,
        dynamic_path,
        start=0,
        duration=4,
        crop_path=[[0.0, 320], [1.0, 280], [2.0, 360], [4.0, 320]],
        has_audio=True,
        source_width=640,
        source_height=360,
        normalize_audio=False,
    )
    dynamic_output = probe_media(dynamic_path)
    assert (dynamic_output.width, dynamic_output.height) == (1080, 1920)
