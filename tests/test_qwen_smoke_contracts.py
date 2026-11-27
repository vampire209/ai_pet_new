"""No neural SDK is imported by these smoke-boundary regression tests."""

import builtins
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts.qwen_voice_engine import REQUIRED_FILES, enable_offline_environment, validate_model_directory, validate_request
from scripts.test_qwen_tts import DEMO_TEXTS, make_parser
from scripts import test_qwen_tts as smoke


class QwenSmokeContractsTests(unittest.TestCase):
    def test_exactly_six_requested_demos(self):
        self.assertEqual([name for name, _ in DEMO_TEXTS], [
            "01_normal.wav", "02_soft.wav", "03_happy.wav", "04_long.wav",
            "05_mixed.wav", "06_number.wav",
        ])
        for _, text in DEMO_TEXTS:
            validate_request(text, "Serena", "Chinese", 1.0)

    def test_voice_language_speed_and_length_validation(self):
        for args in (("", "Serena", "Chinese", 1), ("你" * 241, "Serena", "Chinese", 1),
                     ("你好", "Unknown", "Chinese", 1), ("你好", "Serena", "Wrong", 1),
                     ("你好", "Serena", "Chinese", True), ("你好", "Serena", "Chinese", float("nan"))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_request(*args)

    def test_cli_requires_opt_in_and_defaults_to_ram_guard(self):
        options = make_parser().parse_args(["--model-dir", "example"])
        self.assertFalse(options.smoke)
        self.assertFalse(options.play)
        self.assertFalse(options.warmup_only)
        self.assertEqual(options.min_free_gib, 4.0)

    def test_insufficient_ram_stops_before_importing_or_loading_model(self):
        engine = MagicMock()
        sampler = MagicMock()
        sampler.start.return_value = {"system_available_bytes": 1024 ** 3}
        sampler.report.return_value = {}
        with tempfile.TemporaryDirectory() as directory, patch.object(smoke, "QwenVoiceEngine", return_value=engine), patch.object(smoke, "ResourceSampler", return_value=sampler), patch.object(smoke, "model_bytes", return_value=2500000000), patch.object(smoke, "save_report") as save, patch.object(smoke, "progress"), patch.dict("os.environ"):
            result = smoke.main(["--model-dir", directory, "--output-dir", directory, "--smoke"])
        self.assertEqual(result, 1)
        engine.load.assert_not_called()
        engine.synthesize.assert_not_called()
        report = save.call_args.args[1]
        self.assertEqual(report["failure_phase"], "resource_preflight")
        self.assertFalse(report["all_six_demos_generated"])

    def test_offline_flags_override_instead_of_inheriting_false(self):
        with patch.dict("os.environ", {"HF_HUB_OFFLINE": "0"}):
            enable_offline_environment()
            import os
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"], "1")

    def test_remote_and_relative_model_paths_rejected(self):
        for path in ("", "Qwen/model", "https://example.test/model", r"\\server\share\model"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_model_directory(path)

    def test_wrong_model_and_codec_rejected_without_neural_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in REQUIRED_FILES:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b"x" * 1024)
            config = {"model_type": "qwen3_tts", "tokenizer_type": "qwen3_tts_tokenizer_12hz",
                      "tts_model_size": "0b6", "tts_model_type": "custom_voice"}
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            codec = root / "speech_tokenizer/config.json"
            codec.write_text('{"model_type":"qwen3_tts_tokenizer_12hz"}', encoding="utf-8")
            self.assertEqual(validate_model_directory(root), root.resolve())
            codec.write_text('{"model_type":"wrong"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "tokenizer"):
                validate_model_directory(root)
            config["tts_model_size"] = "1b7"
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "0.6B"):
                validate_model_directory(root)

    def test_importing_engine_never_imports_model_or_starts_process(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"torch", "qwen_tts", "numpy", "transformers", "librosa"}:
                raise AssertionError("Neural dependency imported at module startup")
            return original(name, *args, **kwargs)

        source = Path(__file__).resolve().parents[1] / "scripts/qwen_voice_engine.py"
        spec = importlib.util.spec_from_file_location("_qwen_engine_probe", source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module}), patch("builtins.__import__", guarded), patch("subprocess.Popen", side_effect=AssertionError("spawn")):
            spec.loader.exec_module(module)


if __name__ == "__main__":
    unittest.main()
