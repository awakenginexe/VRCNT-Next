"""SenseVoice-Small speech-to-text backend.

Uses sherpa-onnx for CPU inference. SenseVoice-Small supports zh, yue, en, ja,
ko and is available in FP32 (~938 MB) and INT8 (~228 MB) ONNX formats.
"""

import importlib.util
import os
import sys
from os import path as os_path, makedirs as os_makedirs
from json import dump as json_dump
import json
from typing import Callable, Optional, Dict, Any, List
import logging

import numpy as np
from utils import errorLogging, printLog

try:
    import sherpa_onnx  # type: ignore
    _SHERPA_AVAILABLE = True
except Exception:
    sherpa_onnx = None  # type: ignore
    _SHERPA_AVAILABLE = False

try:
    import huggingface_hub  # type: ignore
    _HF_AVAILABLE = True
except Exception:
    huggingface_hub = None  # type: ignore
    _HF_AVAILABLE = False


logger = logging.getLogger("sensevoice")
logger.setLevel(logging.CRITICAL)

_DLL_DIR_HANDLES = []


def _addDllDirectory(directory: str) -> None:
    if directory and os_path.isdir(directory) and hasattr(os, "add_dll_directory"):
        try:
            _DLL_DIR_HANDLES.append(os.add_dll_directory(directory))
        except Exception:
            pass


def _addCudaDllDirectories() -> None:
    """Expose CUDA DLLs bundled with PyTorch/CTranslate2 to sherpa-onnx."""
    candidates = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.extend([
            os_path.join(frozen_root, "torch", "lib"),
            os_path.join(frozen_root, "ctranslate2"),
        ])

    for package_name, relative_dir in (("torch", "lib"), ("ctranslate2", "")):
        try:
            spec = importlib.util.find_spec(package_name)
            if spec is None or spec.origin is None:
                continue
            package_dir = os_path.dirname(spec.origin)
            candidates.append(os_path.join(package_dir, relative_dir) if relative_dir else package_dir)
        except Exception:
            pass

    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    for directory in candidates:
        if os_path.isdir(directory):
            _addDllDirectory(directory)
            if directory not in path_parts:
                path_parts.insert(0, directory)
    os.environ["PATH"] = os.pathsep.join(path_parts)


_SENSEVOICE_LANGUAGES = ["zh", "yue", "en", "ja", "ko"]

_MODELS: Dict[str, Dict[str, Any]] = {
    "sensevoice-small-int8": {
        "repo": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "onnx_file": "model.int8.onnx",
        "files": [
            "model.int8.onnx",
            "tokens.txt",
        ],
        "capacity_mb": 230,
        "vram_mb": 0,
        "languages": _SENSEVOICE_LANGUAGES,
        "downloadable": True,
        "unavailable_reason": "",
    },
    "sensevoice-small-fp32": {
        "repo": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        "onnx_file": "model.onnx",
        "files": [
            "model.onnx",
            "tokens.txt",
        ],
        "capacity_mb": 938,
        "vram_mb": 0,
        "languages": _SENSEVOICE_LANGUAGES,
        "downloadable": True,
        "unavailable_reason": "",
    },
}


# Language name → SenseVoice language code
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "Chinese Simplified": "zh",
    "Chinese Traditional": "yue",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
}

# Mapping from VRCT language code to SenseVoice language tag
_LANG_CODE_TO_SENSEVOICE: Dict[str, str] = {
    "zh": "zh",
    "yue": "yue",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
}


def _modelDir(root: str, weight_type: str) -> str:
    return os_path.join(root, "weights", "sensevoice", weight_type)


def _markerPath(root: str, weight_type: str) -> str:
    return os_path.join(_modelDir(root, weight_type), "downloaded.json")


def listSenseVoiceModelKeys() -> list:
    return list(_MODELS.keys())


def getSenseVoiceModelMeta(weight_type: str) -> Dict[str, Any]:
    return _MODELS.get(weight_type, {})


def getSenseVoiceSupportedLanguageCodes(weight_type: str) -> List[str]:
    return list(_MODELS.get(weight_type, {}).get("languages", []))


def checkSenseVoiceWeight(root: str, weight_type: str) -> bool:
    meta = _MODELS.get(weight_type)
    if meta is None or meta.get("downloadable") is not True:
        return False
    path = _modelDir(root, weight_type)
    if not os_path.isdir(path):
        return False
    if not os_path.isfile(_markerPath(root, weight_type)):
        return False
    for fname in meta.get("files", []):
        if not os_path.isfile(os_path.join(path, fname)):
            return False
    return True


