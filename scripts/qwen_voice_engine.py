"""Offline Qwen 0.6B CustomVoice engine for the isolated voice interpreter.

This module deliberately imports no ML packages at module import time. The main
AI Pet interpreter must not load it as a voice backend: a separate interpreter
owns the engine, CUDA context and model. No model installation happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any
import wave


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
REQUIRED_FILES = (
    "config.json", "generation_config.json", "model.safetensors",
    "tokenizer_config.json", "vocab.json", "merges.txt",
    "preprocessor_config.json", "speech_tokenizer/config.json",
    "speech_tokenizer/model.safetensors",
    "speech_tokenizer/preprocessor_config.json",
)
MAX_TEXT_CHARACTERS = 240
MAX_AUDIO_SECONDS = 180


def enable_offline_environment() -> None:
    """Set (not setdefault) offline flags before importing HF or Transformers."""
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE",
                 "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        os.environ[name] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


def validate_model_directory(model_dir: str | Path) -> Path:
    """Reject hub identifiers, another variant and incomplete local downloads."""
    if not isinstance(model_dir, (str, Path)) or not str(model_dir).strip():
        raise ValueError("A local model directory is required; hub IDs are not accepted.")
    raw = str(model_dir)
    if raw.replace("\\", "/").startswith("//") or "://" in raw or not Path(raw).is_absolute():
        raise ValueError("Only absolute local model directories are accepted.")
    path = Path(model_dir).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("The model path must be a local directory.")
    config_file = path / "config.json"
    if not config_file.is_file() or config_file.stat().st_size > 1024 * 1024:
        raise ValueError("Missing or invalid local model config.json.")
    with config_file.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    expected = {
        "model_type": "qwen3_tts",
        "tokenizer_type": "qwen3_tts_tokenizer_12hz",
        "tts_model_size": "0b6",
        "tts_model_type": "custom_voice",
    }
    if not isinstance(config, dict) or any(config.get(k) != v for k, v in expected.items()):
        raise ValueError("Only Qwen3-TTS-12Hz-0.6B-CustomVoice is allowed in this phase.")
    missing = [name for name in REQUIRED_FILES
               if not (path / name).is_file() or (path / name).stat().st_size == 0]
    if missing:
        raise ValueError("The local model is incomplete: " + ", ".join(missing))
    for name in REQUIRED_FILES:
        if not (path / name).resolve(strict=True).is_relative_to(path):
            raise ValueError(f"A model resource resolves outside the model directory: {name}")
    codec_config = path / "speech_tokenizer/config.json"
    if codec_config.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid speech tokenizer config size.")
    with codec_config.open("r", encoding="utf-8") as handle:
        codec = json.load(handle)
    if not isinstance(codec, dict) or codec.get("model_type") != "qwen3_tts_tokenizer_12hz":
        raise ValueError("The speech tokenizer is not the required Qwen 12Hz model.")
    # A Git LFS pointer is a text placeholder, not downloaded model weights.
    for name in ("model.safetensors", "speech_tokenizer/model.safetensors"):
        file = path / name
        if file.stat().st_size < 1024:
            raise ValueError(f"Model weights are incomplete or an LFS pointer: {name}")
    return path


def validate_request(text: str, voice_id: str, language: str, speed: float) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Speech text must not be empty.")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ValueError("Speech text is too long; split it into sentences first.")
    if voice_id != "Serena":
        raise ValueError("This phase only enables the Serena preset voice.")
    if language != "Chinese":
        raise ValueError("This phase uses Chinese (including Chinese/English mixed text).")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ValueError("Speed must be a finite number between 0.5 and 2.0.")
    if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError("Speed must be a finite number between 0.5 and 2.0.")


@dataclass(frozen=True, slots=True)
class GeneratedAudio:
    pcm16: bytes
    sample_rate: int
    duration_seconds: float
    elapsed_seconds: float
    statistics: dict[str, Any]

    def write_wav(self, output: Path) -> None:
        """Write a standard mono PCM16 WAV, never a model-specific object."""
        with wave.open(str(output), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(self.pcm16)


class QwenVoiceEngine:
    """One lazy model instance, sequential batch-one synthesis; no emotion API."""

    supports_emotion = False
    supports_reference_audio = False

    def __init__(self, model_dir: str | Path, *, device: str = "cuda:0") -> None:
        if not isinstance(device, str) or not re.fullmatch(r"cuda:\d+|cpu", device):
            raise ValueError("Device must be cuda:N, or cpu for an explicit manual test.")
        self.model_dir = validate_model_directory(model_dir)
        self.device = device
        self.model: Any = None
        self.torch: Any = None
        self.load_seconds: float | None = None

    def synchronize(self) -> None:
        if self.torch is not None and self.device.startswith("cuda:"):
            self.torch.cuda.synchronize(self.device)

    def load(self) -> float:
        if self.model is not None:
            return self.load_seconds or 0.0
        enable_offline_environment()
        started = time.perf_counter()
        import torch
        from qwen_tts import Qwen3TTSModel

        torch.set_num_threads(4)
        # Avoid competing with the desktop for every logical CPU core. This can
        # only be set before inter-op work starts in a fresh runtime process.
        try:
            torch.set_num_interop_threads(2)
        except RuntimeError:
            pass
        if self.device.startswith("cuda:") and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the isolated voice runtime.")
        self.torch = torch
        dtype = torch.bfloat16 if self.device.startswith("cuda:") else torch.float32
        self.model = Qwen3TTSModel.from_pretrained(
            str(self.model_dir), device_map=self.device, dtype=dtype,
            attn_implementation="sdpa", local_files_only=True,
        )
        self.synchronize()
        self.load_seconds = time.perf_counter() - started
        return self.load_seconds

    def reset_gpu_peak(self) -> None:
        if self.torch is not None and self.device.startswith("cuda:"):
            self.synchronize()
            self.torch.cuda.reset_peak_memory_stats(self.device)

    def gpu_memory(self) -> dict[str, int] | None:
        if self.torch is None or not self.device.startswith("cuda:"):
            return None
        return {
            "allocated_bytes": self.torch.cuda.memory_allocated(self.device),
            "reserved_bytes": self.torch.cuda.memory_reserved(self.device),
            "peak_allocated_bytes": self.torch.cuda.max_memory_allocated(self.device),
            "peak_reserved_bytes": self.torch.cuda.max_memory_reserved(self.device),
        }

    def synthesize(self, text: str, *, voice_id: str = "Serena",
                   language: str = "Chinese", speed: float = 1.0) -> GeneratedAudio:
        validate_request(text, voice_id, language, speed)
        self.load()
        import numpy as np

        self.synchronize()
        started = time.perf_counter()
        with self.torch.inference_mode():
            # Deliberately no instruct/emotion/reference argument. 0.6B does not
            # implement the 1.7B emotion instruction feature. A string is batch 1.
            wavs, sample_rate = self.model.generate_custom_voice(
                text=text, language=language, speaker=voice_id,
                max_new_tokens=2048,
            )
        self.synchronize()
        inference_seconds = time.perf_counter() - started
        if len(wavs) != 1:
            raise RuntimeError("Qwen returned an unexpected batch size.")
        if isinstance(sample_rate, bool) or not 8000 <= int(sample_rate) <= 192000:
            raise RuntimeError("Qwen returned an invalid audio sample rate.")
        sample_rate = int(sample_rate)
        samples = np.asarray(wavs[0], dtype=np.float32)
        if samples.ndim != 1 or samples.size == 0:
            raise RuntimeError("Qwen must return a nonempty mono waveform.")
        if samples.size > sample_rate * MAX_AUDIO_SECONDS:
            raise RuntimeError("Qwen returned an excessively long waveform.")
        if not np.isfinite(samples).all():
            raise RuntimeError("Qwen returned NaN or infinite audio samples.")
        post_started = time.perf_counter()
        if speed != 1.0:
            # Qwen has no native rate argument. Optional CPU phase-vocoder
            # postprocessing changes duration while preserving pitch; report it
            # honestly, and do not label it native model prosody control.
            from librosa.effects import time_stretch
            samples = time_stretch(samples, rate=float(speed))
            if not np.isfinite(samples).all():
                raise RuntimeError("Speed postprocessing produced invalid audio.")
        peak = float(np.max(np.abs(samples)))
        clipped_samples = int(np.count_nonzero(np.abs(samples) >= 1.0))
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        pcm16 = np.rint(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        statistics = {
            "sample_count": int(samples.size), "all_samples_finite": True,
            "peak_absolute_amplitude": peak, "rms_amplitude": rms,
            "clipped_sample_count": clipped_samples,
            "clipped_sample_fraction": clipped_samples / int(samples.size),
            "inference_seconds": inference_seconds,
            "postprocessing_seconds": time.perf_counter() - post_started,
            "speed": float(speed),
            "speed_method": "none" if speed == 1.0 else "librosa_phase_vocoder",
            "auditory_review": "required; numerical checks cannot verify naturalness or absence of audible distortion",
        }
        return GeneratedAudio(
            pcm16=pcm16, sample_rate=sample_rate,
            duration_seconds=int(samples.size) / sample_rate,
            elapsed_seconds=time.perf_counter() - started, statistics=statistics,
        )
