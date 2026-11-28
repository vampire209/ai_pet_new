"""Respectful foreground window awareness (coding / Bilibili / video / study) for proactive companion."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal


_IGNORED_PROCESSES = frozenset({
    "explorer.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "dwm.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "taskmgr.exe",
    "aipetnew.exe",
})

_CODING_PROCESSES = frozenset({
    "code.exe",
    "cursor.exe",
    "pycharm64.exe",
    "idea64.exe",
    "devenv.exe",
    "windsurf.exe",
    "zed.exe",
    "windowsterminal.exe",
    "wt.exe",
    "powershell.exe",
    "pwsh.exe",
    "cmd.exe",
})

_BROWSER_PROCESSES = frozenset({
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "arc.exe",
    "vivaldi.exe",
})

_DOC_PROCESSES = frozenset({
    "winword.exe",
    "excel.exe",
    "powerpnt.exe",
    "wps.exe",
    "obsidian.exe",
    "notion.exe",
    "typora.exe",
    "acrobat.exe",
    "msedgewebview2.exe",
})

_CHAT_PROCESSES = frozenset({
    "wechat.exe",
    "weixin.exe",
    "qq.exe",
    "discord.exe",
    "telegram.exe",
    "slack.exe",
    "feishu.exe",
    "lark.exe",
})

_VIDEO_PROCESSES = frozenset({
    "potplayermini64.exe",
    "potplayermini.exe",
    "vlc.exe",
    "mpv.exe",
    "bilibili.exe",
})

_BILIBILI_SUFFIX_RE = re.compile(
    r"\s*[-_—–]\s*(?:哔哩哔哩|bilibili|bilibili_哔哩哔哩).*$",
    re.IGNORECASE,
)
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*[-—–]\s*(?:Google Chrome|Microsoft\s*Edge|Mozilla Firefox|Brave|Opera)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ForegroundWindowInfo:
    """Normalized description of the user's current foreground application."""

    hwnd: int
    process_name: str
    title: str
    category: str
    is_bilibili: bool = False
    media_title: str = ""

    @property
    def context_key(self) -> str:
        if self.is_bilibili and self.media_title:
            return f"bilibili:{self.media_title[:40]}"
        return f"{self.category}:{self.process_name.lower()}"


def extract_bilibili_title(raw_title: str) -> str:
    """Strip browser and Bilibili site suffixes to recover the video/page title."""
    cleaned = _BROWSER_SUFFIX_RE.sub("", raw_title.strip())
    cleaned = _BILIBILI_SUFFIX_RE.sub("", cleaned).strip(" -_—–|")
    if cleaned in ("", "哔哩哔哩", "bilibili", "首页"):
        return ""
    return cleaned[:60]


def classify_foreground_window(
    process_name: str,
    title: str,
    *,
    hwnd: int = 0,
) -> ForegroundWindowInfo | None:
    """Classify a foreground window; return None for system/ignored/empty windows."""
    proc = process_name.strip().lower()
    clean_title = " ".join(title.strip().split())
    if not proc or proc in _IGNORED_PROCESSES or not clean_title:
        return None
    lower_title = clean_title.lower()
    if "ai 桌宠" in lower_title or "aipetnew" in lower_title:
        return None
    is_bili = (
        proc == "bilibili.exe"
        or "哔哩哔哩" in clean_title
        or "bilibili" in lower_title
    )
    if is_bili:
        media_title = extract_bilibili_title(clean_title)
        return ForegroundWindowInfo(
            hwnd=hwnd,
            process_name=proc,
            title=clean_title[:100],
            category="bilibili",
            is_bilibili=True,
            media_title=media_title,
        )

    if proc in _CODING_PROCESSES or any(
        token in lower_title for token in ("visual studio code", "pycharm", "github")
    ):
        category = "coding"
    elif proc in _VIDEO_PROCESSES or any(
        token in lower_title for token in ("youtube", "netflix", "爱奇艺", "腾讯视频", "优酷")
    ):
        category = "video"
    elif proc in _DOC_PROCESSES:
        category = "study"
    elif proc in _CHAT_PROCESSES:
        category = "chat"
    elif proc in _BROWSER_PROCESSES:
        category = "browser"
    else:
        category = "other"

    return ForegroundWindowInfo(
        hwnd=hwnd,
        process_name=proc,
        title=clean_title[:100],
        category=category,
        is_bilibili=False,
        media_title="",
    )


def query_windows_foreground() -> tuple[int, str, str] | None:
    """Read current foreground (hwnd, process_name, window_title) on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return None

        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        if pid.value:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
            )
            if handle:
                try:
                    size = wintypes.DWORD(512)
                    path_buf = ctypes.create_unicode_buffer(512)
                    if kernel32.QueryFullProcessImageNameW(
                        handle, 0, path_buf, ctypes.byref(size)
                    ):
                        proc_name = os.path.basename(path_buf.value)
                finally:
                    kernel32.CloseHandle(handle)
        return (hwnd, proc_name, title)
    except Exception:
        return None


class ForegroundWindowObserver(QObject):
    """Polls foreground window context with dwell filtering and anti-spam cooldown."""

    context_noticed = Signal(object)

    def __init__(
        self,
        *,
        provider: Callable[[], tuple[int, str, str] | None] | None = None,
        own_pid: int | None = None,
        poll_interval_ms: int = 8000,
        dwell_seconds: float = 20.0,
        cooldown_seconds: float = 300.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider or query_windows_foreground
        self._own_pid = own_pid if own_pid is not None else os.getpid()
        self._dwell_seconds = max(0.0, float(dwell_seconds))
        self._cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._enabled = True
        self._suppressed = False
        self._candidate_key: str | None = None
        self._candidate_since: float = 0.0
        self._last_announced_key: str | None = None
        self._last_announced_at: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(max(1000, int(poll_interval_ms)))
        self._timer.timeout.connect(self.poll_once)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._candidate_key = None

    def set_suppressed(self, suppressed: bool) -> None:
        """Pause proactive remarks during fullscreen video/games or active reminders."""
        self._suppressed = bool(suppressed)

    def poll_once(self, *, now: float | None = None) -> ForegroundWindowInfo | None:
        if not self._enabled or self._suppressed:
            return None
        current_time = time.monotonic() if now is None else float(now)
        raw = self._provider()
        if raw is None:
            return None
        hwnd, proc_name, title = raw
        info = classify_foreground_window(proc_name, title, hwnd=hwnd)
        if info is None:
            return None
        # Only proactively react to meaningful contexts (skip vague "other")
        if info.category == "other":
            return None

        key = info.context_key
        if key != self._candidate_key:
            self._candidate_key = key
            self._candidate_since = current_time
            return None

        # Must dwell on this context before commenting
        if (current_time - self._candidate_since) < self._dwell_seconds:
            return None
        # Don't repeat the same context consecutively
        if key == self._last_announced_key:
            return None
        # Respect global cooldown between proactive bubbles
        if (
            self._last_announced_at > 0.0
            and (current_time - self._last_announced_at) < self._cooldown_seconds
        ):
            return None

        self._last_announced_key = key
        self._last_announced_at = current_time
        self.context_noticed.emit(info)
        return info
