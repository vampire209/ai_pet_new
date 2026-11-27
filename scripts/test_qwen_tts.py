"""Manual integration smoke. Run ONLY using runtime/voice_env/Scripts/python.exe.

No downloads, API calls, HTTP server, UI hooks or automatic fallback. With
--smoke this loads the one approved local model, generates warmup twice and the
six requested Chinese demos, and saves measured resource data beside the WAVs.
"""

from __future__ import annotations

import argparse
from collections import deque
import csv
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Any

try:
    from .qwen_voice_engine import MODEL_ID, QwenVoiceEngine, enable_offline_environment
except ImportError:
    from qwen_voice_engine import MODEL_ID, QwenVoiceEngine, enable_offline_environment


DEMO_TEXTS = (
    ("01_normal.wav", "你好呀，今天过得怎么样？"),
    ("02_soft.wav", "没关系，慢慢来，我会陪着你的。"),
    ("03_happy.wav", "太好了！这次终于成功啦！"),
    ("04_long.wav", "今天的任务其实不用一下子全部做完。我们先把最重要的一件事情解决，然后再慢慢处理剩下的内容，这样会轻松很多。"),
    ("05_mixed.wav", "今天我们继续学习 Fourier Transform，先从基本性质开始。"),
    ("06_number.wav", "晚上七点提醒我写作业，明天上午十点还有一项任务。"),
)
WARMUP_TEXT = "你好呀，今天也一起加油吧。"


def progress(message: str) -> None:
    print(message, flush=True)


def installed_versions() -> dict[str, str | None]:
    packages = ("torch", "transformers", "qwen-tts", "accelerate", "numpy",
                "soundfile", "librosa", "soxr", "psutil", "huggingface-hub")
    versions: dict[str, str | None] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def model_bytes(model_dir: Path) -> int:
    return sum(file.stat().st_size for file in model_dir.rglob("*") if file.is_file())


