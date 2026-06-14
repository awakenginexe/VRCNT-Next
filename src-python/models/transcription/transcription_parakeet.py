"""NVIDIA Parakeet speech-to-text backend.

NVIDIA publishes Parakeet TDT v3 as NeMo/safetensors, while this app needs a
runtime that can load quickly inside the bundled Python sidecar. The runnable
entry below uses the ONNX conversion supported by onnx-asr; older friend-added
entries stay visible but unavailable until a matching runtime exists.
"""

from os import path as os_path, makedirs as os_makedirs
from json import dump as json_dump
from typing import Callable, Optional, Dict, Any, List, Tuple
import logging

import numpy as np

try:
    import onnx_asr  # type: ignore
    _ONNX_ASR_AVAILABLE = True
except Exception:
    onnx_asr = None  # type: ignore
    _ONNX_ASR_AVAILABLE = False

try:
    import huggingface_hub  # type: ignore
    _HF_AVAILABLE = True
except Exception:
    huggingface_hub = None  # type: ignore
    _HF_AVAILABLE = False


logger = logging.getLogger("parakeet")
logger.setLevel(logging.CRITICAL)


# capacity_mb is download size; vram_mb is approximate VRAM at fp16.
_UNAVAILABLE_REASON = "This entry is published as .nemo/safetensors and is not wired to this ONNX backend."
_PARAKEET_TDT_V3_LANGUAGES = [
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
    "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
]

_MODELS: Dict[str, Dict[str, Any]] = {
    "parakeet-tdt-0.6b-v3": {
        "repo": "istupakov/parakeet-tdt-0.6b-v3-onnx",
        "onnx_asr_model": "nemo-parakeet-tdt-0.6b-v3",
        "files": [
            "config.json",
            "encoder-model.onnx",
            "encoder-model.onnx.data",
            "decoder_joint-model.onnx",
            "nemo128.onnx",
            "vocab.txt",
        ],
        "capacity_mb": 1200,
        "vram_mb": 3072,
        "languages": _PARAKEET_TDT_V3_LANGUAGES,
        "downloadable": True,
        "unavailable_reason": "",
    },
    "parakeet-tdt-0.6b": {
        "repo": "nvidia/parakeet-tdt-0.6b-v2",
        "files": ["parakeet-tdt-0.6b-v2.nemo"],
        "capacity_mb": 620,
        "vram_mb": 2048,
        "languages": ["en"],
        "downloadable": False,
        "unavailable_reason": _UNAVAILABLE_REASON,
    },
    "parakeet-tdt-ctc-0.6b": {
        "repo": "nvidia/parakeet-tdt_ctc-0.6b-ja",
        "files": ["parakeet-tdt_ctc-0.6b-ja.nemo"],
        "capacity_mb": 620,
        "vram_mb": 2048,
        "languages": ["ja"],
        "downloadable": False,
        "unavailable_reason": _UNAVAILABLE_REASON,
    },
    "parakeet-tdt-1.1b": {
        "repo": "nvidia/parakeet-tdt-1.1b",
        "files": ["parakeet-tdt-1.1b.nemo"],
        "capacity_mb": 1100,
        "vram_mb": 3072,
        "languages": ["en"],
        "downloadable": False,
        "unavailable_reason": _UNAVAILABLE_REASON,
    },
    "canary-1b": {
        "repo": "nvidia/canary-1b",
        "files": ["canary-1b.nemo"],
        "capacity_mb": 1100,
        "vram_mb": 3072,
        "languages": ["en", "de", "es", "fr"],
        "downloadable": False,
        "unavailable_reason": _UNAVAILABLE_REASON,
    },
}


# Language → known Parakeet language code. This is the union of the languages
# documented for NVIDIA Parakeet RNNT 1.1B multilingual and Parakeet TDT 0.6B v3.
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "Arabic": "ar",
    "Bulgarian": "bg",
    "Croatian": "hr",
    "Czech": "cs",
    "Danish": "da",
    "Dutch": "nl",
    "English": "en",
    "Estonian": "et",
    "Finnish": "fi",
    "French": "fr",
    "German": "de",
    "Greek": "el",
    "Hebrew": "he",
    "Hindi": "hi",
    "Hungarian": "hu",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Latvian": "lv",
    "Lithuanian": "lt",
    "Maltese": "mt",
    "Norwegian": "nb",
    "Polish": "pl",
    "Portuguese": "pt",
    "Romanian": "ro",
    "Russian": "ru",
    "Slovak": "sk",
    "Slovenian": "sl",
    "Spanish": "es",
    "Swedish": "sv",
    "Thai": "th",
    "Turkish": "tr",
    "Ukrainian": "uk",
}


def _modelDir(root: str, weight_type: str) -> str:
    return os_path.join(root, "weights", "parakeet", weight_type)


def _markerPath(root: str, weight_type: str) -> str:
    return os_path.join(_modelDir(root, weight_type), "downloaded.json")


def listParakeetModelKeys() -> list:
    return list(_MODELS.keys())


def getParakeetModelMeta(weight_type: str) -> Dict[str, Any]:
    return _MODELS.get(weight_type, {})


def getParakeetSupportedLanguageCodes(weight_type: str) -> List[str]:
    return list(_MODELS.get(weight_type, {}).get("languages", []))


def checkParakeetWeight(root: str, weight_type: str) -> bool:
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


def downloadParakeetWeight(
    root: str,
    weight_type: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> None:
    meta = _MODELS.get(weight_type)
    if meta is None or meta.get("downloadable") is not True or not _ONNX_ASR_AVAILABLE or not _HF_AVAILABLE:
        if callable(end_callback):
            end_callback()
        return

    path = _modelDir(root, weight_type)
    os_makedirs(path, exist_ok=True)

    if checkParakeetWeight(root, weight_type):
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
            json_dump({"repo": meta["repo"], "backend": "onnx-asr", "model": meta["onnx_asr_model"]}, f)
    except Exception:
        logger.exception("Failed to download Parakeet model: %s", weight_type)
    finally:
        if callable(end_callback):
            end_callback()


class ParakeetRecognizer:
    """Thin wrapper over onnx-asr for a Parakeet model."""

    def __init__(self, model_dir: str, device: str = "cuda", device_index: int = 0) -> None:
        if not _ONNX_ASR_AVAILABLE:
            raise RuntimeError("onnx-asr is not installed")
        providers: List = []
        if device == "cuda":
            providers.append(("CUDAExecutionProvider", {"device_id": device_index}))
        providers.append("CPUExecutionProvider")
        self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", path=model_dir, providers=providers)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Run inference over a 1-D float32 PCM array."""
        try:
            waveform = audio.astype(np.float32, copy=False).flatten()
            return str(self.model.recognize(waveform, sample_rate=sample_rate)).strip()
        except Exception:
            return ""


def getParakeetModel(
    root: str,
    weight_type: str,
    device: str = "cuda",
    device_index: int = 0,
) -> ParakeetRecognizer:
    path = _modelDir(root, weight_type)
    if not checkParakeetWeight(root, weight_type):
        raise FileNotFoundError(f"parakeet model not downloaded: {weight_type}")
    try:
        return ParakeetRecognizer(path, device=device, device_index=device_index)
    except RuntimeError as e:
        msg = str(e)
        if "CUDA" in msg or "out of memory" in msg.lower():
            raise ValueError("VRAM_OUT_OF_MEMORY", msg)
        raise
