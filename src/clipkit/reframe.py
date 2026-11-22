from __future__ import annotations

import math
import os
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from clipkit.errors import DependencyError
from clipkit.media import center_crop_box, probe_media
from clipkit.util import clamp

# Ignore faces smaller than this fraction of the frame height. On a 1080p
# stage wide shot the speaker's face is ~15-20% of the height; background
# audience faces and logo false-positives are usually <5%.
MIN_FACE_HEIGHT_RATIO = 0.035
# A merged single-speaker path is only trusted when it covers this fraction of
# the sampled clip. Otherwise the caller's static crop center is safer.
MIN_TRACK_COVERAGE = 0.2


@dataclass
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    descriptor: np.ndarray | None = field(default=None, repr=False, compare=False)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def iou(self, other: FaceBox) -> float:
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        overlap = max(0.0, right - left) * max(0.0, bottom - top)
        union = self.area + other.area - overlap
        return overlap / union if union > 0 else 0.0


@dataclass
class Track:
    id: int
    boxes: list[tuple[float, FaceBox]] = field(default_factory=list)

    @property
    def first_t(self) -> float:
        return self.boxes[0][0]

    @property
    def last_t(self) -> float:
        return self.boxes[-1][0]

    @property
    def duration(self) -> float:
        return max(0.0, self.last_t - self.first_t)

    @property
    def hits(self) -> int:
        return len(self.boxes)

    def median_x(self) -> float:
        return statistics.median(box.cx for _, box in self.boxes)

    def median_y(self) -> float:
        return statistics.median(box.cy for _, box in self.boxes)

    @property
    def median_area(self) -> float:
        return statistics.median(box.area for _, box in self.boxes)

    @property
    def median_height(self) -> float:
        return statistics.median(box.h for _, box in self.boxes)

    @property
    def prominence(self) -> float:
        """Screen time × face size: a proxy for who the shot is about."""
        return self.hits * max(1.0, self.median_area)


class TrackSummary(BaseModel):
    id: int
    coverage: float
    duration: float
    hits: int
    center_x: float
    center_y: float
    prominence: float = 0.0


class ReframePlan(BaseModel):
    layout: Literal["single", "split", "center"] = "center"
    static_center_x: float | None = None
    panel_boxes: list[list[int]] = Field(default_factory=list)
    panel_paths: list[list[list[float]]] = Field(default_factory=list)
    crop_path: list[list[float]] = Field(default_factory=list)
    tracks: list[TrackSummary] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def used_split(self) -> bool:
        return self.layout == "split" and len(self.panel_boxes) == 2


# --------------------------------------------------------------------------- detectors
_CASCADE = None
_YUNET = None
_YUNET_SIZE: tuple[int, int] | None = None


def _yunet_model_path() -> Path | None:
    custom = os.environ.get("CLIPKIT_YUNET_MODEL")
    if custom:
        path = Path(custom).expanduser()
        return path if path.exists() else None
    path = Path(__file__).parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
    return path if path.exists() else None


def detector_status() -> dict:
    requested = os.environ.get("CLIPKIT_FACE_DETECTOR", "auto").lower()
    model = _yunet_model_path()
    if requested == "haar":
        backend = "haar"
    elif model is not None:
        backend = "yunet"
    else:
        backend = "haar"
    return {
        "requested": requested,
        "backend": backend,
        "yunet_model": str(model) if model else None,
    }


def _cascade():
    global _CASCADE
    if _CASCADE is not None:
        return _CASCADE
    try:
        import cv2
    except ImportError as exc:
        raise DependencyError("face tracking needs opencv — run `uv pip install 'clipkit[faces]'`") from exc
    _CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if _CASCADE.empty():
        raise DependencyError("OpenCV Haar cascade could not be loaded")
    return _CASCADE


def _yunet(width: int, height: int):
    global _YUNET, _YUNET_SIZE
    if os.environ.get("CLIPKIT_FACE_DETECTOR", "auto").lower() == "haar":
        return None
    path = _yunet_model_path()
    if path is None:
        return None
    import cv2

    if _YUNET is None:
        _YUNET = cv2.FaceDetectorYN.create(
            str(path),
            "",
            (width, height),
            # Keep low-confidence detections: ByteTrack's second association
            # stage uses them to keep a track alive through a bad frame.
            score_threshold=0.1,
            nms_threshold=0.3,
            top_k=5000,
        )
        _YUNET_SIZE = (width, height)
    elif _YUNET_SIZE != (width, height):
        _YUNET.setInputSize((width, height))
        _YUNET_SIZE = (width, height)
    return _YUNET


