"""Conservative local rules for extracting durable facts from user text."""

from __future__ import annotations

import re

from memory.memory_candidate import MemoryCandidate


_TEMPORARY_CUES = (
    "这次",
    "这回",
    "本次",
    "这道题",
    "这个问题",
    "今天先",
    "暂时",
    "现在先",
    "先别",
)
_TODO_CUES = ("提醒我", "提醒一下", "叫我", "待办", "备忘录", "备忘提醒")
_EXPLICIT_LONG_TERM_CUES = ("记住", "以后", "从现在开始", "今后")
_SIGNAL_TERMS = (
    "傅里叶变换",
    "傅里叶",
    "Z 变换",
    "z 变换",
    "Z变换",
    "z变换",
    "拉普拉斯变换",
    "拉普拉斯",
    "信号与系统",
    "卷积",
    "频域",
)

_LEARNING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:我)?(?:最近|目前|现在|这段时间)(?:还|也)?"
        r"(?:正在|开始|主要)?(?:在)?"
        r"(?:学习|学|复习|研究|练习|练)\s*(?P<subject>[^，,。！？?!；;]+)",
        r"我正在(?:学习|学|复习|研究|练习|练)\s*"
        r"(?P<subject>[^，,。！？?!；;]+)",
        r"(?P<subject>[^，,。！？?!；;]+?)(?:最近|目前)是我(?:主要)?(?:在)?"
        r"(?:学习|学|复习|研究|练习|练)的(?:内容|东西)",
    )
)


def _normalize_key_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def _clean_subject(raw: str) -> str:
    subject = str(raw or "").strip()
    subject = re.sub(r"^(?:一下|一点|一些)\s*", "", subject)
    subject = re.sub(r"(?:的东西|的内容)$", "", subject)
    subject = re.sub(r"(?:了|呢|呀|啊|吧)+$", "", subject)
    return subject.strip(" ，,。！？?!；;：:")


def _sentence(text: str) -> str:
    normalized = text.strip().rstrip("。！？?!")
    return normalized + "。"


def _signal_concepts(text: str) -> tuple[str, ...]:
    concepts: list[str] = []
    lowered = text.lower().replace(" ", "")
    if "傅里叶" in text:
        concepts.append("傅里叶变换")
    if "z变换" in lowered:
        concepts.append("Z 变换")
    if "拉普拉斯" in text:
        concepts.append("拉普拉斯变换")
    if "卷积" in text:
        concepts.append("卷积")
    if "频域" in text:
        concepts.append("频域分析")
    return tuple(concepts)


def infer_memory_metadata(content: str) -> tuple[str, str]:
    """Infer stable local metadata for old/manual rows without rewriting them."""

    normalized = str(content or "")
    compact = _normalize_key_text(normalized)
    if "数学题" in normalized and any(
        cue in normalized for cue in ("简短", "简洁", "详细", "步骤", "推导", "答案")
    ):
        return "preference", "preference:math_answer_style"
    if any(term.lower().replace(" ", "") in compact for term in _SIGNAL_TERMS):
        return "learning", "learning:signal_system"
    if "钢琴" in normalized:
        return "learning", "learning:钢琴"
    if any(
        cue in normalized
        for cue in ("学习", "正在学", "最近在学", "复习", "研究", "练习")
    ):
        tail = re.split(
            r"(?:学习|正在学|最近在学|复习|研究|练习)",
            normalized,
        )[-1]
        subject = _clean_subject(tail)
        if subject:
            return "learning", f"learning:{_normalize_key_text(subject)[:80]}"
        return "learning", "learning:general"
    if any(cue in normalized for cue in ("喜欢", "偏好")):
        return "preference", "preference:likes"
    if "不喜欢" in normalized:
        return "preference", "preference:dislikes"
    if any(cue in normalized for cue in ("通常", "习惯")):
        return "habit", "habit:general"
    return "general", ""


