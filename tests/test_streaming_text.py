"""Pure incremental sentence tests; no model, audio, API, or Qt needed."""

from __future__ import annotations

import unittest

from voice.streaming_text import SpeechSegmenter


def segments(chunks):
    parser = SpeechSegmenter()
    result = []
    for chunk in chunks:
        result.extend(parser.feed(chunk))
    result.extend(parser.finish())
    return result


class StreamingTextTests(unittest.TestCase):
    def assert_any_split(self, source, expected):
        self.assertEqual(segments([source]), expected)
        self.assertEqual(segments(list(source)), expected)
        for split in range(len(source) + 1):
            with self.subTest(split=split):
                self.assertEqual(segments([source[:split], source[split:]]), expected)

    def test_first_sentence_ready_before_reply_finishes(self):
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("你好。第二"), ["你好。"])
        self.assertEqual(parser.feed("句仍在生成"), [])
        self.assertEqual(parser.feed("！第三句？"), ["第二句仍在生成。", "第三句？"])
        self.assertEqual(parser.finish(), [])

    def test_chinese_and_english_sentence_boundaries(self):
        self.assert_any_split(
            "第一句。Hello world. How are you? Fine! 最后一句没有句号",
            ["第一句。", "Hello world.", "How are you?", "Fine。", "最后一句没有句号"],
        )

    def test_decimal_is_not_split_even_when_dot_is_chunk_tail(self):
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("Pi is 3."), [])
        self.assertEqual(parser.feed("14 and 0."), [])
        self.assertEqual(parser.feed("25. Next"), ["Pi is 3.14 and 0.25."])
        self.assertEqual(parser.finish(), ["Next"])
        self.assert_any_split("数值是 3.14。Version 1.2.3 works. End.",
                              ["数值是 3.14。", "Version 1.2.3 works.", "End."])

    def test_multiple_paragraphs_and_markdown(self):
        self.assert_any_split(
            "# 标题\n\n**第一段**。\n- 第二段。\n*第三段*没有句号",
            ["标题", "第一段。", "第二段。", "第三段没有句号"],
        )

    def test_common_abbreviations_are_not_separate_sentences(self):
        self.assert_any_split(
            "Dr. Smith works in the U.S. office. Try examples, e.g. this one.",
            ["Dr. Smith works in the U.S. office.", "Try examples, e.g. this one."],
        )

    def test_unresolved_markup_has_a_size_limit(self):
        parser = SpeechSegmenter()
        with self.assertRaisesRegex(ValueError, "过长"):
            parser.feed("`" + "没有闭合" * 5000)

    def test_fenced_code_hidden_at_every_split(self):
        self.assert_any_split(
            "开头。\n```python\nprint('绝不能朗读！')\n```\n后文。",
            ["开头。", "后文。"],
        )

    def test_tilde_and_long_backtick_fences(self):
        for fence in ("~~~", "````"):
            with self.subTest(fence=fence):
                self.assert_any_split(
                    "前文\n  " + fence + "json\n{\"机密\":\"不读。\"}\n"
                    "```\n" + fence + "\n后文。",
                    ["前文", "后文。"],
                )

    def test_unclosed_or_partially_opened_fence_is_suppressed(self):
        for source in ("先说。\n```", "先说。\n```python\n禁止朗读。", "先说。\n~~~隐藏内容。"):
            self.assert_any_split(source, ["先说。"])

    def test_inline_code_waits_for_closure(self):
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("使用 `print('hello."), [])
        self.assertEqual(parser.feed("')` 即可。"), ["使用 print('hello.') 即可。"])
        self.assertEqual(parser.finish(), [])
        self.assert_any_split("请读 ``a`b`` 和 `3.14`。", ["请读 ab 和 3.14。"])

    def test_markdown_links_wait_and_speak_only_labels(self):
        self.assert_any_split(
            "查看[官方文档](https://example.com/a_(b)?x=1.2)即可。",
            ["查看官方文档即可。"],
        )
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("[第一句。第二句](https://exam"), [])
        self.assertEqual(parser.feed("ple.com)后文。"), ["第一句。", "第二句后文。"])
        self.assertEqual(parser.finish(), [])

    def test_images_and_reference_targets_never_spoken(self):
        self.assert_any_split(
            "图片![不朗读。](https://example.com/a.png)结束。\n"
            "[链接][ref]。\n[ref]: https://example.com/hidden\n后续。",
            ["图片结束。", "链接。", "后续。"],
        )

    def test_unfinished_link_discards_target_at_finish(self):
        self.assert_any_split("打开[文档](https://example.com/secret", ["打开文档"])

    def test_plain_url_never_leaks_partial_path(self):
        self.assert_any_split("链接 https://example.com/a?token=do.not.read 后文。", ["链接 后文。"])

    def test_long_sentence_uses_bounded_soft_splits_without_losing_text(self):
        source = "甲" * 195 + "，" + "乙" * 330 + "尾。"
        result = segments([source])
        self.assertEqual("".join(result), source)
        self.assertEqual(len(result[0]), 196)
        self.assertTrue(all(len(segment) <= 240 for segment in result))
        self.assertEqual(segments(list(source)), result)
        unpunctuated = "abcdefghij" * 75
        result = segments(list(unpunctuated))
        self.assertEqual("".join(result), unpunctuated)
        self.assertTrue(all(len(segment) <= 240 for segment in result))

    def test_emoji_or_formatting_only_does_not_create_speech(self):
        self.assert_any_split("😊💙✨！\n***\n😀。", [])

    def test_reset_discards_unspoken_tail_and_parser_modes(self):
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("完成。尚未读出的尾部"), ["完成。"])
        parser.reset()
        self.assertEqual(parser.finish(), [])
        parser.feed("\n```python\n不读")
        parser.reset()
        self.assertEqual(parser.feed("再次正常。"), ["再次正常。"])

    def test_finish_flushes_once_and_next_turn_is_independent(self):
        parser = SpeechSegmenter()
        self.assertEqual(parser.feed("无标点尾句"), [])
        self.assertEqual(parser.finish(), ["无标点尾句"])
        self.assertEqual(parser.finish(), [])
        self.assertEqual(parser.feed("下一轮。"), ["下一轮。"])

    def test_invalid_chunk_type_has_clear_error(self):
        with self.assertRaises(TypeError):
            SpeechSegmenter().feed(None)


if __name__ == "__main__":
    unittest.main()