def detect_faces(frame, *, max_width: int = 640, min_size: int = 24) -> list[FaceBox]:
    """Detect faces in a BGR frame. YuNet when available, Haar as fallback."""
    import cv2

    height, width = frame.shape[:2]
    scale = min(1.0, max_width / max(1, width))
    if scale < 1.0:
        small = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    else:
        small = frame

    detector = _yunet(small.shape[1], small.shape[0])
    if detector is not None:
        try:
            _ok, faces = detector.detect(small)
            if faces is not None:
                return sorted(
                    [
                        FaceBox(
                            x=float(face[0]) / scale,
                            y=float(face[1]) / scale,
                            w=float(face[2]) / scale,
                            h=float(face[3]) / scale,
                            score=float(face[-1]),
                        )
                        for face in faces
                    ],
                    key=lambda box: box.area,
                    reverse=True,
                )
            return []
        except Exception:  # noqa: BLE001 - fall through to Haar
            pass

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = _cascade().detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(min_size, min_size),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    boxes = [
        FaceBox(
            x=float(x) / scale,
            y=float(y) / scale,
            w=float(w) / scale,
            h=float(h) / scale,
        )
        for x, y, w, h in faces
    ]
    return sorted(boxes, key=lambda box: box.area, reverse=True)


def _descriptor(frame, box: FaceBox) -> np.ndarray | None:
    """Cheap appearance embedding: normalized 32x32 grayscale + hue histogram.

    This is not a face-recognition network. It is enough to keep two talk-show
    hosts apart through a short occlusion, which is the re-ID problem here.
    """
    import cv2

    height, width = frame.shape[:2]
    pad_x = 0.12 * box.w
    pad_y = 0.12 * box.h
    left = int(max(0, box.x - pad_x))
    top = int(max(0, box.y - pad_y))
    right = int(min(width, box.x + box.w + pad_x))
    bottom = int(min(height, box.y + box.h + pad_y))
    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gray = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    gray = gray.flatten()
    gray = gray - gray.mean()
    norm = float(np.linalg.norm(gray))
    if norm > 1e-6:
        gray /= norm
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hue = cv2.calcHist([hsv], [0], None, [32], [0, 180]).flatten().astype(np.float32)
    hue_norm = float(np.linalg.norm(hue))
    if hue_norm > 1e-6:
        hue /= hue_norm
    return np.concatenate([gray, hue * 0.5]).astype(np.float32)


def _plausible_face(box: FaceBox, frame_height: int, min_height_ratio: float) -> bool:
    """Reject tiny background faces and implausible face-box aspect ratios."""
    if box.h < max(20.0, frame_height * min_height_ratio):
        return False
    aspect = box.w / max(1.0, box.h)
    return 0.45 <= aspect <= 2.0


def sample_faces(
    video: Path,
    *,
    start: float,
    duration: float,
    sample_fps: float = 4.0,
    max_samples: int = 600,
) -> list[tuple[float, list[FaceBox]]]:
    """Sample frames from a clip and detect faces. Times are relative to the clip.

    Frames are decoded in one sequential ffmpeg pass. The old implementation
    seeked for every sample, which re-decoded the same GOP repeatedly and made
    cut-heavy clips very slow.
    """
    try:
        import cv2  # noqa: F401 - validate the optional dependency up front
    except ImportError as exc:
        raise DependencyError("face tracking needs opencv — run `uv pip install 'clipkit[faces]'`") from exc

    if duration <= 0:
        return []
    if width is None or height is None:
        info = probe_media(video)
        width, height = info.width, info.height
    if width <= 0 or height <= 0:
        return []

    sample_fps = max(0.5, sample_fps)
    count = min(max_samples, max(2, int(math.ceil(duration * sample_fps))))
    step = duration / count
    effective_fps = count / duration
    frame_bytes = width * height * 3
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-i",
        str(video),
        "-t",
        f"{duration:.3f}",
        "-vf",
        f"fps={effective_fps:.6f},scale={width}:{height}:flags=fast_bilinear",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-",
    ]
    samples: list[tuple[float, list[FaceBox]]] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        for index in range(count):
            at = start + (index + 0.5) * step
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            # Prefer the decoder's actual timestamp; seeking is approximate on
            # VFR sources. Fall back to the sampling grid when unavailable.
            actual = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            relative = actual - start if actual > 0 else index * step
            boxes = detect_faces(frame)
            for box in boxes:
                box.descriptor = _descriptor(frame, box)
            samples.append((round(index * step, 3), boxes))
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return samples