class MemoryExtractor:
    """Extract at most one high-confidence candidate without any API call."""

    minimum_confidence = 0.72

    def extract(self, user_text: str) -> MemoryCandidate | None:
        text = str(user_text or "").strip()
        if not text or any(cue in text for cue in _TEMPORARY_CUES):
            return None
        if any(cue in text for cue in _TODO_CUES):
            return None

        explicit = any(cue in text for cue in _EXPLICIT_LONG_TERM_CUES)
        if self._looks_like_question(text) and not explicit:
            return None

        math_style = self._extract_math_style(text, explicit)
        if math_style is not None:
            return math_style

        learning = self._extract_learning(text)
        if learning is not None:
            return learning

        preference = self._extract_preference(text, explicit)
        if preference is not None:
            return preference

        habit = self._extract_habit(text)
        if habit is not None:
            return habit

        current_activity = self._extract_current_activity(text)
        if current_activity is not None:
            return current_activity

        if explicit:
            return self._extract_explicit_general(text)
        return None

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        if text.endswith(("?", "？")) or text.startswith(("你记得", "你知道")):
            return True
        return bool(re.search(r"(?:什么|啥|怎么|为什么|哪(?:个|些)?).*(?:吗|呢)?[。！？?!]?$", text))

    @staticmethod
    def _extract_math_style(
        text: str,
        explicit: bool,
    ) -> MemoryCandidate | None:
        if "数学题" not in text:
            return None
        has_long_scope = explicit or any(cue in text for cue in ("默认", "都要", "都尽量"))
        if not has_long_scope:
            return None
        if any(cue in text for cue in ("简短", "简洁", "只要答案", "直接答案")):
            content = "用户希望以后数学题尽量简短。"
        elif any(cue in text for cue in ("详细", "步骤", "推导", "关键过程")):
            content = "用户希望数学题默认展示详细步骤。"
        else:
            return None
        return MemoryCandidate(
            content=content,
            category="preference",
            topic_key="preference:math_answer_style",
            importance=0.9,
            is_long_term=True,
            confidence=0.98,
            explicit_update=True,
        )

    @staticmethod
    def _extract_learning(text: str) -> MemoryCandidate | None:
        for pattern in _LEARNING_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            subject = _clean_subject(match.group("subject"))
            if not subject or subject in {"什么", "啥"}:
                return None
            if any(term.lower().replace(" ", "") in _normalize_key_text(subject) for term in _SIGNAL_TERMS):
                concepts = _signal_concepts(subject)
                if concepts:
                    detail = "、".join(concepts)
                    content = f"用户近期正在学习信号与系统，内容包括{detail}。"
                else:
                    content = "用户近期正在学习信号与系统。"
                topic_key = "learning:signal_system"
            else:
                content = f"用户最近正在学习{subject}。"
                topic_key = f"learning:{_normalize_key_text(subject)[:80]}"
            return MemoryCandidate(
                content=content,
                category="learning",
                topic_key=topic_key,
                importance=0.65,
                is_long_term=True,
                confidence=0.9,
            )
        return None

    @staticmethod
    def _extract_preference(
        text: str,
        explicit: bool,
    ) -> MemoryCandidate | None:
        cleaned = re.sub(r"^(?:请)?记住[，,：:]?\s*", "", text)
        dislike = re.search(r"我不喜欢\s*(?P<value>[^，,。！？?!；;]+)", cleaned)
        if dislike is not None:
            value = _clean_subject(dislike.group("value"))
            if value:
                return MemoryCandidate(
                    content=f"用户不喜欢{value}。",
                    category="preference",
                    topic_key="preference:dislikes",
                    importance=0.78 if explicit else 0.7,
                    is_long_term=True,
                    confidence=0.9,
                    explicit_update=explicit,
                )
        like = re.search(r"我(?P<more>更)?喜欢\s*(?P<value>[^，,。！？?!；;]+)", cleaned)
        if like is not None:
            value = _clean_subject(like.group("value"))
            if value:
                wording = "更喜欢" if like.group("more") else "喜欢"
                return MemoryCandidate(
                    content=f"用户{wording}{value}。",
                    category="preference",
                    topic_key="preference:likes",
                    importance=0.78 if explicit else 0.7,
                    is_long_term=True,
                    confidence=0.9,
                    explicit_update=explicit or bool(like.group("more")),
                )
        return None

    @staticmethod
    def _extract_habit(text: str) -> MemoryCandidate | None:
        match = re.search(r"我通常\s*(?P<value>[^，,。！？?!；;]+)", text)
        if match is None:
            return None
        value = _clean_subject(match.group("value"))
        if not value:
            return None
        return MemoryCandidate(
            content=f"用户通常{value}。",
            category="habit",
            topic_key=f"habit:{_normalize_key_text(value)[:80]}",
            importance=0.65,
            is_long_term=True,
            confidence=0.86,
        )

    @staticmethod
    def _extract_current_activity(text: str) -> MemoryCandidate | None:
        patterns = (
            (
                r"我(?:最近|目前|现在主要)(?:正在|在)?(?:做|研究|准备|进行)\s*"
                r"(?P<value>[^，,。！？?!；;]+)",
                "用户目前主要在做{value}。",
            ),
            (
                r"我最近开始\s*(?P<value>[^，,。！？?!；;]+)",
                "用户最近开始{value}。",
            ),
            (
                r"我目前在\s*(?P<value>[^，,。！？?!；;]+)",
                "用户目前在{value}。",
            ),
        )
        value = ""
        template = ""
        for pattern, candidate_template in patterns:
            match = re.search(pattern, text)
            if match is not None:
                value = _clean_subject(match.group("value"))
                template = candidate_template
                break
        if not value:
            return None
        return MemoryCandidate(
            content=template.format(value=value),
            category="activity",
            topic_key=f"activity:{_normalize_key_text(value)[:80]}",
            importance=0.65,
            is_long_term=True,
            confidence=0.84,
        )

    @staticmethod
    def _extract_explicit_general(text: str) -> MemoryCandidate | None:
        cleaned = re.sub(
            r"^(?:请)?(?:记住|以后|从现在开始|今后)[，,：:]?\s*",
            "",
            text,
        )
        cleaned = _clean_subject(cleaned)
        if not cleaned:
            return None
        content = _sentence(f"用户的长期要求：{cleaned}")
        category, inferred_topic = infer_memory_metadata(content)
        topic_key = inferred_topic or f"preference:{_normalize_key_text(cleaned)[:80]}"
        return MemoryCandidate(
            content=content,
            category=category if category != "general" else "preference",
            topic_key=topic_key,
            importance=0.82,
            is_long_term=True,
            confidence=0.82,
            explicit_update=True,
        )
