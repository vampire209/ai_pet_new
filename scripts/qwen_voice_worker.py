"""Private, offline JSON-lines worker. Run only in the external voice venv."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time

try:
    from .qwen_voice_engine import QwenVoiceEngine, enable_offline_environment
except ImportError:
    from qwen_voice_engine import QwenVoiceEngine, enable_offline_environment

MAX_REQUEST = 65536
MAX_PCM = 16 * 1024 * 1024


def wait_for_headroom(grace_seconds: float = 3) -> None:
    """Allow OS reclamation after cancellation, without lowering the 4GiB gate."""
    import psutil
    deadline = time.monotonic() + grace_seconds
    while True:
        available = psutil.virtual_memory().available
        if available >= 4 * 1024**3:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"可用内存 {available // 1024**2} MiB，低于当前 4096 MiB 加载保护门槛。请关闭不用的应用后重试。")
        time.sleep(.1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    protocol = sys.stdout.buffer
    # Model packages print import banners. They must never enter the JSON pipe.
    sys.stdout = sys.stderr
    enable_offline_environment()

    def emit(message: dict) -> None:
        protocol.write(json.dumps({"version": 1, **message}, ensure_ascii=True).encode("ascii") + b"\n")
        protocol.flush()

    try:
        wait_for_headroom()
        engine = QwenVoiceEngine(args.model_dir, device=args.device)
        elapsed = engine.load()
        emit({"ready": True, "model": "0.6b_custom_voice", "load_seconds": elapsed,
              "voices": ["Serena"], "languages": ["Chinese"], "emotion": False})
    except Exception as exc:
        emit({"ready": False, "error": str(exc)[:600]})
        return 1
    while True:
        raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
            return 2
        request_id = None
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("请求必须为 JSON 对象。")
            request_id = request.get("id")
            if (request.get("version") != 1 or type(request_id) is not int or request_id < 1
                    or request.get("action") != "synthesize"):
                raise ValueError("不支持的请求或协议版本。")
            audio = engine.synthesize(request.get("text"), voice_id=request.get("voice"),
                                      language=request.get("language"), speed=request.get("speed"))
            if len(audio.pcm16) > MAX_PCM:
                raise ValueError("音频超过传输上限。")
            emit({"id": request_id, "ok": True, "pcm": base64.b64encode(audio.pcm16).decode("ascii"),
                  "sample_rate": audio.sample_rate, "channels": 1, "sample_width": 2,
                  "voice": request["voice"], "generation_seconds": audio.elapsed_seconds})
        except Exception as exc:
            emit({"id": request_id, "ok": False, "error": str(exc)[:600]})
            # Failed CUDA state is not reused. Auto can fall back in the parent.
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
