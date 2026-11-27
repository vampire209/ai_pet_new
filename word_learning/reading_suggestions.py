"""Parse the hidden, structured vocabulary appendix from one reading response."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Mapping

from word_learning.models import normalize_text, normalize_word


VOCABULARY_BLOCK_START = "<suggested_vocabulary>"
VOCABULARY_BLOCK_END = "</suggested_vocabulary>"

_ENGLISH_TOKEN = re.compile(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b")
_READING_HINT = re.compile(
    r"(?:长难句|阅读|原文|原句|句子|段落|文章|翻译|拆解|分析|结构|主干|"
    r"语法|指代|考研|四六级|雅思|托福|生词|词汇)",
)


def is_reading_vocabulary_request(text: str) -> bool:
    """Return whether this turn contains enough English for reading analysis."""

    normalized = text.strip()
    token_count = len(_ENGLISH_TOKEN.findall(normalized))
    if token_count < 8:
        return False
    if _READING_HINT.search(normalized):
        return True
    return token_count >= 12 and bool(re.search(r"[.!?;:\n]", normalized))


def reading_vocabulary_turn_instruction(limit: int = 8) -> str:
    """Build a nearby, machine-readable appendix requirement for one turn."""

    maximum = max(1, min(int(limit), 20))
    return f"""本轮包含英语阅读材料。先照常完整讲解，随后必须在回复末尾输出下面的隐藏技术区块；即使没有合适生词，也必须输出空列表。不要使用 Markdown 代码围栏，不要在结束标签后补充文字。最多推荐 {maximum} 个真正值得学习的词或短语，source_sentence 必须逐字取自用户原文。
<suggested_vocabulary>
{{"suggested_vocabulary":[{{"word":"...","part_of_speech":"...","meaning":"...","exam_meaning":"...","source_sentence":"..."}}]}}
</suggested_vocabulary>"""


@dataclass(frozen=True, slots=True)
class SuggestedVocabulary:
    """One high-value word or phrase suggested from the user's source text."""

    word: str
    part_of_speech: str
    meaning: str
    exam_meaning: str
    source_sentence: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "SuggestedVocabulary":
        word = normalize_word(raw.get("word", ""))
        meaning = normalize_text(raw.get("meaning", ""), required=True)
        source_sentence = normalize_text(
            raw.get("source_sentence", ""),
            required=True,
        )
        return cls(
            word=word,
            part_of_speech=normalize_text(raw.get("part_of_speech", "")),
            meaning=meaning,
            exam_meaning=normalize_text(raw.get("exam_meaning", "")),
            source_sentence=source_sentence,
        )


@dataclass(frozen=True, slots=True)
class ParsedReadingResponse:
    visible_text: str
    suggestions: tuple[SuggestedVocabulary, ...]
    parse_error: str | None = None


def visible_response_text(text: str) -> str:
    """Hide complete or streaming technical blocks without touching normal prose."""

    start = text.find(VOCABULARY_BLOCK_START)
    if start >= 0:
        end = text.find(
            VOCABULARY_BLOCK_END,
            start + len(VOCABULARY_BLOCK_START),
        )
        suffix = "" if end < 0 else text[end + len(VOCABULARY_BLOCK_END) :]
        text = text[:start] + suffix
    else:
        # A streamed opening tag may be split across chunks. Hide only the
        # longest suffix that could still become the reserved tag.
        maximum = min(len(text), len(VOCABULARY_BLOCK_START) - 1)
        for length in range(maximum, 0, -1):
            if VOCABULARY_BLOCK_START.startswith(text[-length:]):
                text = text[:-length]
                break
    return text.replace(VOCABULARY_BLOCK_END, "").rstrip()


def parse_reading_response(
    text: str,
    *,
    limit: int = 8,
) -> ParsedReadingResponse:
    """Return safe visible prose plus validated, case-insensitive suggestions."""

    visible = visible_response_text(text)
    start = text.find(VOCABULARY_BLOCK_START)
    if start < 0:
        return ParsedReadingResponse(visible, ())
    payload_start = start + len(VOCABULARY_BLOCK_START)
    end = text.find(VOCABULARY_BLOCK_END, payload_start)
    if end < 0:
        return ParsedReadingResponse(visible, (), "建议生词区块未闭合")
    payload = text[payload_start:end].strip()
    if len(payload) > 100_000:
        return ParsedReadingResponse(visible, (), "建议生词数据过长")
    # Be tolerant when a model ignores the no-code-fence instruction but keeps
    # the reserved wrapper intact.
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", payload, re.DOTALL | re.I)
    if fenced is not None:
        payload = fenced.group(1).strip()
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ParsedReadingResponse(visible, (), "建议生词 JSON 无法解析")
    if not isinstance(decoded, dict):
        return ParsedReadingResponse(visible, (), "建议生词结构必须是对象")
    raw_suggestions = decoded.get("suggested_vocabulary")
    if not isinstance(raw_suggestions, list):
        return ParsedReadingResponse(visible, (), "建议生词列表缺失")

    maximum = max(1, min(int(limit), 20))
    suggestions: list[SuggestedVocabulary] = []
    seen: set[str] = set()
    invalid_count = 0
    for raw in raw_suggestions:
        if not isinstance(raw, Mapping):
            invalid_count += 1
            continue
        try:
            suggestion = SuggestedVocabulary.from_mapping(raw)
        except (TypeError, ValueError):
            invalid_count += 1
            continue
        key = suggestion.word.casefold()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
        if len(suggestions) >= maximum:
            break
    error = None
    if invalid_count:
        error = f"已忽略 {invalid_count} 条无效建议生词"
    return ParsedReadingResponse(visible, tuple(suggestions), error)
