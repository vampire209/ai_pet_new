"""Offline contracts for a future neural-TTS adapter; not connected to runtime.

No model SDK, process launch, network access or weight deserialization belongs
here. A successful setup inspection proves only local file presence/configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


class TTSBackendChoice(str, Enum):
    AUTO = "auto"
    LOCAL_NEURAL = "local_neural"
    SYSTEM = "system"


class QwenCustomVoiceVariant(str, Enum):
    SMALL = "0.6b"
    LARGE = "1.7b"


@dataclass(frozen=True, slots=True)
class TTSCapabilities:
    """Planned adapter contract, not a statement that its runtime is installed.

    supports_streaming refers to incremental PCM from this adapter, not a model
    project's streaming claims or the application's existing sentence queue.
    Volume is owned by the existing audio player, not this synthesis contract.
    """

    supports_streaming: bool = False
    supports_speaker_selection: bool = True
    supports_language_selection: bool = True
    supports_speed: bool = False
    supports_temperature: bool = True
    supports_emotion: bool = False
    supports_reference_audio: bool = False
    supports_reference_text: bool = False
    supports_speaker_embedding: bool = False
    languages: tuple[str, ...] = ()
    speakers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name in ("languages", "speakers"):
                if not isinstance(value, tuple) or any(not isinstance(item, str) or not item.strip() for item in value):
                    raise ValueError(f"{name} 必须是非空名称组成的 tuple。")
            elif not isinstance(value, bool):
                raise ValueError(f"{name} 必须是布尔值。")


_LANGUAGES = (
    "Auto", "Chinese", "English", "Japanese", "Korean", "German", "French",
    "Russian", "Portuguese", "Spanish", "Italian",
)
_SPEAKERS = (
    "vivian", "serena", "uncle_fu", "dylan", "eric", "ryan", "aiden",
    "ono_anna", "sohee",
)


def capabilities_for_variant(variant: QwenCustomVoiceVariant | str) -> TTSCapabilities:
    variant = QwenCustomVoiceVariant(variant)
    return TTSCapabilities(
        supports_emotion=variant is QwenCustomVoiceVariant.LARGE,
        languages=_LANGUAGES, speakers=_SPEAKERS,
    )


def _strings(instance: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(instance, name)
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{name} 必须是不含 NUL 的字符串。")


def _finite_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是正有限数值。")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须是正有限数值。")


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """Future profile slots; unsupported slots must not be silently applied.

    This does not replace VoiceSettings and has no persistence or UI wiring.
    Reference/embedding slots are reserved; neither CustomVoice variant supports
    them. Numeric speed is also reserved until an exact-speed adapter exists.
    """

    name: str = "默认声音"
    backend: TTSBackendChoice = TTSBackendChoice.AUTO
    model_path: str = ""
    language: str = "Auto"
    speaker_id: str = "serena"
    speed: float = 1.0
    temperature: float = 0.9
    emotion: str = ""
    reference_audio_path: str = ""
    reference_text: str = ""
    speaker_embedding_path: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", TTSBackendChoice(self.backend))
        _strings(self, (
            "name", "model_path", "language", "speaker_id", "emotion",
            "reference_audio_path", "reference_text", "speaker_embedding_path",
        ))
        _finite_positive("speed", self.speed)
        _finite_positive("temperature", self.temperature)


@dataclass(frozen=True, slots=True)
class NeuralRuntimeConfig:
    """Explicit external environment; never imports models into desktop Python."""

    python_executable: str = ""
    model_path: str = ""
    device: str = "auto"
    model_variant: QwenCustomVoiceVariant = QwenCustomVoiceVariant.SMALL

    def __post_init__(self) -> None:
        _strings(self, ("python_executable", "model_path", "device"))
        object.__setattr__(self, "model_variant", QwenCustomVoiceVariant(self.model_variant))
        if self.device not in ("auto", "cpu", "cuda") and not re.fullmatch(r"cuda:[0-9]+", self.device):
            raise ValueError("device 必须是 auto、cpu、cuda 或 cuda:N。")


@dataclass(frozen=True, slots=True)
class NeuralSetupInspection:
    files_present: bool
    configuration_matches: bool
    issues: tuple[str, ...]
    weight_files: tuple[Path, ...] = ()
    dependencies_checked: bool = field(default=False, init=False)
    runtime_available: bool = field(default=False, init=False)
    weights_validated: bool = field(default=False, init=False)

    @property
    def local_checks_passed(self) -> bool:
        return self.files_present and self.configuration_matches and not self.issues


_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_INDEX_ENTRIES = 65536
_MAX_SHARDS = 1024


def _local_path(value: str, label: str, issues: list[str]) -> Path | None:
    try:
        if not value.strip():
            raise ValueError("尚未指定路径")
        # Reject UNC/device/URL paths before any filesystem lookup. Explicit
        # absolute paths also avoid a changing working directory choosing a model.
        if value.replace("\\", "/").startswith("//") or "://" in value:
            raise ValueError("只接受本地绝对路径，不接受网络或设备路径")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("需要本地绝对路径")
        return path
    except (OSError, ValueError, RuntimeError) as exc:
        issues.append(f"{label}: {exc}")
        return None


def _file(path: Path, issues: list[str], *, root: Path | None = None) -> bool:
    try:
        if root is not None and not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("文件解析后位于模型目录之外")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError("文件缺失、不是文件或为空")
        return True
    except (OSError, ValueError, RuntimeError) as exc:
        issues.append(f"{path}: {exc}")
        return False


def _json_object(path: Path, issues: list[str]) -> dict | None:
    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_JSON_BYTES + 1)
        if len(raw) > _MAX_JSON_BYTES:
            raise ValueError("JSON 文件超过 4 MiB 检查上限")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("JSON 顶层必须是对象")
        return result
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        issues.append(f"{path}: {exc}")
        return None


def _weight_files(directory: Path, issues: list[str]) -> list[Path]:
    single = directory / "model.safetensors"
    index = directory / "model.safetensors.index.json"
    try:
        indexed = index.exists()
    except (OSError, ValueError, RuntimeError) as exc:
        issues.append(f"{index}: {exc}")
        return []
    if not indexed:
        return [single] if _file(single, issues, root=directory) else []
    if not _file(index, issues, root=directory):
        return []
    data = _json_object(index, issues)
    mapping = data.get("weight_map") if data is not None else None
    if not isinstance(mapping, dict) or not 1 <= len(mapping) <= _MAX_INDEX_ENTRIES:
        issues.append(f"{index}: weight_map 必须是 1 到 {_MAX_INDEX_ENTRIES} 项的对象。")
        return []
    if any(not isinstance(key, str) or not key for key in mapping):
        issues.append(f"{index}: weight_map 参数名必须是非空字符串。")
        return []
    names: set[str] = set()
    for value in mapping.values():
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            issues.append(f"{index}: 分片路径必须是非空字符串。")
            return []
        posix, windows = PurePosixPath(value.replace("\\", "/")), PureWindowsPath(value)
        if (posix.is_absolute() or windows.drive or windows.root
                or ".." in posix.parts or ":" in value or posix.suffix != ".safetensors"):
            issues.append(f"{index}: 拒绝非相对、安全的 safetensors 分片路径 {value!r}。")
            return []
        names.add(posix.as_posix())
    if len(names) > _MAX_SHARDS:
        issues.append(f"{index}: 分片数量超过 {_MAX_SHARDS}。")
        return []
    return [directory / name for name in sorted(names)
            if _file(directory / name, issues, root=directory)]


def inspect_neural_setup(config: NeuralRuntimeConfig) -> NeuralSetupInspection:
    """Check small metadata and nonempty files only; do not execute Python.

    File completeness does not validate safetensors contents, dependencies,
    hardware compatibility, model quality, or whether inference can succeed.
    """
    if not isinstance(config, NeuralRuntimeConfig):
        raise TypeError("config 必须是 NeuralRuntimeConfig。")
    files: list[str] = []
    metadata: list[str] = []
    weights: list[Path] = []
    executable = _local_path(config.python_executable, "独立 Python 解释器", files)
    if executable is not None:
        _file(executable, files)
    root = _local_path(config.model_path, "语音模型目录", files)
    try:
        if root is None or not root.is_dir():
            files.append("尚未指定存在的模型目录。")
            return NeuralSetupInspection(False, False, tuple(files))
    except (OSError, ValueError, RuntimeError) as exc:
        files.append(f"模型目录无法检查: {exc}")
        return NeuralSetupInspection(False, False, tuple(files))
    path = root / "config.json"
    data = _json_object(path, metadata) if _file(path, files, root=root) else None
    expected = {
        "model_type": "qwen3_tts", "tokenizer_type": "qwen3_tts_tokenizer_12hz",
        "tts_model_type": "custom_voice",
        "tts_model_size": "0b6" if config.model_variant is QwenCustomVoiceVariant.SMALL else "1b7",
    }
    if data is not None:
        for key, value in expected.items():
            if data.get(key) != value:
                metadata.append(f"{path}: {key} 必须为 {value!r}。")
    weights.extend(_weight_files(root, files))
    # The official CustomVoice snapshot includes the text tokenizer and both
    # processors. A weights-only directory cannot pass the local file check.
    for name in (
        "generation_config.json", "preprocessor_config.json", "tokenizer_config.json",
        "vocab.json", "speech_tokenizer/preprocessor_config.json",
    ):
        resource = root / name
        if _file(resource, files, root=root):
            _json_object(resource, metadata)
    _file(root / "merges.txt", files, root=root)
    tokenizer = root / "speech_tokenizer"
    tokenizer_config = tokenizer / "config.json"
    if _file(tokenizer_config, files, root=root):
        codec = _json_object(tokenizer_config, metadata)
        if codec is not None and codec.get("model_type") != "qwen3_tts_tokenizer_12hz":
            metadata.append(f"{tokenizer_config}: model_type 必须为 'qwen3_tts_tokenizer_12hz'。")
    weights.extend(_weight_files(tokenizer, files))
    return NeuralSetupInspection(
        files_present=not files, configuration_matches=data is not None and not metadata,
        issues=tuple(files + metadata), weight_files=tuple(weights),
    )
