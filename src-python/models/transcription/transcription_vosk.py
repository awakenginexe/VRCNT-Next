"""Vosk speech-to-text backend.

Vosk models are offline, CPU-friendly, and per-language. Each configured
weight type below maps to one downloadable model from Alpha Cephei's model
list.
"""

from os import path as os_path, makedirs as os_makedirs, listdir as os_listdir
from typing import Callable, Optional, Dict, Any, List
import json
import zipfile
import io
from requests import get as requests_get

try:
    import vosk  # type: ignore
    _VOSK_AVAILABLE = True
except Exception:
    vosk = None  # type: ignore
    _VOSK_AVAILABLE = False


_BASE_URL = "https://alphacephei.com/vosk/models"


def _model(model_name: str, language: str, capacity_mb: int, ram_mb: int = 300) -> Dict[str, Any]:
    return {
        "url": f"{_BASE_URL}/{model_name}.zip",
        "model_name": model_name,
        "language": language,
        "languages": [language],
        "capacity_mb": capacity_mb,
        "ram_mb": ram_mb,
    }


_MODELS: Dict[str, Dict[str, Any]] = {
    # Existing IDs kept for compatibility with saved configs.
    "small-en": _model("vosk-model-small-en-us-0.15", "en", 40),
    "large-en": _model("vosk-model-en-us-0.22", "en", 1800, 16000),
    "small-ja": _model("vosk-model-small-ja-0.22", "ja", 48),
    "small-zh": _model("vosk-model-small-cn-0.22", "zh", 42),
    "small-ko": _model("vosk-model-small-ko-0.22", "ko", 82),
    "small-fr": _model("vosk-model-small-fr-0.22", "fr", 41),

    # Additional Vosk languages from the current official model list.
    "small-en-in": _model("vosk-model-small-en-in-0.4", "en", 36),
    "small-de": _model("vosk-model-small-de-0.15", "de", 45),
    "small-es": _model("vosk-model-small-es-0.42", "es", 39),
    "small-pt": _model("vosk-model-small-pt-0.3", "pt", 31),
    "small-ru": _model("vosk-model-small-ru-0.22", "ru", 45),
    "small-tr": _model("vosk-model-small-tr-0.3", "tr", 35),
    "small-vn": _model("vosk-model-small-vn-0.4", "vi", 32),
    "small-it": _model("vosk-model-small-it-0.22", "it", 48),
    "small-nl": _model("vosk-model-small-nl-0.22", "nl", 39),
    "small-ca": _model("vosk-model-small-ca-0.4", "ca", 42),
    "ar-mgb2": _model("vosk-model-ar-mgb2-0.4", "ar", 318, 800),
    "el-gr": _model("vosk-model-el-gr-0.7", "el", 1100, 2000),
    "small-fa": _model("vosk-model-small-fa-0.42", "fa", 53),
    "tl-ph-generic": _model("vosk-model-tl-ph-generic-0.6", "tl", 320, 800),
    "small-uk": _model("vosk-model-small-uk-v3-small", "uk", 133, 500),
    "small-kz": _model("vosk-model-small-kz-0.42", "kk", 58),
    "small-sv": _model("vosk-model-small-sv-rhasspy-0.15", "sv", 289, 700),
    "small-eo": _model("vosk-model-small-eo-0.42", "eo", 42),
    "small-hi": _model("vosk-model-small-hi-0.22", "hi", 42),
    "small-cs": _model("vosk-model-small-cs-0.4-rhasspy", "cs", 44),
    "small-pl": _model("vosk-model-small-pl-0.22", "pl", 50),
    "small-uz": _model("vosk-model-small-uz-0.22", "uz", 49),
    "br": _model("vosk-model-br-0.8", "br", 70),
    "small-gu": _model("vosk-model-small-gu-0.42", "gu", 100),
    "small-tg": _model("vosk-model-small-tg-0.22", "tg", 50),
    "small-te": _model("vosk-model-small-te-0.42", "te", 58),
    "small-ky": _model("vosk-model-small-ky-0.42", "ky", 49),
    "small-ka": _model("vosk-model-small-ka-0.42", "ka", 45),
}


