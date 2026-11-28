"""Phase 14 proactive companion controller: isolated 30-turn session, poke coalescing, and quick-ask."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ai.ai_settings_service import AiSettingsService
from ai.deepseek_client import DeepSeekClient, DeepSeekClientError, MissingAPIKeyError
from ai.persona_builder import build_system_persona
from ai.profile_service import ProfileService
from window_observer import ForegroundWindowInfo


_MAX_COMPANION_TURNS = 30

_POKE_SINGLE_LINES = (
    "呀，干嘛突然戳我呀~ 有事直接点左上角气泡问我哦！",
    "嘿嘿，我在呢！今天也要元气满满呀~",
    "别光戳我嘛，要不要看看待办进度？",
    "收到召唤！需要我帮你查东西还是陪你聊两句？",
)

_POKE_MULTI_LINES = (
    "哇啊连戳了 {count} 下！好痒好痒，我可要记小本本啦~",
    "连戳 {count} 下是有什么急事吗？点我头顶左边的气泡就能直接问我哦！",
    "呜哇别戳啦（第 {count} 下了！），我头发都要被你戳乱了~",
)

_CONTEXT_FALLBACKS: dict[str, tuple[str, ...]] = {
    "coding": (
        "写代码辛苦啦！卡壳了随时点我头顶气泡问我~",
        "专注敲代码的样子超帅！记得喝口水歇歇眼睛哦。",
    ),
    "bilibili": (
        "在看 B 站呀~ 这期「{media}」好看吗？我也凑过来一起看！",
        "哇在刷 B 站呢，摸鱼也要开心哦~",
    ),
    "video": (
        "在看视频放松吗？需要我安静待在角落陪你看哦~",
    ),
    "study": (
        "在认真看文档学习呢，加油！需要定个待办提醒随时告诉我~",
    ),
    "browser": (
        "在逛网页呀，找到什么好玩的东西了吗？",
    ),
    "chat": (
        "聊得开心吗？有要记下来的日程记得告诉我哦~",
    ),
}


@dataclass(frozen=True, slots=True)
class CompanionReply:
    """Completed companion utterance ready to display in CompanionBubble."""

    kind: str  # "poke" | "quick_ask" | "context"
    text: str
    speaker: str
    used_llm: bool


class CompanionSession:
    """Isolated short-term companion session capped at 30 turns (60 messages).

    Completely decoupled from main ChatService history and MemoryService so casual
    poke/context quips never pollute long-term memory or formal chat transcripts.
    """

    def __init__(self, max_turns: int = _MAX_COMPANION_TURNS) -> None:
        self._max_turns = max(1, int(max_turns))
        self._messages: deque[dict[str, str]] = deque(maxlen=self._max_turns * 2)

    @property
    def max_turns(self) -> int:
        return self._max_turns

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        u = user_text.strip()
        a = assistant_text.strip()
        if not u or not a:
            return
        self._messages.append({"role": "user", "content": u})
        self._messages.append({"role": "assistant", "content": a})

    def messages(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._messages]

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages) // 2


class PokeCoalescer(QObject):
    """Coalesce rapid consecutive pokes into one trigger with the total tap count."""

    pokes_ready = Signal(int)

    def __init__(self, window_ms: int = 520, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._count = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(50, int(window_ms)))
        self._timer.timeout.connect(self._flush)

    def record_poke(self) -> None:
        self._count += 1
        self._timer.start()

    def flush_now(self) -> int:
        self._timer.stop()
        count = self._count
        self._count = 0
        if count > 0:
            self.pokes_ready.emit(count)
        return count

    def _flush(self) -> None:
        self.flush_now()

    def cancel(self) -> None:
        self._timer.stop()
        self._count = 0


def _sanitize_bubble_text(raw: str, max_chars: int = 110) -> str:
    clean = " ".join(raw.replace("\r", "\n").split())
    clean = clean.strip("\"'“”` ")
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip("，、 ") + "…"
    return clean


class CompanionController(QObject):
    """Orchestrates poke reactions, quick-ask replies, and proactive window remarks."""

    reply_ready = Signal(object)  # emits CompanionReply
    activity_changed = Signal(str)  # empty string clears transient pill status

    def __init__(
        self,
        *,
        profile_service: ProfileService | None = None,
        ai_settings_service: AiSettingsService | None = None,
        client_factory: Callable[[], DeepSeekClient] | None = None,
        poke_window_ms: int = 520,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_service = profile_service or ProfileService()
        self._ai_settings_service = ai_settings_service
        self._client_factory = client_factory
        self._session = CompanionSession(max_turns=_MAX_COMPANION_TURNS)
        self._poke_coalescer = PokeCoalescer(window_ms=poke_window_ms, parent=self)
        self._poke_coalescer.pokes_ready.connect(self._on_pokes_coalesced)
        self._enabled = True
        self._busy_lock = threading.Lock()
        self._request_seq = 0
        self._closed = False

    @property
    def session(self) -> CompanionSession:
        return self._session

    @property
    def poke_coalescer(self) -> PokeCoalescer:
        return self._poke_coalescer

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._poke_coalescer.cancel()

    def shutdown(self) -> None:
        self._closed = True
        self._poke_coalescer.cancel()

    def _speaker_name(self) -> str:
        name = self._profile_service.profile.pet_name.strip()
        return name or "克劳德娘"

    def _resolve_client(self) -> DeepSeekClient:
        if self._client_factory is not None:
            return self._client_factory()
        if self._ai_settings_service is not None:
            settings = self._ai_settings_service.settings
            return DeepSeekClient(
                settings.resolve_config(),
                api_key=settings.resolve_api_key(),
            )
        return DeepSeekClient()

    def handle_poke(self) -> None:
        if not self._enabled or self._closed:
            return
        self._poke_coalescer.record_poke()

    def _on_pokes_coalesced(self, count: int) -> None:
        if not self._enabled or self._closed:
            return
        prompt = (
            f"用户刚刚用鼠标轻戳了你 {count} 下。"
            if count > 1
            else "用户刚刚用鼠标轻戳了你一下。"
        )
        fallback = self._fallback_poke_line(count)
        self._dispatch_async(
            kind="poke",
            user_prompt=prompt,
            fallback_text=fallback,
            activity_label="被戳了一下~",
            allow_fallback_on_missing_key=True,
        )

    def ask_quick(self, question: str) -> None:
        clean = question.strip()
        if not clean or self._closed:
            return
        self._dispatch_async(
            kind="quick_ask",
            user_prompt=clean,
            fallback_text="还没配置 DeepSeek API Key 哦，去「桌宠设置 → AI 接口」填一下就能随时聊啦~",
            activity_label="思考回复中…",
            allow_fallback_on_missing_key=True,
        )

    def handle_foreground_context(self, info: ForegroundWindowInfo) -> None:
        if not self._enabled or self._closed:
            return
        if info.is_bilibili:
            if info.media_title:
                prompt = f"用户正在哔哩哔哩看视频《{info.media_title}》，请你像贴心桌面伙伴一样顺口搭一句简短有趣的话（别剧透、别说教）。"
            else:
                prompt = "用户正在刷哔哩哔哩，顺口搭一句轻松简短的陪伴小话。"
            activity = "陪你看 B 站"
        else:
            prompt = (
                f"用户当前正在使用应用 {info.process_name}（窗口标题：{info.title}，类别：{info.category}）。"
                "请用非常简短自然的一句话送上陪伴感，不要打断或说教。"
            )
            activity = "陪你摸鱼中" if info.category == "video" else "陪你专注中"
        fallback = self._fallback_context_line(info)
        self._dispatch_async(
            kind="context",
            user_prompt=prompt,
            fallback_text=fallback,
            activity_label=activity,
            allow_fallback_on_missing_key=True,
        )

    def _fallback_poke_line(self, count: int) -> str:
        if count >= 2:
            return random.choice(_POKE_MULTI_LINES).format(count=count)
        return random.choice(_POKE_SINGLE_LINES)

    def _fallback_context_line(self, info: ForegroundWindowInfo) -> str:
        if info.is_bilibili and info.media_title:
            return f"在看 B 站呀~《{info.media_title[:18]}》好看吗？我也一起看！"
        pool = _CONTEXT_FALLBACKS.get(info.category) or ("我一直在桌面陪着你哦~",)
        return random.choice(pool).replace("{media}", info.media_title[:16] or "这个视频")

    def _build_messages(self, user_prompt: str, *, kind: str) -> list[dict[str, str]]:
        persona = build_system_persona(self._profile_service.profile)
        companion_rules = (
            "你现在正在桌宠头顶/身旁的轻量气泡里说话。"
            "要求：只回复 1~2 句自然口语，控制在 45 个汉字以内；不要使用 Markdown、列表、代码块或表情符号堆砌。"
        )
        if kind == "quick_ask":
            companion_rules = (
                "你现在正在桌宠身旁的快捷气泡里回答用户提问。"
                "要求：直接、清晰、简明地回答，尽量控制在 80 个汉字以内，不要输出长篇 Markdown。"
            )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": f"{persona}\n\n{companion_rules}"}
        ]
        messages.extend(self._session.messages())
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def _dispatch_async(
        self,
        *,
        kind: str,
        user_prompt: str,
        fallback_text: str,
        activity_label: str,
        allow_fallback_on_missing_key: bool,
    ) -> None:
        with self._busy_lock:
            self._request_seq += 1
            seq = self._request_seq

        speaker = self._speaker_name()
        client = self._resolve_client()

        # Fast path: if API key isn't configured, emit friendly instant response without spawning thread
        if not client.is_configured:
            if allow_fallback_on_missing_key:
                self._session.append_turn(user_prompt, fallback_text)
                self.reply_ready.emit(
                    CompanionReply(
                        kind=kind,
                        text=fallback_text,
                        speaker=speaker,
                        used_llm=False,
                    )
                )
            return

        self.activity_changed.emit(activity_label)
        messages = self._build_messages(user_prompt, kind=kind)

        def _worker() -> None:
            used_llm = False
            reply_text = ""
            try:
                chunks: list[str] = []
                for chunk in client.stream_chat(messages):
                    if self._closed:
                        return
                    chunks.append(chunk)
                candidate = _sanitize_bubble_text("".join(chunks))
                if candidate:
                    reply_text = candidate
                    used_llm = True
            except (MissingAPIKeyError, DeepSeekClientError, Exception):
                reply_text = ""

            if self._closed:
                return
            with self._busy_lock:
                stale = seq != self._request_seq
            if stale:
                return

            if not reply_text:
                reply_text = fallback_text if kind != "quick_ask" else "刚才网络开小差了，再问我一次试试吧~"
            self._session.append_turn(user_prompt, reply_text)
            self.activity_changed.emit("")
            self.reply_ready.emit(
                CompanionReply(
                    kind=kind,
                    text=reply_text,
                    speaker=speaker,
                    used_llm=used_llm,
                )
            )

        thread = threading.Thread(
            target=_worker,
            name=f"companion-{kind}-{seq}",
            daemon=True,
        )
        thread.start()
