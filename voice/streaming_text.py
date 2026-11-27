"""Incremental, voice-only sentence boundaries for streamed Markdown replies."""

from __future__ import annotations

import re

from voice.text import prepare_text_for_speech


_URL_STARTS = ("https://", "http://")
_NORMAL_RUN = re.compile(r"[^`~\[!h]+")
_SOFT_MIN = 180
_SOFT_MAX = 240
_MAX_UNRESOLVED_MARKUP = 16384
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"}


def split_for_speech(text: str) -> list[str]:
    """Use the same bounded parser for full replies and streamed replies."""
    segmenter = SpeechSegmenter()
    return segmenter.feed(text) + segmenter.finish()


class SpeechSegmenter:
    """Emit safe spoken sentences while retaining undecidable Markdown tails.

    Only an unfinished source tail and pending spoken prose are retained.
    Fenced code and the reading appendix never enter the spoken buffer. Link
    targets and inline-code delimiters wait for closure before being processed,
    so a split marker cannot expose machine-readable content to speech.
    ``finish`` flushes a normal unfinished sentence; ``reset`` discards it.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._source = ""
        self._prose = ""
        self._fence_char = ""
        self._fence_length = 0
        # Nonnegative while the source line consists only of spaces/tabs.
        self._line_indent = 0

    def feed(self, chunk: str) -> list[str]:
        if not isinstance(chunk, str):
            raise TypeError("语音分句输入必须是字符串。")
        self._source += chunk
        self._scan(final=False)
        if len(self._source) > _MAX_UNRESOLVED_MARKUP:
            raise ValueError("未闭合的语音 Markdown 片段过长。")
        return self._drain(final=False)

    def finish(self) -> list[str]:
        self._scan(final=True)
        result = self._drain(final=True)
        self.reset()
        return result

    def _take(self, length: int, *, spoken: str | None = None) -> str:
        raw = self._source[:length]
        self._source = self._source[length:]
        if "\n" in raw:
            tail = raw.rsplit("\n", 1)[1]
            self._line_indent = len(tail) if not tail.strip(" \t\r") else -1
        elif self._line_indent >= 0:
            if raw.strip(" \t\r"):
                self._line_indent = -1
            else:
                self._line_indent += len(raw)
        if spoken is not None:
            self._prose += spoken
        return raw

    def _scan(self, *, final: bool) -> None:
        while self._source:
            source = self._source

            if self._fence_char:
                newline = source.find("\n")
                if newline < 0 and not final:
                    return
                length = len(source) if newline < 0 else newline + 1
                line = source[:length].rstrip("\r\n")
                closing = re.fullmatch(
                    r"[ \t]{0,3}" + re.escape(self._fence_char)
                    + "{" + str(self._fence_length) + r",}[ \t]*",
                    line,
                )
                self._take(length)
                if closing:
                    self._fence_char = ""
                    self._fence_length = 0
                continue

            char = source[0]

            if char == "h":
                if any(prefix.startswith(source) for prefix in _URL_STARTS):
                    if not final:
                        return
                if source.startswith(_URL_STARTS):
                    whitespace = re.search(r"\s", source)
                    if whitespace is None and not final:
                        return
                    self._take(whitespace.start() if whitespace else len(source))
                    continue

            if char in "`~" and 0 <= self._line_indent <= 3:
                run = len(source) - len(source.lstrip(char))
                if run == len(source) and not final:
                    return
                if run >= 3:
                    newline = source.find("\n")
                    if newline < 0:
                        if final:
                            self._take(len(source))
                        return
                    self._fence_char, self._fence_length = char, run
                    self._take(newline + 1)
                    continue

            if char == "`":
                run = len(source) - len(source.lstrip("`"))
                if run == len(source) and not final:
                    return
                close = re.search(r"(?<!`)`{" + str(run) + r"}(?!`)", source[run:])
                if close:
                    end = run + close.start()
                    self._take(end + run, spoken=source[run:end])
                    continue
                if not final:
                    return
                self._take(len(source), spoken=source[run:])
                continue

            image = source.startswith("![")
            if char == "!" and len(source) == 1 and not final:
                return
            if char == "[" or image:
                if not self._consume_link(image=image, final=final):
                    return
                continue

            normal = _NORMAL_RUN.match(source)
            length = normal.end() if normal else 1
            self._take(length, spoken=source[:length])

    @staticmethod
    def _matching_close(source: str, start: int, opening: str, closing: str) -> int:
        depth = 0
        escaped = False
        for index in range(start, len(source)):
            char = source[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        return -1

    def _consume_link(self, *, image: bool, final: bool) -> bool:
        source = self._source
        start = 1 if image else 0
        close = self._matching_close(source, start, "[", "]")
        if close < 0:
            if not final:
                return False
            self._take(len(source), spoken="" if image else source)
            return True
        after = close + 1
        if after == len(source) and not final:
            return False
        label = source[start + 1:close]
        if after < len(source) and source[after] in "([":
            opening = source[after]
            closing = ")" if opening == "(" else "]"
            end = self._matching_close(source, after, opening, closing)
            if end < 0 and not final:
                return False
            self._take(len(source) if end < 0 else end + 1, spoken="" if image else label)
            return True
        if after < len(source) and source[after] == ":" and 0 <= self._line_indent <= 3:
            newline = source.find("\n", after)
            if newline < 0 and not final:
                return False
            self._take(len(source) if newline < 0 else newline + 1)
            return True
        self._take(after, spoken="" if image else source[:after])
        return True

    def _boundary(self, *, final: bool) -> int | None:
        text = self._prose
        for index, char in enumerate(text[:_SOFT_MAX]):
            if char in "。！？!?\n":
                return index + 1
            if char != ".":
                continue
            if index + 1 == len(text):
                return index + 1 if final else None
            following = text[index + 1]
            previous = text[index - 1] if index else ""
            if previous.isdigit() and following.isdigit():
                continue
            token = re.search(r"[A-Za-z]+(?:\.[A-Za-z]+)*$", text[:index])
            if token:
                word = token[0]
                if word.lower() in _ABBREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]", word):
                    continue
            if following.isspace() or following in '。！？!?"”’）)]':
                return index + 1
        if len(text) >= _SOFT_MAX:
            for index in range(_SOFT_MAX - 1, _SOFT_MIN - 1, -1):
                if text[index].isspace() or text[index] in "，,；;：:":
                    return index + 1
            return _SOFT_MAX
        return len(text) if final and text else None

    def _drain(self, *, final: bool) -> list[str]:
        result: list[str] = []
        while self._prose:
            boundary = self._boundary(final=final)
            if boundary is None:
                break
            raw, self._prose = self._prose[:boundary], self._prose[boundary:]
            cleaned = prepare_text_for_speech(raw)
            # Emoji or Markdown-only pieces must not create audio jobs.
            if cleaned and any(char.isalnum() for char in cleaned):
                result.append(cleaned)
        return result