# --------------------------------------------------------------------------- tracking
class _KalmanFilter:
    """Constant-velocity Kalman filter on (cx, cy, w, h)."""

    def __init__(self, box: FaceBox) -> None:
        self.x = np.array(
            [box.cx, box.cy, max(2.0, box.w), max(2.0, box.h), 0.0, 0.0, 0.0, 0.0],
            dtype=float,
        )
        self.F = np.eye(8)
        for index in range(4):
            self.F[index, index + 4] = 1.0
        self.H = np.zeros((4, 8))
        self.H[:4, :4] = np.eye(4)
        self.P = np.eye(8) * 100.0
        self.P[4:, 4:] *= 10.0
        self.Q = np.eye(8)
        self.Q[:4, :4] *= 1.0
        self.Q[4:, 4:] *= 0.01
        self.R = np.eye(4) * 25.0

    def predict(self) -> FaceBox:
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.box()

    def update(self, box: FaceBox) -> None:
        measurement = np.array(
            [box.cx, box.cy, max(2.0, box.w), max(2.0, box.h)], dtype=float
        )
        residual = measurement - self.H @ self.x
        covariance = self.H @ self.P @ self.H.T + self.R
        gain = self.P @ self.H.T @ np.linalg.inv(covariance)
        self.x = self.x + gain @ residual
        self.P = (np.eye(8) - gain @ self.H) @ self.P

    def box(self) -> FaceBox:
        cx, cy, width, height = self.x[:4]
        width = max(2.0, float(width))
        height = max(2.0, float(height))
        return FaceBox(cx - width / 2, cy - height / 2, width, height)


def _blend_descriptor(
    current: np.ndarray | None, incoming: np.ndarray | None, alpha: float = 0.2
) -> np.ndarray | None:
    if incoming is None:
        return current
    if current is None or current.shape != incoming.shape:
        return incoming
    blended = (1.0 - alpha) * current + alpha * incoming
    norm = float(np.linalg.norm(blended))
    return blended / norm if norm > 1e-6 else incoming