SUPPORTED_LANGUAGES: Dict[str, str] = {
    "Arabic": "ar",
    "Breton": "br",
    "Catalan": "ca",
    "Chinese Simplified": "zh",
    "Chinese Traditional": "zh",
    "Czech": "cs",
    "Dutch": "nl",
    "English": "en",
    "Esperanto": "eo",
    "Filipino": "tl",
    "French": "fr",
    "Georgian": "ka",
    "German": "de",
    "Greek": "el",
    "Gujarati": "gu",
    "Hindi": "hi",
    "Italian": "it",
    "Japanese": "ja",
    "Kazakh": "kk",
    "Korean": "ko",
    "Kyrgyz": "ky",
    "Persian": "fa",
    "Polish": "pl",
    "Portuguese": "pt",
    "Russian": "ru",
    "Swedish": "sv",
    "Tajik": "tg",
    "Telugu": "te",
    "Turkish": "tr",
    "Ukrainian": "uk",
    "Uzbek": "uz",
    "Vietnamese": "vi",
}


def _modelDir(root: str, weight_type: str) -> str:
    return os_path.join(root, "weights", "vosk", weight_type)


def listVoskModelKeys() -> list:
    return list(_MODELS.keys())


def getVoskModelMeta(weight_type: str) -> Dict[str, Any]:
    return _MODELS.get(weight_type, {})


def getVoskSupportedLanguageCodes(weight_type: str) -> List[str]:
    meta = _MODELS.get(weight_type, {})
    return list(meta.get("languages") or ([meta["language"]] if meta.get("language") else []))


def _looksLikeVoskModel(model_dir: str) -> bool:
    return (
        os_path.isdir(model_dir)
        and os_path.isfile(os_path.join(model_dir, "conf", "model.conf"))
        and os_path.isfile(os_path.join(model_dir, "am", "final.mdl"))
    )


def checkVoskWeight(root: str, weight_type: str) -> bool:
    if weight_type not in _MODELS:
        return False
    return _looksLikeVoskModel(_modelDir(root, weight_type))


def _safeExtractZip(zip_bytes: bytes, target_dir: str) -> None:
    target_abs = os_path.abspath(target_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [name for name in zf.namelist() if name and not name.endswith("/")]
        root_parts = {name.split("/", 1)[0] for name in names if "/" in name}
        strip_root = len(root_parts) == 1

        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if strip_root and "/" in name:
                name = name.split("/", 1)[1]
            if not name:
                continue

            dest = os_path.abspath(os_path.join(target_dir, *name.split("/")))
            if os_path.commonpath([target_abs, dest]) != target_abs:
                continue
            os_makedirs(os_path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())


def downloadVoskWeight(
    root: str,
    weight_type: str,
    callback: Optional[Callable[[float], None]] = None,
    end_callback: Optional[Callable[[], None]] = None,
) -> None:
    meta = _MODELS.get(weight_type)
    if meta is None:
        if callable(end_callback):
            end_callback()
        return

    model_dir = _modelDir(root, weight_type)
    os_makedirs(model_dir, exist_ok=True)

    if checkVoskWeight(root, weight_type):
        if callable(end_callback):
            end_callback()
        return

    try:
        response = requests_get(meta["url"], stream=True, timeout=60)
        response.raise_for_status()
        file_size = int(response.headers.get("content-length", 0))
        buf = io.BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            buf.write(chunk)
            total += len(chunk)
            if callable(callback) and file_size:
                callback(total / file_size)
        _safeExtractZip(buf.getvalue(), model_dir)
        if callable(callback):
            callback(1.0)
    except Exception:
        pass
    finally:
        if callable(end_callback):
            end_callback()


class VoskRecognizer:
    def __init__(self, model_dir: str, sample_rate: int = 16000) -> None:
        if not _VOSK_AVAILABLE:
            raise RuntimeError("vosk is not installed")
        self.model = vosk.Model(model_dir)
        self.sample_rate = sample_rate

    def transcribe(self, pcm16: bytes, sample_rate: int = 16000) -> str:
        recognizer = vosk.KaldiRecognizer(self.model, sample_rate or self.sample_rate)
        recognizer.SetWords(False)
        recognizer.AcceptWaveform(pcm16)
        result = json.loads(recognizer.FinalResult())
        return str(result.get("text", "")).strip()


def getVoskRecognizer(root: str, weight_type: str, sample_rate: int = 16000) -> VoskRecognizer:
    if weight_type not in _MODELS:
        raise FileNotFoundError(f"unknown vosk model: {weight_type}")

    model_dir = _modelDir(root, weight_type)
    if not checkVoskWeight(root, weight_type):
        raise FileNotFoundError(f"vosk model not downloaded: {weight_type}")
    return VoskRecognizer(model_dir, sample_rate=sample_rate)


if __name__ == "__main__":
    def cb(p):
        print(f"progress: {p * 100:.1f}%")

    def end():
        print("done")

    downloadVoskWeight("./", "small-en", cb, end)
