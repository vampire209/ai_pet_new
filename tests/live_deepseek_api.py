"""Optional explicit one-request probe for a configured DeepSeek account."""

from __future__ import annotations

from pathlib import Path
import os
import sys
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.ai_config import API_KEY_ENVIRONMENT_VARIABLE, DeepSeekConfig
from ai.chat_service import ChatService
from ai.deepseek_client import user_facing_error


def main() -> int:
    if not os.environ.get(API_KEY_ENVIRONMENT_VARIABLE, "").strip():
        print(
            "REAL_SKIP 未设置 DEEPSEEK_API_KEY；本脚本不会自动读取文件或发起请求。",
            flush=True,
        )
        return 0

    config = DeepSeekConfig.from_environment()
    service = ChatService(config=config)
    try:
        messages = service.begin_turn("请用一句简短中文向我问好。")
        parts = list(service.stream_response(messages, Event()))
        answer = "".join(parts).strip()
        service.complete_turn(answer)
    except Exception as exc:
        print(f"REAL_FAIL {user_facing_error(exc)}", flush=True)
        return 1

    if not answer:
        print("REAL_FAIL API 没有返回可显示文字。", flush=True)
        return 1
    print(f"REAL_PASS model={config.model} chunks={len(parts)}", flush=True)
    print(answer, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
