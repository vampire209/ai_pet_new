"""English Learning 2.0 storage, scheduling, UI, and integration tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ai.ability_modes import ABILITY_ENGLISH_TEACHER
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.profile import (
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_NORMAL,
    PetProfile,
)
from ai.profile_service import ProfileService
from chat_window import ChatWindow, clean_chat_markdown
from config import AppConfig
from models.recurrence import REPEAT_DAILY
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from word_learning.models import (
    LearningSettings,
    RESULT_EASY,
    RESULT_FORGOT,
    RESULT_HARD,
    SOURCE_READING,
    WordEntry,
)
from word_learning.repository import WordLearningRepository
from word_learning.service import WordLearningService
from word_learning.widgets import WordCardWidget
from word_learning.window import WordLearningWindow


BASE = datetime(2026, 9, 5, 10, 0)


class WordLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["word-learning-tests"])

    def test_01_default_settings_persist_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "word_learning.db"
            service = WordLearningService(path, now_provider=lambda: BASE)
            self.assertEqual(service.settings.daily_new_word_count, 20)
            self.assertEqual(service.settings.daily_review_limit, 50)
            self.assertFalse(service.settings.word_reminder_enabled)
            service.save_settings(
                LearningSettings(daily_new_word_count=30, daily_review_limit=None)
            )
            service.close()

            restored = WordLearningService(path, now_provider=lambda: BASE)
            self.assertEqual(restored.settings.daily_new_word_count, 30)
            self.assertIsNone(restored.settings.daily_review_limit)
            restored.close()

    def test_02_additive_database_upgrade_keeps_legacy_word_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE words (id TEXT PRIMARY KEY, word TEXT, meaning TEXT)"
            )
            connection.execute(
                "INSERT INTO words VALUES ('old_1', 'legacy', '遗留的')"
            )
            connection.execute(
                "CREATE TABLE learning_settings (id INTEGER PRIMARY KEY, daily_new_word_count INTEGER)"
            )
            connection.execute("INSERT INTO learning_settings VALUES (1, 30)")
            connection.commit()
            connection.close()

            repository = WordLearningRepository(path)
            word = repository.get_word("old_1")
            self.assertIsNotNone(word)
            self.assertEqual(word.word, "legacy")
            self.assertEqual(word.review_count, 0)
            self.assertEqual(repository.load_settings().daily_new_word_count, 30)
            self.assertEqual(repository.load_settings().daily_review_limit, 50)
            self.assertEqual(
                repository.load_settings().reading_vocab_suggestion_limit,
                8,
            )
            repository.close()

    def test_03_add_duplicate_search_star_and_delete(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        first = service.add_word("Conventional")
        second = service.add_word("conventional")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.word.id, second.word.id)
        self.assertEqual(service.search_words("conven"), (first.word,))
        starred = service.set_starred(first.word.id, True)
        self.assertTrue(starred.is_starred)
        self.assertTrue(service.remove_word(first.word.id))
        self.assertEqual(service.search_words(), ())
        service.close()

    def test_04_reading_word_preserves_source_and_context(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        result = service.add_reading_word(
            "challenge",
            "挑战；质疑",
            source_context="This conventional approach has recently been challenged.",
        )
        restored = service.get_word(result.word.id)
        self.assertEqual(restored.source, SOURCE_READING)
        self.assertIn("approach", restored.source_context)
        service.close()

    def test_05_review_results_update_stable_intervals_and_counts(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        forgot_word = service.add_word("conventional").word
        easy_word = service.add_word("significant").word
        hard_word = service.add_word("challenge").word

        forgotten = service.mark_review_result(forgot_word.id, RESULT_FORGOT)
        easy = service.mark_review_result(easy_word.id, RESULT_EASY)
        hard = service.mark_review_result(hard_word.id, RESULT_HARD)

        self.assertEqual(
            datetime.fromisoformat(forgotten.next_review_at),
            BASE + timedelta(minutes=10),
        )
        self.assertEqual(
            datetime.fromisoformat(hard.next_review_at),
            BASE + timedelta(days=1),
        )
        self.assertEqual(
            datetime.fromisoformat(easy.next_review_at),
            BASE + timedelta(days=7),
        )
        self.assertEqual(forgotten.wrong_count, 1)
        self.assertEqual(forgotten.correct_count, 0)
        self.assertEqual(easy.correct_count, 1)
        self.assertGreater(easy.mastery, hard.mastery)
        second_easy = service.mark_review_result(
            easy_word.id,
            RESULT_EASY,
            count_for_daily=False,
        )
        self.assertEqual(
            datetime.fromisoformat(second_easy.next_review_at),
            BASE + timedelta(days=14),
        )
        service.close()

    def test_06_due_filter_excludes_future_and_already_reviewed_today(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        due = service.add_word("maintain").word
        future = service.add_word("assume").word
        today = service.add_word("approach").word
        service.repository.update_word(
            replace(
                due,
                review_count=2,
                next_review_at=(BASE - timedelta(days=4)).isoformat(),
            )
        )
        service.repository.update_word(
            replace(
                future,
                review_count=2,
                next_review_at=(BASE + timedelta(days=1)).isoformat(),
            )
        )
        service.repository.update_word(
            replace(
                today,
                review_count=2,
                last_reviewed_at=BASE.isoformat(),
                next_review_at=(BASE - timedelta(minutes=1)).isoformat(),
            )
        )
        self.assertEqual([word.word for word in service.get_today_reviews()], ["maintain"])
        service.close()

    def test_07_new_word_session_and_daily_stats_are_local(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        words = service.get_today_new_words()
        self.assertEqual(len(words), 20)
        self.assertTrue(all(word.review_count == 0 for word in words))
        service.mark_review_result(words[0].id, RESULT_EASY)
        progress = service.get_today_progress()
        stats = service.get_stats()
        self.assertEqual(progress.new_words_done, 1)
        self.assertEqual(progress.new_words_goal, 20)
        self.assertEqual(stats.today.correct_count, 1)
        self.assertEqual(stats.today.new_words_count, 1)
        self.assertEqual(stats.total_words, 20)
        service.close()

    def test_08_word_card_hides_answer_until_revealed(self) -> None:
        card = WordCardWidget()
        word = WordEntry.create(
            "conventional",
            "传统的；常规的",
            collocations="conventional wisdom 传统观念",
            example="It is a conventional method.",
        )
        card.set_word(word)
        self.assertFalse(card.is_revealed)
        self.assertEqual(card.front_word.text(), "conventional")
        self.assertEqual(card.faces.currentIndex(), 0)
        card.reveal("fuzzy")
        self.assertTrue(card.is_revealed)
        self.assertEqual(card.meaning.text(), "传统的；常规的")
        self.assertEqual(card.initial_judgment, "fuzzy")

    def test_09_learning_window_has_four_tabs_and_relationship_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            profiles.save(
                PetProfile.for_mode(
                    RELATIONSHIP_CLINGY_GIRLFRIEND,
                    ability_mode=ABILITY_ENGLISH_TEACHER,
                )
            )
            service = WordLearningService(":memory:", now_provider=lambda: BASE)
            window = WordLearningWindow(service, profile_service=profiles)
            self.assertEqual(window.tabs.count(), 4)
            self.assertTrue(window.start_session("new"))
            self.assertIn("陪你", window.study_companion.text())
            self.assertFalse(window.word_card.is_revealed)
            profiles.save(
                PetProfile.for_mode(
                    RELATIONSHIP_NORMAL,
                    ability_mode=ABILITY_ENGLISH_TEACHER,
                )
            )
            self.assertTrue(window.start_session("new"))
            self.assertIn("开始今天的单词学习", window.study_companion.text())
            self.assertNotIn("陪你", window.study_companion.text())
            window.hide()
            service.close()

    def test_10_reminder_setting_reuses_one_existing_daily_todo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            todos = TodoManager(Path(temp_dir) / "tasks.json")
            service = WordLearningService(":memory:", now_provider=lambda: BASE)
            window = WordLearningWindow(service, todo_manager=todos)
            enabled = LearningSettings(
                word_reminder_enabled=True,
                word_reminder_time="19:00",
            )
            service.save_settings(enabled)
            window._sync_existing_reminder(enabled)
            saved = service.settings
            self.assertIsNotNone(saved.word_reminder_task_id)
            self.assertEqual(len(todos.get_tasks()), 1)
            self.assertEqual(todos.get_tasks()[0].repeat_type, REPEAT_DAILY)
            window._sync_existing_reminder(saved)
            self.assertEqual(len(todos.get_tasks()), 1)
            window._sync_existing_reminder(
                replace(saved, word_reminder_enabled=False)
            )
            self.assertEqual(todos.get_tasks(), ())
            window.hide()
            service.close()

    def test_11_lightweight_markdown_cleanup_is_targeted(self) -> None:
        raw = "### **conventional**\n- `常规的`\n---\n2 * 3 = 6"
        cleaned = clean_chat_markdown(raw)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("###", cleaned)
        self.assertIn("• 常规的", cleaned)
        self.assertIn("2 * 3 = 6", cleaned)

    def test_12_chat_learning_commands_are_local_but_word_query_stays_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            profiles.save(
                PetProfile(ability_mode=ABILITY_ENGLISH_TEACHER)
            )
            learning = WordLearningService(":memory:", now_provider=lambda: BASE)
            fake = FakeDeepSeekClient()
            window = ChatWindow(
                ChatService(client=fake),
                profile_service=profiles,
                word_learning_service=learning,
            )
            requests: list[str] = []
            window.word_learning_requested.connect(requests.append)
            window.input.setPlainText("开始背单词")
            window.send_message()
            self.assertEqual(requests, ["new"])
            self.assertEqual(fake.requests, [])
            self.assertFalse(
                window._try_handle_word_learning_action("conventional 是什么意思？")
            )
            self.assertTrue(
                window._try_handle_word_learning_action(
                    "把 conventional 加入单词本，原句：A conventional approach works."
                )
            )
            saved = learning.get_word_by_text("conventional")
            self.assertEqual(saved.source, SOURCE_READING)
            self.assertIn("approach", saved.source_context)
            self.assertTrue(window._try_handle_word_learning_action("每天背 30 个"))
            self.assertEqual(learning.settings.daily_new_word_count, 30)
            self.assertTrue(window._try_handle_word_learning_action("从单词本删除 conventional"))
            self.assertIsNone(learning.get_word_by_text("conventional"))
            self.assertTrue(window._try_handle_word_learning_action("开始复习"))
            self.assertEqual(requests, ["new", "review"])
            self.assertEqual(fake.requests, [])
            self.assertEqual(
                profiles.profile.ability_mode,
                ABILITY_ENGLISH_TEACHER,
            )
            window.shutdown()
            learning.close()

    def test_13_restart_keeps_words_review_stats_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "word_learning.db"
            service = WordLearningService(path, now_provider=lambda: BASE)
            service.save_settings(
                LearningSettings(daily_new_word_count=30, daily_review_limit=100)
            )
            added = service.add_reading_word(
                "conventional",
                "传统的；常规的",
                source_context="A conventional approach works.",
            ).word
            reviewed = service.mark_review_result(added.id, RESULT_EASY)
            expected_next_review = reviewed.next_review_at
            service.close()

            restored = WordLearningService(path, now_provider=lambda: BASE)
            word = restored.get_word_by_text("conventional")
            self.assertIsNotNone(word)
            self.assertEqual(word.source, SOURCE_READING)
            self.assertIn("approach", word.source_context)
            self.assertEqual(word.next_review_at, expected_next_review)
            self.assertEqual(word.review_count, 1)
            self.assertEqual(restored.settings.daily_new_word_count, 30)
            self.assertEqual(restored.settings.daily_review_limit, 100)
            stats = restored.get_stats()
            self.assertEqual(stats.today.new_words_count, 1)
            self.assertEqual(stats.today.correct_count, 1)
            restored.close()

    def test_14_chat_header_opens_one_shared_learning_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            todo_manager = TodoManager(Path(temp_dir) / "tasks.json")
            reminder_manager = ReminderManager(todo_manager)
            profile_service = ProfileService(Path(temp_dir) / "profile.json")
            profile_service.save(PetProfile(ability_mode=ABILITY_ENGLISH_TEACHER))
            learning = WordLearningService(
                Path(temp_dir) / "word_learning.db",
                now_provider=lambda: BASE,
            )
            pet = PetWindow(
                AppConfig.load(),
                todo_manager,
                reminder_manager,
                chat_service=ChatService(client=FakeDeepSeekClient()),
                profile_service=profile_service,
                word_learning_service=learning,
            )
            pet._open_chat_window()
            pet.chat_window.word_learning_button.click()
            self.app.processEvents()
            first = pet.word_learning_window
            self.assertIsNotNone(first)
            self.assertTrue(first.isVisible())
            pet.chat_window.word_learning_requested.emit("wordbook")
            self.app.processEvents()
            self.assertIs(pet.word_learning_window, first)
            self.assertEqual(first.tabs.currentIndex(), 1)
            reading_word = learning.add_reading_word(
                "effectiveness",
                "有效性",
                source_context="Technology reduced their effectiveness.",
            ).word
            pet.chat_window.word_learning_words_requested.emit((reading_word.id,))
            self.app.processEvents()
            self.assertIs(first.main_stack.currentWidget(), first.study_page)
            self.assertEqual(first.word_card.word.id, reading_word.id)
            pet.close()
            self.app.processEvents()

    def test_15_due_review_priority_is_stable(self) -> None:
        service = WordLearningService(":memory:", now_provider=lambda: BASE)
        oldest = service.add_word("maintain").word
        weaker = service.add_word("assume").word
        more_wrong = service.add_word("challenge").word
        for word, overdue_days, mastery, wrong_count in (
            (oldest, 5, 80, 0),
            (weaker, 2, 10, 0),
            (more_wrong, 2, 10, 3),
        ):
            service.repository.update_word(
                replace(
                    word,
                    review_count=2,
                    next_review_at=(BASE - timedelta(days=overdue_days)).isoformat(),
                    mastery=mastery,
                    wrong_count=wrong_count,
                )
            )
        self.assertEqual(
            [word.word for word in service.get_today_reviews()],
            ["maintain", "challenge", "assume"],
        )
        service.close()


if __name__ == "__main__":
    unittest.main()
