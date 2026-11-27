"""Reading-answer vocabulary extraction, UI, and wordbook integration tests."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ai.ability_modes import ABILITY_ENGLISH_TEACHER
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.profile import PetProfile
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from word_learning.models import LearningSettings, RESULT_EASY, SOURCE_READING
from word_learning.reading_suggestions import (
    VOCABULARY_BLOCK_END,
    VOCABULARY_BLOCK_START,
    SuggestedVocabulary,
    is_reading_vocabulary_request,
    parse_reading_response,
    reading_vocabulary_turn_instruction,
    visible_response_text,
)
from word_learning.service import WordLearningService
from word_learning.suggestion_widgets import SuggestedVocabularyCard
from word_learning.window import WordLearningWindow


BASE = datetime(2026, 9, 5, 10, 0)
SOURCE_SENTENCE = (
    "Some researchers argue that conventional methods remain useful, but "
    "others maintain that rapid technological change has significantly "
    "reduced their effectiveness."
)


def response_with(suggestions) -> str:
    payload = json.dumps(
        {"suggested_vocabulary": suggestions},
        ensure_ascii=False,
    )
    return (
        "正常讲解\n这句话表达了两类研究者的不同看法。\n"
        f"{VOCABULARY_BLOCK_START}\n{payload}\n{VOCABULARY_BLOCK_END}"
    )


def suggestion(
    word: str,
    meaning: str,
    *,
    part_of_speech: str = "v.",
    sentence: str = SOURCE_SENTENCE,
) -> dict[str, str]:
    return {
        "word": word,
        "part_of_speech": part_of_speech,
        "meaning": meaning,
        "exam_meaning": meaning,
        "source_sentence": sentence,
    }


def wait_until(predicate, app: QApplication, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())


class ReadingVocabularyLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["reading-vocab-tests"])

    def _profiles(self, path: Path, *, english: bool = True) -> ProfileService:
        profiles = ProfileService(path)
        profiles.save(
            PetProfile(
                ability_mode=(ABILITY_ENGLISH_TEACHER if english else "default")
            )
        )
        return profiles

    def test_01_valid_structure_is_hidden_parsed_limited_and_deduplicated(self) -> None:
        raw = response_with(
            [
                suggestion("Conventional", "传统的；常规的", part_of_speech="adj."),
                suggestion("conventional", "重复项"),
                suggestion("account for", "解释；占比"),
                suggestion("maintain", "认为；坚持"),
            ]
        )
        parsed = parse_reading_response(raw, limit=2)
        self.assertEqual(parsed.visible_text.splitlines()[0], "正常讲解")
        self.assertNotIn("suggested_vocabulary", parsed.visible_text)
        self.assertEqual(
            [item.word for item in parsed.suggestions],
            ["conventional", "account for"],
        )
        self.assertEqual(parsed.suggestions[0].part_of_speech, "adj.")

    def test_02_empty_list_produces_no_card_data(self) -> None:
        parsed = parse_reading_response(response_with([]))
        self.assertEqual(parsed.suggestions, ())
        self.assertIsNone(parsed.parse_error)
        self.assertIn("正常讲解", parsed.visible_text)

    def test_03_invalid_or_streaming_structure_is_safely_hidden(self) -> None:
        raw = (
            "正常阅读分析\n"
            f"{VOCABULARY_BLOCK_START}\n{{not-json}}\n{VOCABULARY_BLOCK_END}"
        )
        parsed = parse_reading_response(raw)
        self.assertEqual(parsed.visible_text, "正常阅读分析")
        self.assertEqual(parsed.suggestions, ())
        self.assertIsNotNone(parsed.parse_error)
        partial = "正常阅读分析\n<suggested_voc"
        self.assertEqual(visible_response_text(partial), "正常阅读分析")

    def test_04_parse_failure_preserves_chat_answer_and_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(Path(temp_dir) / "profile.json")
            service = WordLearningService(":memory:", now_provider=lambda: BASE)
            raw = (
                "正常阅读讲解没有丢失\n"
                f"{VOCABULARY_BLOCK_START}broken{VOCABULARY_BLOCK_END}"
            )
            fake = FakeDeepSeekClient(((raw,),))
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=service,
            )
            window.input.setPlainText(SOURCE_SENTENCE)
            window.send_message()
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            self.assertIn("正常阅读讲解没有丢失", window.message_texts[-1][1])
            self.assertNotIn("suggested_vocabulary", window.message_texts[-1][1])
            self.assertEqual(window.suggested_vocabulary_cards, ())
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(
                window.service.history[-1]["content"],
                "正常阅读讲解没有丢失",
            )
            window.shutdown()
            service.close()

    def test_05_batch_add_partial_duplicate_and_selection_are_correct(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        service.add_word("conventional", "旧释义")
        items = (
            SuggestedVocabulary.from_mapping(
                suggestion("conventional", "传统的；常规的", part_of_speech="adj.")
            ),
            SuggestedVocabulary.from_mapping(suggestion("maintain", "认为；坚持")),
            SuggestedVocabulary.from_mapping(
                suggestion("effectiveness", "有效性", part_of_speech="n.")
            ),
        )
        card = SuggestedVocabularyCard(items, service)
        self.assertTrue(all(item.checkbox.isChecked() for item in card.items))
        self.assertEqual(card.items[0].state_label.text(), "已在单词本")
        card.items[2].checkbox.setChecked(False)
        message = card.add_selected()
        self.assertIn("新增 1 个", message)
        self.assertIn("跳过 1 个", message)
        self.assertIsNotNone(service.get_word_by_text("maintain"))
        self.assertIsNone(service.get_word_by_text("effectiveness"))
        self.assertTrue(card.has_pending)
        card.select_all()
        self.assertIn("已加入 1 个", card.add_selected())
        self.assertFalse(card.has_pending)
        service.close()

    def test_06_existing_word_keeps_multiple_unique_reading_contexts(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        first = service.add_reading_word(
            "maintain",
            "认为；坚持",
            source_context="Researchers maintain that the method works.",
        )
        second = service.add_reading_suggestions(
            (
                SuggestedVocabulary.from_mapping(
                    suggestion("maintain", "认为；坚持")
                ),
            )
        )
        third = service.add_reading_suggestions(
            (
                SuggestedVocabulary.from_mapping(
                    suggestion("maintain", "认为；坚持")
                ),
            )
        )
        word = service.get_word(first.word.id)
        self.assertEqual(word.source, SOURCE_READING)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.context_count, 1)
        self.assertEqual(third.context_count, 0)
        self.assertEqual(len(service.get_word_contexts(word.id)), 2)
        service.close()

    def test_07_valid_chat_response_renders_real_card_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(Path(temp_dir) / "profile.json")
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            raw = response_with(
                [
                    suggestion("conventional", "传统的；常规的", part_of_speech="adj."),
                    suggestion("maintain", "认为；坚持"),
                ]
            )
            fake = FakeDeepSeekClient(((raw[:40], raw[40:]),))
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            window.input.setPlainText(SOURCE_SENTENCE)
            window.send_message()
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            self.assertEqual(len(fake.requests), 1)
            self.assertIn(
                VOCABULARY_BLOCK_START,
                fake.requests[0][0]["content"],
            )
            self.assertEqual(len(window.suggested_vocabulary_cards), 1)
            card = window.suggested_vocabulary_cards[0]
            self.assertEqual(card.items[0].checkbox.text(), "conventional")
            self.assertNotIn("suggested_vocabulary", window.message_texts[-1][1])
            self.assertEqual(card.add_all_pending(), "已加入 2 个单词。")
            saved = learning.get_word_by_text("conventional")
            self.assertEqual(saved.source, SOURCE_READING)
            self.assertEqual(saved.source_context, SOURCE_SENTENCE)
            self.assertEqual(saved.part_of_speech, "adj.")
            self.assertEqual(saved.exam_meaning, "传统的；常规的")
            window.shutdown()
            learning.close()

    def test_08_old_plain_message_and_default_ability_render_without_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(
                Path(temp_dir) / "profile.json",
                english=False,
            )
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            raw = response_with([suggestion("maintain", "认为；坚持")])
            fake = FakeDeepSeekClient(((raw,),))
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            window._add_message("assistant", "旧消息没有 metadata")
            window.input.setPlainText(SOURCE_SENTENCE)
            window.send_message()
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            self.assertEqual(window.message_texts[0][1], "旧消息没有 metadata")
            self.assertEqual(window.suggested_vocabulary_cards, ())
            self.assertEqual(learning.search_words(), ())
            window.shutdown()
            learning.close()

    def test_09_natural_language_adds_current_suggestions_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(Path(temp_dir) / "profile.json")
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            fake = FakeDeepSeekClient()
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            card = window._add_suggested_vocabulary_card(
                (
                    SuggestedVocabulary.from_mapping(
                        suggestion("maintain", "认为；坚持")
                    ),
                )
            )
            window.input.setPlainText("这些都加进去")
            window.send_message()
            self.assertFalse(card.has_pending)
            self.assertIsNotNone(learning.get_word_by_text("maintain"))
            self.assertEqual(fake.requests, [])
            window.shutdown()
            learning.close()

    def test_10_now_study_emits_only_new_words_and_does_not_change_plan(self) -> None:
        learning = WordLearningService(":memory:", now_provider=lambda: BASE)
        planned = learning.get_today_new_words()
        for word in planned:
            learning.mark_review_result(word.id, RESULT_EASY)
        before = learning.get_today_progress()
        self.assertEqual((before.new_words_done, before.new_words_goal), (20, 20))

        card = SuggestedVocabularyCard(
            (
                SuggestedVocabulary.from_mapping(
                    suggestion("effectiveness", "有效性", part_of_speech="n.")
                ),
            ),
            learning,
        )
        self.assertEqual(card.add_all_pending(), "已加入 1 个单词。")
        requested: list[tuple[str, ...]] = []
        card.study_requested.connect(requested.append)
        card.study_button.click()
        self.assertEqual(requested, [card.new_word_ids])

        window = WordLearningWindow(learning)
        words = [learning.get_word(word_id) for word_id in card.new_word_ids]
        self.assertTrue(window.start_session("reading", words=words))
        window.word_card.reveal()
        window.word_card.rate(RESULT_EASY)
        after = learning.get_today_progress()
        self.assertEqual((after.new_words_done, after.new_words_goal), (20, 20))
        window.hide()
        learning.close()

    def test_11_suggestion_limit_defaults_migrates_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "word_learning.db"
            learning = WordLearningService(path, now_provider=lambda: BASE)
            self.assertEqual(learning.settings.reading_vocab_suggestion_limit, 8)
            learning.save_settings(
                LearningSettings(reading_vocab_suggestion_limit=5)
            )
            learning.close()
            restored = WordLearningService(path, now_provider=lambda: BASE)
            self.assertEqual(restored.settings.reading_vocab_suggestion_limit, 5)
            restored.close()

    def test_12_existing_primary_context_is_backfilled_on_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy_context.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE words (
                    id TEXT PRIMARY KEY,
                    word TEXT,
                    meaning TEXT,
                    source_context TEXT,
                    created_at TEXT
                )
                """
            )
            connection.execute(
                "INSERT INTO words VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy_word",
                    "maintain",
                    "认为；坚持",
                    "Researchers maintain that it works.",
                    BASE.isoformat(),
                ),
            )
            connection.commit()
            connection.close()

            learning = WordLearningService(path, now_provider=lambda: BASE)
            self.assertEqual(
                learning.get_word_contexts("legacy_word"),
                ("Researchers maintain that it works.",),
            )
            learning.close()

    def test_13_reading_words_wait_until_next_day_then_have_priority(self) -> None:
        clock = [BASE]
        learning = WordLearningService(
            ":memory:",
            now_provider=lambda: clock[0],
        )
        reading_word = learning.add_reading_word(
            "effectiveness",
            "有效性",
            source_context=SOURCE_SENTENCE,
        ).word
        today_words = learning.get_today_new_words()
        self.assertNotIn(reading_word.id, {word.id for word in today_words})
        self.assertEqual(learning.get_today_progress().new_words_goal, 20)

        clock[0] = BASE.replace(day=6)
        tomorrow_words = learning.get_today_new_words()
        self.assertEqual(tomorrow_words[0].id, reading_word.id)
        learning.close()

    def test_14_reading_detection_avoids_short_word_queries(self) -> None:
        self.assertTrue(is_reading_vocabulary_request(SOURCE_SENTENCE))
        self.assertTrue(
            is_reading_vocabulary_request(
                f"请拆解这道考研长难句：{SOURCE_SENTENCE}"
            )
        )
        self.assertFalse(is_reading_vocabulary_request("conventional 是什么意思？"))
        self.assertFalse(is_reading_vocabulary_request("今天陪我聊聊天吧"))

    def test_15_turn_instruction_is_proximal_and_keeps_one_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(Path(temp_dir) / "profile.json")
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            fake = FakeDeepSeekClient(((response_with([]),),))
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            window.input.setPlainText(SOURCE_SENTENCE)
            window.send_message()
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            self.assertEqual(len(fake.requests), 1)
            request = fake.requests[0]
            self.assertEqual(request[-1], {"role": "user", "content": SOURCE_SENTENCE})
            self.assertEqual(request[-2]["role"], "system")
            self.assertIn(VOCABULARY_BLOCK_START, request[-2]["content"])
            self.assertIn("最多推荐 8 个", request[-2]["content"])
            window.shutdown()
            learning.close()

    def test_16_default_ability_gets_no_turn_level_reading_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = self._profiles(Path(temp_dir) / "profile.json", english=False)
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            fake = FakeDeepSeekClient((("普通回答",),))
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            window.input.setPlainText(SOURCE_SENTENCE)
            window.send_message()
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            self.assertEqual(len(fake.requests), 1)
            request = fake.requests[0]
            self.assertEqual(len(request), 2)
            self.assertEqual(request[-1]["role"], "user")
            self.assertNotIn(VOCABULARY_BLOCK_START, request[0]["content"])
            window.shutdown()
            learning.close()

    def test_17_parser_tolerates_json_code_fence_inside_hidden_block(self) -> None:
        payload = json.dumps(
            {"suggested_vocabulary": [suggestion("maintain", "认为；坚持")]},
            ensure_ascii=False,
        )
        raw = (
            "正常讲解\n"
            f"{VOCABULARY_BLOCK_START}\n```json\n{payload}\n```\n"
            f"{VOCABULARY_BLOCK_END}"
        )
        parsed = parse_reading_response(raw)
        self.assertEqual([item.word for item in parsed.suggestions], ["maintain"])
        self.assertIsNone(parsed.parse_error)
        self.assertIn("最多推荐 5 个", reading_vocabulary_turn_instruction(5))


if __name__ == "__main__":
    unittest.main()
