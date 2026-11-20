from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

from clipkit.align import align_words_to_ctc, ctc_greedy_words
from clipkit.asr.base import ProgressFn
from clipkit.errors import BackendError
from clipkit.media import read_wav, write_wav
from clipkit.models import Segment, Transcript
from clipkit.text import estimate_word_times
from clipkit.vad import chunk_regions, compute_rms, detect_speech_regions


class RumbleBackend:
    """Reson8/Rumble-2-Flash via Transformers (Apache-2.0, 0.5B, 45 languages).

    Rumble emits text, not word timestamps. The repo ships an auxiliary CTC
    head at 13 fps; when `ctc_align=True` we force-align the LLM transcript
    against those frame posteriors. Otherwise we fall back to proportional
    timing (fast, but visibly less precise in karaoke captions).
    """

    name = "rumble"
    model_id = os.environ.get("CLIPKIT_RUMBLE_MODEL", "Reson8/Rumble-2-Flash")
    frame_rate = 13.0

    def __init__(self, *, ctc_align: bool = False, chunk_seconds: float = 25.0) -> None:
        self.ctc_align = ctc_align
        self.chunk_seconds = chunk_seconds
        self._torch = None
        self._model = None
        self._processor = None
        self._ctc = None
        self._ctc_ok = False

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
        except ImportError as exc:
            raise BackendError(
                "Rumble needs torch + transformers — run `uv pip install 'clipkit[rumble]'`"
            ) from exc

        self._torch = torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype_name = os.environ.get("CLIPKIT_RUMBLE_DTYPE")
        if dtype_name:
            dtype = getattr(torch, dtype_name)
        elif device == "cuda" and torch.cuda.is_bf16_supported():
            dtype = torch.bfloat16
        elif device == "cuda":
            dtype = torch.float16
        else:
            dtype = torch.float32

        try:
            self._processor = AutoProcessor.from_pretrained(self.model_id)
            self._model = AutoModelForMultimodalLM.from_pretrained(self.model_id, dtype=dtype)
            self._model.to(device)
            self._model.eval()
        except Exception as exc:  # noqa: BLE001
            raise BackendError(
                f"could not load {self.model_id}: {exc}\n"
                "If you are offline, pre-download it with `huggingface-cli download "
                f"{self.model_id}`."
            ) from exc

    def _load_ctc(self) -> None:
        if self._ctc is not None:
            return
        self._load()
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise BackendError("huggingface_hub is required for CTC alignment") from exc

        path = hf_hub_download(self.model_id, "ctc_head.py")
        module_name = "clipkit_rumble_ctc"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise BackendError("could not import ctc_head.py from the model repo")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._ctc = module.RumbleCTC.from_pretrained(self.model_id, self._model, self._processor)

    # ------------------------------------------------------------------ transcribe
    def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        duration: float | None = None,
        progress: ProgressFn | None = None,
    ) -> Transcript:
        self._load()
        assert self._torch is not None and self._model is not None and self._processor is not None
        torch = self._torch

        samples, sample_rate = read_wav(audio)
        total = duration or (samples.size / sample_rate if sample_rate else 0.0)
        if progress:
            progress("transcribe", 0.0, f"loaded {self.model_id}")

        times, rms = compute_rms(samples, sample_rate)
        regions = detect_speech_regions(times, rms)
        if not regions:
            # Music / no-dialogue fallback: let the model look at the audio anyway.
            regions = [[0.0, max(0.5, total)]]
        chunks = chunk_regions(regions, max_seconds=self.chunk_seconds, pad=0.15, duration=total)

        segments: list[Segment] = []
        ctc_used = False
        with tempfile.TemporaryDirectory(prefix="clipkit-rumble-") as tmp:
            for index, (start, end) in enumerate(chunks):
                if end - start < 0.2:
                    continue
                lo = max(0, int(start * sample_rate))
                hi = min(samples.size, int(end * sample_rate))
                if hi <= lo:
                    continue
                chunk_path = Path(tmp) / f"chunk_{index:04d}.wav"
                write_wav(chunk_path, samples[lo:hi], sample_rate)

                text, detected_language = self._transcribe_chunk(
                    chunk_path, language=language, torch=torch
                )
                if not text.strip():
                    continue

                if self.ctc_align:
                    words = self._ctc_words(samples[lo:hi], text, start, end)
                    ctc_used = ctc_used or self._ctc_ok
                else:
                    words = estimate_word_times(text, start, end)
                segments.append(
                    Segment(start=round(start, 3), end=round(end, 3), text=text.strip(), words=words)
                )
                if detected_language and not language:
                    language = detected_language

                if progress:
                    progress(
                        "transcribe",
                        min(1.0, end / total) if total else 0.0,
                        f"transcribed {len(segments)} chunk(s)",
                    )

        alignment = "ctc" if ctc_used else "estimated"
        return Transcript(
            language=language,
            backend="rumble",
            duration=total,
            alignment=alignment,
            segments=segments,
        )

    def _transcribe_chunk(self, chunk: Path, *, language: str | None, torch) -> tuple[str, str | None]:
        assert self._model is not None and self._processor is not None
        try:
            kwargs: dict = {
                "audio": str(chunk),
                # Collapse stutters/repeats ("go go") that the decoder sometimes
                # produces; captions should read cleanly.
                "verbatim": os.environ.get("CLIPKIT_RUMBLE_VERBATIM", "0") == "1",
            }
            if language:
                kwargs["language"] = language
            inputs = self._processor.apply_transcription_request(**kwargs).to(
                self._model.device, dtype=self._model.dtype
            )
            with torch.inference_mode():
                output = self._model.generate(**inputs, max_new_tokens=1024)
            generated = output[:, inputs["input_ids"].shape[-1] :]
            if language:
                text = self._processor.decode(generated, return_format="transcription_only")[0]
                return str(text), language
            parsed = self._processor.decode(generated, return_format="parsed")[0]
            if isinstance(parsed, dict):
                text = parsed.get("transcription") or parsed.get("text") or ""
                return str(text), parsed.get("language")
            return str(parsed), None
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Rumble failed on chunk {chunk.name}: {exc}") from exc

    # ------------------------------------------------------------------ CTC alignment
    def _ctc_words(
        self,
        samples: np.ndarray,
        text: str,
        start: float,
        end: float,
    ) -> list:
        fallback = estimate_word_times(text, start, end)
        try:
            self._load_ctc()
            assert self._ctc is not None and self._torch is not None and self._processor is not None
            ctc = self._ctc
            torch = self._torch
            tokenizer = self._processor.tokenizer

            words = text.split()
            if not words:
                return fallback

            features = ctc.log_mel(samples)
            with torch.inference_mode():
                states = ctc.ln_post_states(features)
                # The full head is 99,631 classes; argmax keeps only the frame
                # peaks, so this is ~65 MB for a 25s chunk.
                class_ids = ctc.logits(states).argmax(dim=-1).cpu().numpy()

            ctc_words = ctc_greedy_words(
                class_ids,
                tokenizer,
                ctc.token_ids,
                ctc.blank,
                frame_rate=self.frame_rate,
            )
            if not ctc_words:
                return fallback
            aligned = align_words_to_ctc(
                words, ctc_words, chunk_start=start, chunk_end=end
            )
            self._ctc_ok = aligned is not None
            return aligned or fallback
        except Exception:  # noqa: BLE001 - alignment is an enhancement, never fatal
            return fallback
