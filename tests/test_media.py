import shutil
import subprocess

import pytest

from clipkit.media import (
    _blur_filter,
    _split_filter,
    _write_crop_commands,
    center_crop_box,
    loudnorm_filter,
    measure_loudness,
)


def test_center_crop_box_horizontal():
    x, y, w, h = center_crop_box(1920, 1080)
    assert (x, y, w, h) == (656, 0, 608, 1080)


def test_center_crop_box_vertical_is_identity():
    assert center_crop_box(1080, 1920) == (0, 0, 1080, 1920)


def test_center_crop_box_four_three():
    assert center_crop_box(1440, 1080) == (416, 0, 608, 1080)


def test_center_crop_box_respects_face_center():
    assert center_crop_box(1920, 1080, center_x=0)[0] == 0
    assert center_crop_box(1920, 1080, center_x=1920)[0] == 1312


def test_blur_and_split_fills_are_dark_and_abstract():
    # A close-up fill used to leave a recognisable half-face in the background.
    # Keep the stronger blur + darkening in place for both layouts.
    blur = _blur_filter(1080, 1920, 30)
    assert "boxblur=80:4" in blur
    assert "eq=brightness=-0.35" in blur

    split = _split_filter(
        [(0, 0, 608, 1080), (1312, 0, 608, 1080)],
        1080,
        1920,
        30,
        None,
    )
    assert "boxblur=80:4" in split
    assert "eq=brightness=-0.35" in split


def test_crop_commands_are_interpolated_and_monotonic(tmp_path):
    path = tmp_path / "crop.cmd"
    _write_crop_commands(
        path,
        [[0.0, 400], [2.0, 600]],
        duration=2.0,
        crop_width=608,
        source_width=1920,
        command_fps=30,
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 60
    x_values = [float(line.split()[-1].rstrip(";")) for line in lines]
    assert x_values == sorted(x_values)
    assert x_values[0] == 96.0  # 400 - 608/2
    assert x_values[-1] == 296.0  # 600 - 608/2


def test_loudnorm_filter_uses_two_pass_measurements():
    measurement = {
        "input_i": "-26.58",
        "input_lra": "5.20",
        "input_tp": "-4.08",
        "input_thresh": "-36.58",
        "target_offset": "4.15",
    }
    value = loudnorm_filter(measurement)
    assert "measured_I=-26.58" in value
    assert "measured_LRA=5.20" in value
    assert "offset=4.15" in value
    assert "linear=true" in value


def test_loudnorm_filter_falls_back_without_measurements():
    assert loudnorm_filter(None) == "loudnorm=I=-14.0:TP=-1.5:LRA=11.0"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_measure_loudness_reads_ffmpeg_analysis(tmp_path):
    tone = tmp_path / "tone.wav"
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
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "3",
            str(tone),
        ],
        check=True,
    )
    measurement = measure_loudness(tone, start=0, duration=3)
    assert measurement is not None
    assert float(measurement["input_i"]) < 0
    assert "input_tp" in measurement