def _descriptor_similarity(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None or left.shape != right.shape:
        return 0.0
    return float(np.dot(left, right))


class _Track:
    def __init__(self, track_id: int, at: float, box: FaceBox) -> None:
        self.id = track_id
        self.kf = _KalmanFilter(box)
        self.boxes: list[tuple[float, FaceBox]] = [(at, box)]
        self.hits = 1
        self.time_since_update = 0
        self.last_t = at
        self.descriptor = box.descriptor

    def predict(self) -> FaceBox:
        self.time_since_update += 1
        return self.kf.predict()

    def update(self, at: float, box: FaceBox) -> None:
        self.kf.update(box)
        self.time_since_update = 0
        self.hits += 1
        self.last_t = at
        self.descriptor = _blend_descriptor(self.descriptor, box.descriptor)
        smoothed = self.kf.box()
        smoothed.score = box.score
        self.boxes.append((at, smoothed))

    def reactivate(self, at: float, box: FaceBox) -> None:
        """Re-attach a lost identity. Reset the motion model to the new box."""
        self.kf = _KalmanFilter(box)
        self.time_since_update = 0
        self.hits += 1
        self.last_t = at
        self.descriptor = _blend_descriptor(self.descriptor, box.descriptor)
        self.boxes.append((at, box))

    def output(self) -> Track:
        return Track(id=self.id, boxes=list(self.boxes))


def _match(
    detections: list[FaceBox],
    predicted: list[FaceBox],
    threshold: float,
) -> list[tuple[int, int]]:
    pairs = []
    for track_index, pred in enumerate(predicted):
        for det_index, det in enumerate(detections):
            score = pred.iou(det)
            if score >= threshold:
                pairs.append((score, track_index, det_index))
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_tracks: set[int] = set()
    used_dets: set[int] = set()
    matches: list[tuple[int, int]] = []
    for _score, track_index, det_index in pairs:
        if track_index in used_tracks or det_index in used_dets:
            continue
        used_tracks.add(track_index)
        used_dets.add(det_index)
        matches.append((track_index, det_index))
    return matches


def _reid_score(
    track: _Track,
    detection: FaceBox,
    *,
    frame_width: int,
    threshold: float,
) -> float | None:
    similarity = _descriptor_similarity(track.descriptor, detection.descriptor)
    if similarity < threshold:
        return None
    last = track.boxes[-1][1]
    distance = math.hypot(detection.cx - last.cx, detection.cy - last.cy)
    max_distance = max(0.25 * frame_width, 3.0 * max(last.w, detection.w))
    if distance > max_distance:
        return None
    return similarity - 0.1 * (distance / max(1, frame_width))


def associate_tracks(
    samples: list[tuple[float, list[FaceBox]]],
    *,
    iou_threshold: float = 0.3,
    low_iou_threshold: float = 0.15,
    max_gap: float = 0.8,
    min_hits: int = 3,
    sample_fps: float = 4.0,
    scene_changes: list[float] | None = None,
    max_lost: float = 5.0,
    reid_threshold: float = 0.5,
    frame_width: int = 1920,
) -> list[Track]:
    """ByteTrack-style association + appearance re-ID through occlusion.

    Active tracks are matched with Kalman predictions. Tracks that miss
    `max_gap` seconds move to a lost gallery for up to `max_lost` seconds. A
    new detection can re-attach to a lost identity using a normalized face
    descriptor plus a spatial gate, preserving the track ID across occlusion.
    Scene cuts clear the gallery so identities do not leak across shots.
    """
    active: list[_Track] = []
    lost: list[_Track] = []
    finished: list[_Track] = []
    next_id = 0
    max_gap_frames = max(2, int(round(max_gap * sample_fps)))
    cuts = sorted(scene_changes or [])
    previous_t = -1.0

    for at, detections in samples:
        if any(previous_t < cut <= at for cut in cuts):
            finished.extend(active)
            finished.extend(lost)
            active = []
            lost = []
        previous_t = at

        predicted = [track.predict() for track in active]
        high = [box for box in detections if box.score >= 0.5]
        low = [box for box in detections if 0.1 <= box.score < 0.5]

        high_matches = _match(high, predicted, iou_threshold)
        matched_tracks = {track_index for track_index, _ in high_matches}
        matched_high = {det_index for _, det_index in high_matches}
        for track_index, det_index in high_matches:
            active[track_index].update(at, high[det_index])

        remaining = [index for index in range(len(active)) if index not in matched_tracks]
        if remaining and low:
            low_matches = _match(
                low, [predicted[index] for index in remaining], low_iou_threshold
            )
            for local_track, det_index in low_matches:
                active[remaining[local_track]].update(at, low[det_index])

        # Re-identify unmatched high-confidence detections against the lost
        # gallery before spawning a new ID.
        unmatched_high = [
            index for index in range(len(high)) if index not in matched_high
        ]
        reid_pairs = []
        for det_index in unmatched_high:
            for lost_index, track in enumerate(lost):
                score = _reid_score(
                    track,
                    high[det_index],
                    frame_width=frame_width,
                    threshold=reid_threshold,
                )
                if score is not None:
                    reid_pairs.append((score, lost_index, det_index))
        reid_pairs.sort(key=lambda item: item[0], reverse=True)
        used_lost: set[int] = set()
        used_high = set(matched_high)
        for _score, lost_index, det_index in reid_pairs:
            if lost_index in used_lost or det_index in used_high:
                continue
            track = lost[lost_index]
            track.reactivate(at, high[det_index])
            active.append(track)
            used_lost.add(lost_index)
            used_high.add(det_index)
        lost = [track for index, track in enumerate(lost) if index not in used_lost]

        for det_index, box in enumerate(high):
            if det_index in used_high:
                continue
            active.append(_Track(next_id, at, box))
            next_id += 1

        still_active: list[_Track] = []
        for track in active:
            if track.time_since_update > max_gap_frames:
                if at - track.last_t <= max_lost:
                    lost.append(track)
                else:
                    finished.append(track)
            else:
                still_active.append(track)
        active = still_active

        still_lost: list[_Track] = []
        for track in lost:
            if at - track.last_t <= max_lost:
                still_lost.append(track)
            else:
                finished.append(track)
        lost = still_lost

    finished.extend(active)
    finished.extend(lost)
    tracks: list[Track] = []
    for track in finished:
        output = track.output()
        if output.hits >= min_hits and output.duration >= 0.4:
            tracks.append(output)
    return sorted(tracks, key=lambda track: track.first_t)


def merge_tracks(tracks: list[Track], *, duration: float) -> Track:
    """Merge per-shot face tracks into one largest-face-per-sample path.

    A cut-heavy talk can produce a separate track for every camera angle. Picking
    one track makes the crop hold a stale position whenever that angle is off
    screen. Instead, take the largest plausible face at each sampled time so the
    crop follows the speaker across cuts.
    """
    by_time: dict[float, FaceBox] = {}
    for track in tracks:
        for at, box in track.boxes:
            key = round(at, 3)
            previous = by_time.get(key)
            if previous is None or box.area > previous.area:
                by_time[key] = box
    points = [(at, by_time[at]) for at in sorted(by_time)]
    if not points:
        return Track(id=-1)
    if points[0][0] > 0:
        points.insert(0, (0.0, points[0][1]))
    if points[-1][0] < duration:
        points.append((duration, points[-1][1]))
    return Track(id=-1, boxes=points)


# --------------------------------------------------------------------------- crop plans
def build_crop_path(
    track: Track,
    *,
    duration: float,
    width: int,
    height: int,
    out_width: int = 1080,
    out_height: int = 1920,
    scene_changes: list[float] | None = None,
    alpha: float = 0.3,
    deadzone: float = 0.04,
) -> list[list[float]]:
    """Smooth a face track into (time, crop-center-x) keyframes, resetting at cuts."""
    if not track.boxes:
        return []
    _crop_x, _crop_y, crop_w, _crop_h = center_crop_box(
        width, height, out_width, out_height
    )
    min_center = crop_w / 2
    max_center = max(min_center, width - crop_w / 2)
    deadzone_px = max(1.0, deadzone * width)
    cuts = sorted(scene_changes or [])

    points = sorted(track.boxes, key=lambda item: item[0])
    if points[0][0] > 0:
        points.insert(0, (0.0, points[0][1]))
    if points[-1][0] < duration:
        points.append((duration, points[-1][1]))

    smoothed = clamp(points[0][1].cx, min_center, max_center)
    path: list[list[float]] = [[0.0, round(smoothed, 2)]]
    cut_index = 0
    for at, box in points[1:]:
        while cut_index < len(cuts) and cuts[cut_index] <= at:
            cut_index += 1
        target = clamp(box.cx, min_center, max_center)
        if cut_index > 0 and cuts[cut_index - 1] >= path[-1][0]:
            smoothed = target
        elif abs(target - smoothed) > deadzone_px:
            smoothed += alpha * (target - smoothed)
        smoothed = clamp(smoothed, min_center, max_center)
        path.append([round(at, 3), round(smoothed, 2)])
    return path


def _panel_box(center_x: float, center_y: float, width: int, height: int) -> list[int]:
    # A 9:16 panel crop, letterboxed with a blurred fill inside the 1080x960
    # output. This is narrow enough to actually track a moving speaker; a 9:8
    # crop is nearly two-thirds of a 16:9 frame and clamps against the edges.
    x, _y, w, h = center_crop_box(width, height, 1080, 1920, center_x=center_x)
    max_y = max(0, height - h)
    y = int(clamp(center_y - h / 2, 0, max_y))
    return [int(x), int(y), int(w), int(h)]


def plan_reframe(
    video: Path,
    *,
    start: float,
    duration: float,
    media=None,
    scene_changes: list[float] | None = None,
    layout: str = "auto",
    sample_fps: float = 4.0,
    min_split_duration: float = 2.0,
) -> ReframePlan:
    """Decide single vs split-screen and build crop paths.

    Split-screen requires two persistent, horizontally separated tracks that
    overlap in time for at least `min_split_duration`. That is the hysteresis:
    a brief second face (someone walking behind the speaker) does not flip it.
    """
    info = media or probe_media(video)
    samples = sample_faces(
        video,
        start=start,
        duration=duration,
        sample_fps=sample_fps,
        width=info.width,
        height=info.height,
    )
    tracks = associate_tracks(
        samples,
        sample_fps=sample_fps,
        scene_changes=scene_changes,
        frame_width=info.width,
    )
    total_samples = max(1.0, duration * sample_fps)

    summaries = [
        TrackSummary(
            id=track.id,
            coverage=round(min(1.0, track.hits / total_samples), 3),
            duration=round(track.duration, 3),
            hits=track.hits,
            center_x=round(track.median_x(), 1),
            center_y=round(track.median_y(), 1),
            prominence=round(track.prominence, 1),
        )
        for track in tracks
    ]

    if not tracks:
        return ReframePlan(layout="center", notes=["no faces detected; center crop"])

    min_face_height = info.height * MIN_FACE_HEIGHT_RATIO
    persistent = [
        track
        for track in tracks
        if track.hits >= 3
        and track.duration >= 0.5
        and track.median_height >= min_face_height
    ]
    eligible = [
        track
        for track in persistent
        if track.hits / total_samples >= 0.25 and track.duration >= min_split_duration
    ]
    eligible.sort(key=lambda track: track.prominence, reverse=True)
    split_note: str | None = None

    if layout in ("auto", "split") and len(eligible) >= 2:
        pair = eligible[:2]
        left, right = sorted(pair, key=lambda track: track.median_x())
        separation = abs(left.median_x() - right.median_x())
        overlap = min(left.last_t, right.last_t) - max(left.first_t, right.first_t)
        if separation >= 0.18 * max(1, info.width) and overlap >= min_split_duration:
            panels = [
                _panel_box(left.median_x(), left.median_y(), info.width, info.height),
                _panel_box(right.median_x(), right.median_y(), info.width, info.height),
            ]
            panel_paths = [
                build_crop_path(
                    left,
                    duration=duration,
                    width=info.width,
                    height=info.height,
                    out_width=1080,
                    out_height=1920,
                    scene_changes=scene_changes,
                ),
                build_crop_path(
                    right,
                    duration=duration,
                    width=info.width,
                    height=info.height,
                    out_width=1080,
                    out_height=1920,
                    scene_changes=scene_changes,
                ),
            ]
            return ReframePlan(
                layout="split",
                panel_boxes=panels,
                panel_paths=panel_paths,
                tracks=summaries,
                notes=[
                    (
                        f"{len(eligible)} persistent faces; showing top 2 by prominence"
                        if len(eligible) > 2
                        else "2 persistent face tracks"
                    ),
                    f"horizontal separation {separation:.0f}px",
                    "two-panel split-screen with tracked panels",
                ],
            )
        split_note = "multiple faces detected but not enough co-temporal separation for split-screen"

    # Persistence, not split eligibility, is the right pool for a single crop:
    # a speaker can produce a separate sub-25%-coverage track per camera angle.
    pool = persistent
    if not pool:
        return ReframePlan(
            layout="center",
            tracks=summaries,
            notes=["no persistent face track; center crop"],
        )

    merged = merge_tracks(pool, duration=duration)
    coverage = min(1.0, merged.hits / total_samples)
    if not merged.boxes or merged.duration < 0.5 or coverage < MIN_TRACK_COVERAGE:
        return ReframePlan(
            layout="center",
            tracks=summaries,
            notes=[f"low-confidence face track ({coverage:.0%} coverage); center crop"],
        )

    notes = [f"single tracked path ({coverage:.0%} coverage)"]
    if split_note:
        notes.append(split_note)
    elif len(eligible) > 1:
        notes.append("multiple faces detected but not enough co-temporal separation for split-screen")
    if layout == "split":
        notes.append("split requested; using a single merged face path")

    return ReframePlan(
        layout="single",
        static_center_x=round(merged.median_x(), 2),
        crop_path=build_crop_path(
            merged,
            duration=duration,
            width=info.width,
            height=info.height,
            scene_changes=scene_changes,
        ),
        tracks=summaries,
        notes=notes,
    )


def detect_face_center_x(
    video: Path,
    *,
    start: float,
    duration: float,
    samples: int = 7,
) -> float | None:
    """Backward-compatible helper: median dominant face center for a clip."""
    plan = plan_reframe(
        video,
        start=start,
        duration=duration,
        layout="single",
        sample_fps=max(1.0, samples / max(0.1, duration)),
    )
    return plan.static_center_x
