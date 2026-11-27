"""Offline neural-TTS contracts and preflight, using only tiny local fixtures."""

from __future__ import annotations

import builtins
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from voice.neural_contracts import (
    NeuralRuntimeConfig,
    NeuralSetupInspection,
    QwenCustomVoiceVariant,
    TTSBackendChoice,
    TTSCapabilities,
    VoiceProfile,
    capabilities_for_variant,
    inspect_neural_setup,
)


class NeuralContractTests(unittest.TestCase):
    def test_backend_choices_and_defaults_remain_configuration_only(self):
        self.assertEqual({item.value for item in TTSBackendChoice}, {"auto", "local_neural", "system"})
        profile = VoiceProfile()
        self.assertEqual(profile.backend, TTSBackendChoice.AUTO)
        self.assertEqual(profile.model_path, "")
        self.assertEqual(profile.reference_audio_path, "")
        self.assertEqual(profile.reference_text, "")
        self.assertEqual(profile.speaker_embedding_path, "")
        self.assertEqual(VoiceProfile(backend="system").backend, TTSBackendChoice.SYSTEM)

    def test_variant_capabilities_do_not_promise_unsupported_features(self):
        small = capabilities_for_variant(QwenCustomVoiceVariant.SMALL)
        large = capabilities_for_variant("1.7b")
        self.assertFalse(small.supports_emotion)
        self.assertTrue(large.supports_emotion)
        for capability in (small, large):
            self.assertTrue(capability.supports_speaker_selection)
            self.assertTrue(capability.supports_language_selection)
            self.assertTrue(capability.supports_temperature)
            self.assertFalse(capability.supports_speed)
            self.assertFalse(capability.supports_streaming)
            self.assertFalse(capability.supports_reference_audio)
            self.assertFalse(capability.supports_reference_text)
            self.assertFalse(capability.supports_speaker_embedding)
            self.assertIn("Chinese", capability.languages)
            self.assertIn("English", capability.languages)
            self.assertIn("Auto", capability.languages)
            self.assertIn("vivian", capability.speakers)
        self.assertEqual(replace(small, supports_emotion=True), large)

    def test_unknown_variant_and_backend_fail_clearly(self):
        for value in ("base", "voice_design", "0.6", "3b", None):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    capabilities_for_variant(value)
                with self.assertRaises((TypeError, ValueError)):
                    NeuralRuntimeConfig(model_variant=value)
        with self.assertRaises(ValueError):
            VoiceProfile(backend="download-and-run")

    def test_capability_values_are_typed_and_collections_immutable(self):
        for options in ({"supports_emotion": 1}, {"languages": ["Chinese"]}, {"speakers": ("",)}, {"languages": (1,)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                TTSCapabilities(**options)
        self.assertEqual(VoiceProfile().speaker_id, "serena")

    def test_profiles_are_immutable_and_reference_fields_only_reserved(self):
        profile = VoiceProfile(
            name="未来的角色声音",
            reference_audio_path="reference.wav",
            reference_text="用户提供的参考文字",
            speaker_embedding_path="speaker.bin",
        )
        self.assertEqual(profile.reference_text, "用户提供的参考文字")
        self.assertFalse(capabilities_for_variant("1.7b").supports_reference_audio)
        with self.assertRaises(FrozenInstanceError):
            profile.name = "不可原地修改"

    def test_profile_numeric_values_are_positive_finite_and_not_bool(self):
        for field in ("speed", "temperature"):
            for value in (True, False, 0, -1, float("nan"), float("inf"), float("-inf"), "1.0", None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        VoiceProfile(**{field: value})
        self.assertEqual(VoiceProfile(speed=1.1, temperature=0.75).speed, 1.1)

    def test_profile_string_fields_reject_non_strings_and_nul(self):
        fields = (
            "name", "model_path", "language", "speaker_id", "emotion",
            "reference_audio_path", "reference_text", "speaker_embedding_path",
        )
        for field in fields:
            for value in (None, 7, Path("some-path"), "unsafe\x00value"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        VoiceProfile(**{field: value})

    def test_runtime_device_validation_and_external_paths(self):
        for device in ("auto", "cpu", "cuda", "cuda:0", "cuda:12"):
            with self.subTest(device=device):
                config = NeuralRuntimeConfig(device=device, model_variant="1.7b")
                self.assertEqual(config.device, device)
                self.assertEqual(config.model_variant, QwenCustomVoiceVariant.LARGE)
        for device in ("gpu", "mps", "cuda:-1", "cuda:x", "cuda:0;run", "", None, True):
            with self.subTest(device=device):
                with self.assertRaises(ValueError):
                    NeuralRuntimeConfig(device=device)
        for field in ("python_executable", "model_path"):
            for value in (None, Path("model"), "x\x00y"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        NeuralRuntimeConfig(**{field: value})


class NeuralOfflinePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        self.python = self.root / "external-python.exe"
        # Deliberately not executable: preflight must only inspect it, never run it.
        self.python.write_bytes(b"offline test interpreter placeholder")
        self.metadata = {
            "model_type": "qwen3_tts",
            "tokenizer_type": "qwen3_tts_tokenizer_12hz",
            "tts_model_type": "custom_voice",
            "tts_model_size": "0b6",
        }
        self.write_json("config.json", self.metadata)
        self.write_bytes("model.safetensors", b"fake, not deserialized model weights")
        self.write_json("speech_tokenizer/config.json", {"model_type": "qwen3_tts_tokenizer_12hz"})
        self.write_bytes("speech_tokenizer/model.safetensors", b"fake speech tokenizer weights")
        for name in (
            "generation_config.json", "preprocessor_config.json", "tokenizer_config.json",
            "vocab.json", "speech_tokenizer/preprocessor_config.json",
        ):
            self.write_json(name, {"fixture": True})
        self.write_bytes("merges.txt", b"#version: 0.2\na b\n")
        self.config = NeuralRuntimeConfig(
            python_executable=str(self.python), model_path=str(self.model), device="cpu",
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_bytes(self, relative, payload):
        path = self.model / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def write_json(self, relative, value):
        return self.write_bytes(relative, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def index(self, mapping, *, prefix=""):
        return self.write_json(prefix + "model.safetensors.index.json", {"weight_map": mapping})

    def inspect(self, **changes):
        return inspect_neural_setup(replace(self.config, **changes))

    def assert_offline_only(self, result):
        self.assertFalse(result.dependencies_checked)
        self.assertFalse(result.runtime_available)
        self.assertFalse(result.weights_validated)

    def test_complete_nonempty_files_do_not_claim_runtime_available(self):
        result = self.inspect()
        self.assertTrue(result.files_present)
        self.assertTrue(result.configuration_matches)
        self.assertTrue(result.local_checks_passed)
        self.assertEqual(result.issues, ())
        self.assertEqual(set(result.weight_files), {
            self.model / "model.safetensors",
            self.model / "speech_tokenizer/model.safetensors",
        })
        self.assert_offline_only(result)
        with self.assertRaises(TypeError):
            NeuralSetupInspection(True, True, (), runtime_available=True)

    def test_missing_python_or_model_directory_is_not_ready(self):
        for changes in (
            {"python_executable": ""},
            {"python_executable": str(self.root / "absent-python.exe")},
            {"python_executable": str(self.model)},
            {"model_path": ""},
            {"model_path": str(self.root / "missing-model")},
            {"model_path": str(self.python)},
        ):
            with self.subTest(changes=changes):
                result = self.inspect(**changes)
                self.assertFalse(result.files_present)
                self.assertFalse(result.local_checks_passed)
                self.assertTrue(result.issues)
                self.assert_offline_only(result)

    def test_empty_interpreter_is_reported_without_execution(self):
        self.python.write_bytes(b"")
        result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertTrue(any("external-python" in issue for issue in result.issues))

    def test_missing_empty_or_directory_weights_are_rejected(self):
        weight = self.model / "model.safetensors"
        weight.unlink()
        self.assertFalse(self.inspect().files_present)
        weight.touch()
        self.assertFalse(self.inspect().files_present)
        weight.unlink()
        weight.mkdir()
        result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertFalse(result.local_checks_passed)

    def test_speech_tokenizer_files_are_required_too(self):
        weight = self.model / "speech_tokenizer/model.safetensors"
        weight.unlink()
        result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertTrue(any("speech_tokenizer" in issue for issue in result.issues))
        weight.write_bytes(b"restored placeholder")
        (self.model / "speech_tokenizer/config.json").unlink()
        self.assertFalse(self.inspect().local_checks_passed)

    def test_model_type_tokenizer_type_and_variant_must_match(self):
        invalid_fields = {
            "model_type": "unrelated_model",
            "tokenizer_type": "qwen3_tts_tokenizer_25hz",
            "tts_model_type": "base",
            "tts_model_size": "1b7",
        }
        for key, value in invalid_fields.items():
            with self.subTest(key=key):
                self.write_json("config.json", {**self.metadata, key: value})
                result = self.inspect()
                self.assertTrue(result.files_present)
                self.assertFalse(result.configuration_matches)
                self.assertFalse(result.local_checks_passed)
                self.assertTrue(any(key in issue for issue in result.issues))
        self.write_json("config.json", {**self.metadata, "tts_model_size": "1b7"})
        self.assertTrue(self.inspect(model_variant="1.7b").local_checks_passed)

    def test_missing_config_fields_and_malformed_json_fail_safely(self):
        for value in ({}, [], "not an object", None):
            with self.subTest(value=value):
                self.write_json("config.json", value)
                result = self.inspect()
                self.assertFalse(result.configuration_matches)
                self.assertFalse(result.local_checks_passed)
        for raw in (b"{bad json}", b"\xff\xfe\xff", b""):
            with self.subTest(raw=raw):
                self.write_bytes("config.json", raw)
                self.assertFalse(self.inspect().local_checks_passed)

    def test_invalid_tokenizer_metadata_is_not_a_passing_preflight(self):
        for value in ([], {}, {"model_type": "unrelated"}):
            with self.subTest(value=value):
                self.write_json("speech_tokenizer/config.json", value)
                result = self.inspect()
                self.assertTrue(result.files_present)
                self.assertFalse(result.configuration_matches)
                self.assertFalse(result.local_checks_passed)

    def test_text_resources_and_processor_metadata_must_be_present(self):
        for name in (
            "generation_config.json", "preprocessor_config.json", "tokenizer_config.json",
            "vocab.json", "merges.txt", "speech_tokenizer/preprocessor_config.json",
        ):
            with self.subTest(name=name):
                path = self.model / name
                original = path.read_bytes()
                path.unlink()
                self.assertFalse(self.inspect().files_present)
                path.write_bytes(b"")
                self.assertFalse(self.inspect().files_present)
                path.write_bytes(original)

    def test_text_resource_malformed_json_fails_metadata_check(self):
        self.write_bytes("tokenizer_config.json", b"{broken}")
        result = self.inspect()
        self.assertTrue(result.files_present)
        self.assertFalse(result.configuration_matches)

    def test_path_errors_are_reported_instead_of_escaping(self):
        for method, error in (("expanduser", RuntimeError("cannot resolve user")),
                              ("is_dir", PermissionError("access denied")),
                              ("exists", PermissionError("index inaccessible"))):
            with self.subTest(method=method), patch.object(Path, method, side_effect=error):
                result = self.inspect()
                self.assertFalse(result.local_checks_passed)
                self.assertTrue(result.issues)
                self.assert_offline_only(result)

    def test_nonlocal_and_relative_paths_are_rejected_before_lookup(self):
        for path in (r"\\server\share\model", "//server/share/python.exe", "https://example.test/model", "relative/model"):
            with self.subTest(path=path), patch.object(Path, "is_dir", side_effect=AssertionError("network lookup")), patch.object(Path, "is_file", side_effect=AssertionError("network lookup")):
                result = self.inspect(python_executable=path, model_path=path)
                self.assertFalse(result.local_checks_passed)

    def test_resolved_path_containment_without_symlink_privileges(self):
        original = Path.resolve
        weight = self.model / "model.safetensors"

        def outside_for_weight(path, *args, **kwargs):
            if path == weight:
                return self.root / "outside.safetensors"
            return original(path, *args, **kwargs)

        with patch.object(Path, "resolve", outside_for_weight):
            result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertTrue(any("目录之外" in issue for issue in result.issues))

    def test_all_indexed_shards_including_tokenizer_are_checked(self):
        self.index({"layer.a": "part-1.safetensors", "layer.b": "nested/part-2.safetensors"})
        self.write_bytes("part-1.safetensors", b"part one")
        self.write_bytes("nested/part-2.safetensors", b"part two")
        self.index({"encoder": "codec-1.safetensors"}, prefix="speech_tokenizer/")
        self.write_bytes("speech_tokenizer/codec-1.safetensors", b"codec part")
        result = self.inspect()
        self.assertTrue(result.local_checks_passed)
        self.assertEqual(set(result.weight_files), {
            self.model / "part-1.safetensors",
            self.model / "nested/part-2.safetensors",
            self.model / "speech_tokenizer/codec-1.safetensors",
        })
        self.assert_offline_only(result)

    def test_missing_and_empty_shards_do_not_fall_back_to_single_file(self):
        self.index({"a": "present.safetensors", "b": "absent.safetensors"})
        self.write_bytes("present.safetensors", b"present")
        self.assertTrue((self.model / "model.safetensors").is_file())
        result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertFalse(result.local_checks_passed)
        self.assertNotIn(self.model / "model.safetensors", result.weight_files)
        self.write_bytes("absent.safetensors", b"")
        self.assertFalse(self.inspect().files_present)

    def test_shared_shard_is_deduplicated(self):
        self.index({"first": "part.safetensors", "second": "part.safetensors"})
        part = self.write_bytes("part.safetensors", b"shared")
        result = self.inspect()
        self.assertTrue(result.local_checks_passed)
        self.assertEqual(result.weight_files.count(part), 1)

    def test_bad_index_structure_and_values_fail_without_loading_weights(self):
        for mapping in ({}, [], None, {"": "part.safetensors"}, {"a": ""}, {"a": 1}, {"a": "x\x00.safetensors"}):
            with self.subTest(mapping=mapping):
                self.index(mapping)
                self.assertFalse(self.inspect().local_checks_passed)
        self.write_bytes("model.safetensors.index.json", b"{malformed}")
        self.assertFalse(self.inspect().local_checks_passed)

    def test_shard_traversal_absolute_windows_and_wrong_extensions_rejected(self):
        (self.root / "outside.safetensors").write_bytes(b"exists outside model root")
        paths = (
            "../outside.safetensors", "nested/../../outside.safetensors",
            "nested/../model.safetensors", "..\\outside.safetensors",
            "/absolute.safetensors", "C:\\weights\\part.safetensors",
            "C:relative.safetensors", "\\rooted.safetensors",
            "\\\\server\\share\\part.safetensors", "part.safetensors:stream",
            "part.bin", "part.py", ".", " ",
        )
        for name in paths:
            with self.subTest(name=name):
                self.index({"layer": name})
                result = self.inspect()
                self.assertFalse(result.files_present)
                self.assertFalse(result.local_checks_passed)
                self.assertTrue(result.issues)

    def test_resolved_symlink_shard_cannot_escape_model_root(self):
        outside = self.root / "outside.safetensors"
        outside.write_bytes(b"outside")
        link = self.model / "escape.safetensors"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable on this host: {exc}")
        self.index({"layer": "escape.safetensors"})
        result = self.inspect()
        self.assertFalse(result.files_present)
        self.assertFalse(result.local_checks_passed)

    def test_metadata_and_index_size_limits_fail_with_clear_issues(self):
        self.write_bytes("config.json", b" " * (4 * 1024 * 1024 + 1))
        result = self.inspect()
        self.assertFalse(result.local_checks_passed)
        self.assertTrue(any("4 MiB" in issue for issue in result.issues))
        self.write_json("config.json", self.metadata)
        self.index({str(index): f"part-{index}.safetensors" for index in range(1025)})
        result = self.inspect()
        self.assertFalse(result.local_checks_passed)
        self.assertTrue(any("1024" in issue for issue in result.issues))

    def test_preflight_is_read_only_and_never_opens_weight_contents(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        original_open = Path.open

        def metadata_only_open(path, *args, **kwargs):
            if path.suffix == ".safetensors":
                raise AssertionError("Preflight tried to read tensor contents")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", metadata_only_open):
            result = self.inspect()
        self.assertTrue(result.local_checks_passed)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_fresh_module_import_and_preflight_do_not_start_neural_runtime(self):
        forbidden_modules = {
            "torch", "torchaudio", "transformers", "qwen_tts", "huggingface_hub",
            "safetensors", "accelerate", "numpy", "soundfile",
        }
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".", 1)[0] in forbidden_modules:
                raise AssertionError(f"Offline contracts imported a neural dependency: {name}")
            return original_import(name, *args, **kwargs)

        module_name = "_offline_neural_contract_probe"
        source = Path(__file__).resolve().parents[1] / "voice" / "neural_contracts.py"
        spec = importlib.util.spec_from_file_location(module_name, source)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        forbidden_calls = (
            "subprocess.Popen", "subprocess.run", "subprocess.call",
            "os.system", "socket.create_connection", "urllib.request.urlopen",
        )
        with ExitStack() as stack:
            for target in forbidden_calls:
                stack.enter_context(patch(target, side_effect=AssertionError(f"Offline preflight called {target}")))
            stack.enter_context(patch("builtins.__import__", guarded_import))
            stack.enter_context(patch.dict(sys.modules, {module_name: module}))
            spec.loader.exec_module(module)
            config = module.NeuralRuntimeConfig(
                python_executable=str(self.python), model_path=str(self.model),
            )
            result = module.inspect_neural_setup(config)
            self.assertTrue(result.local_checks_passed)
            self.assert_offline_only(result)

    def test_preflight_rejects_wrong_config_object(self):
        for value in (None, {}, VoiceProfile(), str(self.model)):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    inspect_neural_setup(value)


if __name__ == "__main__":
    unittest.main()
