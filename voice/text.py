"""Prepare a separate spoken copy of Markdown; never mutate chat/history text."""

from __future__ import annotations

import re
import unicodedata


def _without_fenced_code(text: str) -> str:
    lines: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if not fence_char:
            if fence:
                fence_char, fence_length = fence[1][0], len(fence[1])
            else:
                lines.append(line)
        elif (
            fence
            and fence[1][0] == fence_char
            and len(fence[1]) >= fence_length
            and not fence[2].strip()
        ):
            fence_char = ""
            fence_length = 0
    return "\n".join(lines)


def prepare_text_for_speech(text: str) -> str:
    """Remove display-only syntax, code blocks and emoji from completed replies.

    Link labels and inline-code words remain pronounceable. Punctuation and
    natural-language text are retained; multiple line breaks become one space.
    This is deliberately not a renderer and never rewrites the UI's source.
    """
    if not isinstance(text, str):
        raise TypeError("朗读文本必须是字符串。")
    cleaned = _without_fenced_code(text)
    cleaned = re.sub(r"(?m)^\s{0,3}\[[^\]\n]+\]:\s*\S+.*$", "", cleaned)
    cleaned = re.sub(r"!\[[^\]\n]*\]\([^\n]*?\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]\n]+)\]\([^\n]*?\)", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]\n]+)\]\[[^\]\n]*\]", r"\1", cleaned)
    cleaned = re.sub(r"<[^>\n]+>", "", cleaned)
    cleaned = re.sub(r"https?://[^\s<>]+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"(?m)\s+#+\s*$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:[-*_]\s*){3,}$", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:>\s*)+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?", "", cleaned)
    cleaned = re.sub(r"(`+)(.+?)\1", r"\2", cleaned)
    cleaned = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", cleaned)
    cleaned = re.sub(r"[#*0-9]\ufe0f?\u20e3", "", cleaned)
    cleaned = "".join(
        ch
        for ch in cleaned
        if ch not in "\ufe0e\ufe0f\u200d\u20e3*`|"
        and unicodedata.category(ch) not in {"So", "Sk", "Cf"}
        and not (unicodedata.category(ch) == "Sm" and ch not in "+=<>~")
    )
    cleaned = re.sub(r"[!！]+", "。", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