def query_gpus() -> list[dict[str, Any]]:
    """Read whole-card use, distinct from this process' PyTorch allocator."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True,
        timeout=3, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    records = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != 5:
            continue
        index, name, total, used, utilization = (cell.strip() for cell in row)
        records.append({
            "index": int(index), "name": name,
            "total_bytes": int(float(total) * 1024 * 1024),
            "used_bytes": int(float(used) * 1024 * 1024),
            "utilization_percent": float(utilization) if utilization != "[N/A]" else None,
        })
    return records


class ResourceSampler:
    """Bounded samples; global peak aggregates are retained even if ring wraps."""

    def __init__(self, interval: float = 0.25, gpu_interval: float = 1.0) -> None:
        import psutil
        self.psutil = psutil
        self.process = psutil.Process()
        self.interval = interval
        self.gpu_interval = gpu_interval
        self.phase = "idle"
        self.samples: deque[dict[str, Any]] = deque(maxlen=20000)
        self.phase_peaks: dict[str, dict[str, Any]] = {}
        self.sample_count = 0
        self.gpu_error: str | None = None
        self.started = time.perf_counter()
        self._last_gpu = float("-inf")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def sample(self, *, include_gpu: bool = True) -> dict[str, Any]:
        virtual = self.psutil.virtual_memory()
        # The system figure includes all applications and file cache according
        # to psutil's platform definition; it is not Qwen's memory footprint.
        item: dict[str, Any] = {
            "seconds_since_start": time.perf_counter() - self.started,
            "phase": self.phase,
            "process_rss_bytes": self.process.memory_info().rss,
            "system_total_bytes": virtual.total,
            "system_used_bytes": virtual.total - virtual.available,
            "system_available_bytes": virtual.available,
        }
        if include_gpu and time.perf_counter() - self._last_gpu >= self.gpu_interval:
            self._last_gpu = time.perf_counter()
            try:
                item["whole_card_gpus"] = query_gpus()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.gpu_error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            self.sample_count += 1
            self.samples.append(item)
            for phase in ("all", item["phase"]):
                peak = self.phase_peaks.setdefault(phase, {
                    "process_rss_bytes": 0, "system_used_bytes": 0,
                    "whole_card_used_bytes_by_index": {},
                })
                for key in ("process_rss_bytes", "system_used_bytes"):
                    peak[key] = max(peak[key], item[key])
                for gpu in item.get("whole_card_gpus", []):
                    index = str(gpu["index"])
                    by_index = peak["whole_card_used_bytes_by_index"]
                    by_index[index] = max(by_index.get(index, 0), gpu["used_bytes"])
        return item

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except (OSError, self.psutil.Error) as exc:
                # Keep a GPU error distinct from a RAM sampling failure.
                with self._lock:
                    self.samples.append({"phase": self.phase, "sample_error": str(exc)})

    def start(self) -> dict[str, Any]:
        idle = self.sample()
        self._thread = threading.Thread(target=self._run, name="qwen-resource-sampler", daemon=True)
        self._thread.start()
        return idle

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ram_sampling_interval_seconds": self.interval,
                "gpu_sampling_interval_seconds": self.gpu_interval,
                "sample_count": self.sample_count,
                "retained_sample_count": len(self.samples),
                "phase_peaks": self.phase_peaks,
                "samples": list(self.samples), "gpu_query_error": self.gpu_error,
                "memory_definitions": {
                    "process_rss_bytes": "Resident working set of the isolated Python process; excludes other programs.",
                    "system_used_bytes": "Physical RAM total minus available, for the entire system, not just Qwen.",
                    "whole_card_gpus": "nvidia-smi whole-GPU memory including desktop and other programs; sampled peak is a lower bound.",
                    "torch_gpu": "CUDA memory allocated/reserved by PyTorch in this process; excludes non-PyTorch allocations.",
                },
            }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "runtime/cache/voice_demo")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--smoke", action="store_true", help="Explicit opt-in to local real-model inference.")
    parser.add_argument("--warmup-only", action="store_true", help="Only two identical short sentences; no six-demo claim.")
    parser.add_argument("--min-free-gib", type=float, default=4.0,
                        help="Required available system RAM before importing/loading the model (default 4 GiB).")
    parser.add_argument("--sample-interval", type=float, default=0.25)
    parser.add_argument("--gpu-sample-interval", type=float, default=1.0)
    parser.add_argument("--play", action="store_true", help="On Windows, play each generated WAV for manual listening.")
    return parser


def save_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not args.smoke:
        parser.error("Real-model execution requires the explicit --smoke switch.")
    if not 0.1 <= args.sample_interval <= 10 or not 0.5 <= args.gpu_sample_interval <= 30:
        parser.error("Invalid sampling intervals (RAM: 0.1..10, GPU: 0.5..30 seconds).")
    if not 0 <= args.min_free_gib <= 128:
        parser.error("--min-free-gib must be finite and between 0 and 128.")
    enable_offline_environment()
    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "smoke_report.json"
    report: dict[str, Any] = {
        "schema_version": 1, "status": "running",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "python_executable": sys.executable,
        "platform": platform.platform(), "dependencies": installed_versions(),
        "model_id": MODEL_ID, "model_directory": str(args.model_dir.resolve()),
        "device": args.device, "voice_id": "Serena", "language": "Chinese",
        "supports_emotion": False, "supports_reference_audio": False,
        "batch_size": 1, "attention_implementation": "sdpa", "torch_threads": 4,
        "minimum_available_ram_gib": args.min_free_gib,
        "offline": True, "audio_files": [],
        "auditory_review": "not performed; listen to WAV files, numerical checks alone are insufficient",
        "desktop_ui_review": "not performed; this script runs independently of AI Pet",
    }
    sampler: ResourceSampler | None = None
    engine: QwenVoiceEngine | None = None
    exit_code = 0
    try:
        progress("Validating the approved local 0.6B CustomVoice model (no downloads)...")
        engine = QwenVoiceEngine(args.model_dir, device=args.device)
        report["model_actual_file_bytes"] = model_bytes(engine.model_dir)
        sampler = ResourceSampler(args.sample_interval, args.gpu_sample_interval)
        report["idle_resources"] = sampler.start()
        sampler.phase = "resource_preflight"
        available_gib = report["idle_resources"]["system_available_bytes"] / (1024 ** 3)
        if available_gib < args.min_free_gib:
            raise RuntimeError(
                f"Only {available_gib:.2f} GiB system RAM available; "
                f"this smoke requires {args.min_free_gib:.2f} GiB headroom. "
                "Save and close unused applications before retrying; model was not imported or loaded."
            )
        sampler.phase = "model_load"
        progress("Loading Serena model in the isolated runtime; first load may take some time...")
        report["model_load_seconds"] = engine.load()
        report["torch_cuda_version"] = engine.torch.version.cuda
        report["dtype"] = "bfloat16" if args.device.startswith("cuda:") else "float32"
        report["after_load_torch_gpu"] = engine.gpu_memory()
        sampler.phase = "loaded_idle"
        report["after_load_resources"] = sampler.sample()
        progress(f"Model loaded in {engine.load_seconds:.3f}s. Generating the first and second identical warmup sentences.")
        jobs = (("00_warmup.wav", WARMUP_TEXT), ("00_warmup_repeat.wav", WARMUP_TEXT))
        if not args.warmup_only:
            jobs += DEMO_TEXTS
        for index, (filename, text) in enumerate(jobs, 1):
            sampler.phase = filename.removesuffix(".wav")
            engine.reset_gpu_peak()
            progress(f"[{index}/{len(jobs)}] {filename}: {text}")
            audio = engine.synthesize(text)
            destination = args.output_dir / filename
            audio.write_wav(destination)
            entry = {
                "file": str(destination), "text": text, "voice_id": "Serena",
                "sample_rate": audio.sample_rate, "channels": 1, "sample_width_bytes": 2,
                "duration_seconds": audio.duration_seconds,
                "generation_seconds": audio.elapsed_seconds,
                "real_time_factor": audio.elapsed_seconds / audio.duration_seconds,
                "audio_statistics": audio.statistics,
                "torch_gpu": engine.gpu_memory(),
                "after_generation_resources": sampler.sample(),
            }
            report["audio_files"].append(entry)
            report["resources"] = sampler.report()
            save_report(report_path, report)
            progress(f"  Saved {audio.duration_seconds:.3f}s audio; generation {audio.elapsed_seconds:.3f}s; clipped fraction {audio.statistics['clipped_sample_fraction']:.6f}")
            if args.play:
                if sys.platform != "win32":
                    raise RuntimeError("--play is only available on Windows; the WAV files can be played separately.")
                import winsound
                winsound.PlaySound(str(destination), winsound.SND_FILENAME)
        report["first_generation_seconds"] = report["audio_files"][0]["generation_seconds"]
        report["second_generation_seconds"] = report["audio_files"][1]["generation_seconds"]
        report["status"] = "warmup_only_needs_listening" if args.warmup_only else "generated_needs_listening"
        report["all_six_demos_generated"] = not args.warmup_only
        report["playback_invoked"] = bool(args.play)
    except BaseException as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["failure_phase"] = sampler.phase if sampler else "preflight"
        if engine is not None:
            try:
                report["failure_torch_gpu"] = engine.gpu_memory()
            except Exception:
                pass
        report["all_six_demos_generated"] = False
        exit_code = 130 if isinstance(exc, KeyboardInterrupt) else 1
        progress(f"Smoke stopped: {type(exc).__name__}: {exc}")
    finally:
        if sampler is not None:
            sampler.stop()
            report["resources"] = sampler.report()
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_report(report_path, report)
        progress(f"Report: {report_path}")
        progress(f"Status: {report['status']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