def downloadSenseVoiceWeight(
    root: str,
    weight_type: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> None:
    meta = _MODELS.get(weight_type)
    if meta is None or meta.get("downloadable") is not True or not _HF_AVAILABLE:
        if callable(end_callback):
            end_callback()
        return

    path = _modelDir(root, weight_type)
    os_makedirs(path, exist_ok=True)

    if checkSenseVoiceWeight(root, weight_type):
        if callable(end_callback):
            end_callback()
        return

    try:
        if callable(callback):
            callback(0.05)
        huggingface_hub.snapshot_download(
            repo_id=meta["repo"],
            local_dir=path,
            allow_patterns=meta["files"],
            local_dir_use_symlinks=False,
        )
        if callable(callback):
            callback(1.0)
        with open(_markerPath(root, weight_type), "w", encoding="utf-8") as f:
            json_dump({"repo": meta["repo"], "backend": "sherpa-onnx", "onnx_file": meta["onnx_file"]}, f)
    except Exception:
        logger.exception("Failed to download SenseVoice model: %s", weight_type)
    finally:
        if callable(end_callback):
            end_callback()


class SenseVoiceRecognizer:
    """Thin wrapper over sherpa-onnx for a SenseVoice model."""

    def __init__(self, model_dir: str, weight_type: str, device: str = "cuda", device_index: int = 0) -> None:
        if not _SHERPA_AVAILABLE:
            raise RuntimeError("sherpa-onnx is not installed")

        meta = _MODELS.get(weight_type, {})
        onnx_file = meta.get("onnx_file", "model.int8.onnx")
        model_path = os_path.join(model_dir, onnx_file)
        tokens_path = os_path.join(model_dir, "tokens.txt")

        provider = "cuda" if device == "cuda" else "cpu"
        if provider == "cuda":
            _addCudaDllDirectories()

        self.model_path = model_path
        self.tokens_path = tokens_path
        self.provider = provider
        self.recognizers = {}
        self.cpu_recognizers = {}
        self.recognizer = self._getRecognizer(provider, "auto")

    def _createRecognizer(self, provider: str, language: str = "auto"):
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=self.model_path,
            tokens=self.tokens_path,
            use_itn=True,
            provider=provider,
            num_threads=4,
            language=language or "auto",
        )

    def _getRecognizer(self, provider: str, language: str = "auto"):
        language = language or "auto"
        recognizers = self.cpu_recognizers if provider == "cpu" else self.recognizers
        key = (provider, language)
        if key not in recognizers:
            recognizers[key] = self._createRecognizer(provider, language)
        return recognizers[key]

    def _normalizeLanguageTag(self, language: str) -> str:
        language = (language or "").strip()
        if language.startswith("<|") and language.endswith("|>"):
            return language[2:-2]
        return language

    def _recognize(self, recognizer, waveform: np.ndarray, sample_rate: int, requested_language: str) -> Dict[str, str]:
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, waveform)
        if hasattr(stream, "input_finished"):
            stream.input_finished()
        recognizer.decode_stream(stream)
        result = stream.result
        text = getattr(result, "text", "").strip()
        detected_language = requested_language if requested_language != "auto" else ""

        as_json_string = getattr(result, "as_json_string", None)
        if callable(as_json_string):
            try:
                payload = json.loads(as_json_string())
                detected_language = self._normalizeLanguageTag(payload.get("lang", detected_language))
            except Exception:
                pass

        detected_language = self._normalizeLanguageTag(
            getattr(result, "lang", detected_language)
        )
        return {"text": text, "language": detected_language}

    def _shouldFallbackToCpu(self, waveform: np.ndarray, result: Dict[str, str]) -> bool:
        if self.provider != "cuda" or result.get("text"):
            return False
        if waveform.size < 1600:
            return False
        try:
            return float(np.max(np.abs(waveform))) > 0.00001
        except Exception:
            return False

    def recognize(self, audio: np.ndarray, sample_rate: int = 16000, language: str = "auto") -> Dict[str, str]:
        """Run inference over a 1-D float32 PCM array."""
        waveform = audio.astype(np.float32, copy=False).flatten()
        language = language or "auto"
        try:
            recognizer = self._getRecognizer(self.provider, language)
            result = self._recognize(recognizer, waveform, sample_rate, language)
        except Exception:
            errorLogging()
            result = {"text": "", "language": language if language != "auto" else ""}

        if self._shouldFallbackToCpu(waveform, result):
            try:
                cpu_recognizer = self._getRecognizer("cpu", language)
                cpu_result = self._recognize(cpu_recognizer, waveform, sample_rate, language)
                if cpu_result.get("text"):
                    printLog(
                        "SenseVoice CUDA returned empty text; used CPU fallback",
                        {"sample_rate": sample_rate, "samples": int(waveform.size)},
                    )
                return cpu_result
            except Exception:
                errorLogging()

        return result

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str = "auto") -> str:
        """Run inference and return only text for older callers."""
        return self.recognize(audio, sample_rate=sample_rate, language=language).get("text", "")


def getSenseVoiceModel(
    root: str,
    weight_type: str,
    device: str = "cuda",
    device_index: int = 0,
) -> SenseVoiceRecognizer:
    path = _modelDir(root, weight_type)
    if not checkSenseVoiceWeight(root, weight_type):
        raise FileNotFoundError(f"SenseVoice model not downloaded: {weight_type}")
    try:
        return SenseVoiceRecognizer(path, weight_type, device=device, device_index=device_index)
    except RuntimeError as e:
        msg = str(e)
        if "CUDA" in msg or "out of memory" in msg.lower():
            raise ValueError("VRAM_OUT_OF_MEMORY", msg)
        raise
