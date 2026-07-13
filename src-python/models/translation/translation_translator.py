from os import path as os_path
import importlib
import sys
from threading import Lock
from time import perf_counter

from deepl import DeepLClient
import requests

try:
    from .translation_languages import translation_lang
    from .translation_utils import ctranslate2_weights, _prepareCtrTranslate2Runtime, loadCTranslate2Tokenizer
except Exception:
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from translation_languages import translation_lang
    from translation_utils import ctranslate2_weights, _prepareCtrTranslate2Runtime, loadCTranslate2Tokenizer

from utils import errorLogging, getBestComputeType
from models.pipeline.pipeline_types import TranslationAttempt, TranslationStatus

import warnings
from typing import Any, Optional, Tuple

warnings.filterwarnings("ignore")


PROVIDER_TIMEOUT_EXCEPTIONS = (TimeoutError, requests.exceptions.Timeout)
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 5.0


def _getCtrTranslate2():
    _prepareCtrTranslate2Runtime()
    return importlib.import_module("ctranslate2")


def _getTransformers():
    return importlib.import_module("transformers")


def _getWebTranslator():
    try:
        return importlib.import_module("translators").translate_text
    except Exception:
        errorLogging()
        return None


def _getRelativeClientModule(module_name: str):
    try:
        return importlib.import_module(f".{module_name}", __package__)
    except Exception:
        root_dir = os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__))))
        if root_dir not in sys.path:
            sys.path.append(root_dir)
        return importlib.import_module(module_name)


class Translator:
    """High-level translator facade.

    This class wraps multiple backends (DeepL, DeepL API, Google, Bing, Papago,
    and CTranslate2 local models). Optional dependencies may be unavailable at
    runtime; methods degrade gracefully and return False or an empty string on
    failure (kept compatible with existing behavior).
    """

    def __init__(self) -> None:
        self.deepl_client: Optional[DeepLClient] = None
        self.plamo_client: Any = None
        self.gemini_client: Any = None
        self.openai_client: Any = None
        self.groq_client: Any = None
        self.openrouter_client: Any = None
        self.lmstudio_client: Any = None
        self.lmstudio_connected: bool = False
        self.ollama_client: Any = None
        self.ollama_connected: bool = False
        self.ctranslate2_translator: Any = None
        self.ctranslate2_tokenizer: Any = None
        self.is_loaded_ctranslate2_model: bool = False
        self.is_changed_translator_parameters: bool = False
        self._web_translator = None
        self.is_enable_translators: bool = True
        self._context_provider_locks = {
            provider: Lock()
            for provider in (
                "Plamo_API",
                "Gemini_API",
                "OpenAI_API",
                "Groq_API",
                "OpenRouter_API",
                "LMStudio",
                "Ollama",
            )
        }

    def authenticationDeepLAuthKey(self, auth_key: str) -> bool:
        """Authenticate DeepL API with the provided key.

        Returns True on success, False on failure.
        """
        result = True
        try:
            self.deepl_client = DeepLClient(auth_key)
            # quick smoke test
            self.deepl_client.translate_text(" ", target_lang="EN-US")
        except Exception:
            errorLogging()
            self.deepl_client = None
            result = False
        return result

    def authenticationPlamoAuthKey(self, auth_key: str, root_path: str = None) -> bool:
        """Authenticate Plamo API with the provided key.

        Returns True on success, False on failure.
        """
        self.plamo_client = _getRelativeClientModule("translation_plamo").PlamoClient(root_path=root_path)
        if self.plamo_client.setAuthKey(auth_key):
            return True
        else:
            self.plamo_client = None
            return False

    def getPlamoModelList(self) -> list[str]:
        """Get available Plamo models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.plamo_client is None:
            return []
        return self.plamo_client.getModelList()

    def setPlamoModel(self, model: str) -> bool:
        """Change the Plamo model used for translation.

        Returns True on success, False on failure.
        """
        if self.plamo_client is None:
            return False
        return self.plamo_client.setModel(model)

    def updatePlamoClient(self) -> None:
        """Update the Plamo client (fetch available models)."""
        self.plamo_client.updateClient()

    def authenticationGeminiAuthKey(self, auth_key: str, root_path: str = None) -> bool:
        """Authenticate Gemini API with the provided key.

        Returns True on success, False on failure.
        """
        self.gemini_client = _getRelativeClientModule("translation_gemini").GeminiClient(root_path=root_path)
        if self.gemini_client.setAuthKey(auth_key):
            return True
        else:
            self.gemini_client = None
            return False

    def getGeminiModelList(self) -> list[str]:
        """Get available Gemini models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.gemini_client is None:
            return []
        return self.gemini_client.getModelList()

    def setGeminiModel(self, model: str) -> bool:
        """Change the Gemini model used for translation.

        Returns True on success, False on failure.
        """
        if self.gemini_client is None:
            return False
        return self.gemini_client.setModel(model)

    def updateGeminiClient(self) -> None:
        """Update the Gemini client (fetch available models)."""
        self.gemini_client.updateClient()

    def authenticationOpenAIAuthKey(self, auth_key: str, base_url: str | None = None, root_path: str = None) -> bool:
        """Authenticate OpenAI (Chat Completions) API with the provided key.

        base_url を指定することで互換エンドポイント (例: Azure OpenAI 互換, Proxy) にも対応可能。
        Returns True on success, False on failure.
        """
        self.openai_client = _getRelativeClientModule("translation_openai").OpenAIClient(base_url=base_url, root_path=root_path)
        if self.openai_client.setAuthKey(auth_key):
            return True
        else:
            self.openai_client = None
            return False

    def getOpenAIModelList(self) -> list[str]:
        """Get available OpenAI models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.openai_client is None:
            return []
        return self.openai_client.getModelList()

    def setOpenAIModel(self, model: str) -> bool:
        """Change the OpenAI model used for translation.

        Returns True on success, False on failure.
        """
        if self.openai_client is None:
            return False
        return self.openai_client.setModel(model)

    def updateOpenAIClient(self) -> None:
        """Update the OpenAI client (fetch available models)."""
        self.openai_client.updateClient()

    def authenticationGroqAuthKey(self, auth_key: str, root_path: str = None) -> bool:
        """Authenticate Groq API with the provided key.

        Returns True on success, False on failure.
        """
        self.groq_client = _getRelativeClientModule("translation_groq").GroqClient(root_path=root_path)
        if self.groq_client.setAuthKey(auth_key):
            return True
        else:
            self.groq_client = None
            return False

    def getGroqModelList(self) -> list[str]:
        """Get available Groq models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.groq_client is None:
            return []
        return self.groq_client.getModelList()

    def setGroqModel(self, model: str) -> bool:
        """Change the Groq model used for translation.

        Returns True on success, False on failure.
        """
        if self.groq_client is None:
            return False
        return self.groq_client.setModel(model)

    def updateGroqClient(self) -> None:
        """Update the Groq client (fetch available models)."""
        self.groq_client.updateClient()

    def authenticationOpenRouterAuthKey(self, auth_key: str, root_path: str = None) -> bool:
        """Authenticate OpenRouter API with the provided key.

        Returns True on success, False on failure.
        """
        self.openrouter_client = _getRelativeClientModule("translation_openrouter").OpenRouterClient(root_path=root_path)
        if self.openrouter_client.setAuthKey(auth_key):
            return True
        else:
            self.openrouter_client = None
            return False

    def getOpenRouterModelList(self) -> list[str]:
        """Get available OpenRouter models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.openrouter_client is None:
            return []
        return self.openrouter_client.getModelList()

    def setOpenRouterModel(self, model: str) -> bool:
        """Change the OpenRouter model used for translation.

        Returns True on success, False on failure.
        """
        if self.openrouter_client is None:
            return False
        return self.openrouter_client.setModel(model)

    def updateOpenRouterClient(self) -> None:
        """Update the OpenRouter client (fetch available models)."""
        self.openrouter_client.updateClient()

    def getLMStudioConnected(self) -> bool:
        """Get LM Studio connection status.

        Returns True if connected and verified, False otherwise.
        """
        return self.lmstudio_connected

    def setLMStudioClientURL(self, base_url: str | None = None, root_path: str = None) -> bool:
        """Authenticate LM Studio with the provided base URL.

        Returns True on success, False on failure.
        """
        self.lmstudio_client = _getRelativeClientModule("translation_lmstudio").LMStudioClient(base_url=base_url, root_path=root_path)
        result = self.lmstudio_client.setBaseURL(base_url)
        if result is False:
            self.lmstudio_client = None
            self.lmstudio_connected = False
        else:
            self.lmstudio_connected = True
        return result

    def getLMStudioModelList(self) -> list[str]:
        """Get available LM Studio models.

        Returns a list of model names, or an empty list on failure.
        """
        if self.lmstudio_client is None:
            return []
        return self.lmstudio_client.getModelList()

    def setLMStudioModel(self, model: str) -> bool:
        """Change the LM Studio model used for translation.
        """
        if self.lmstudio_client is None:
            return False
        return self.lmstudio_client.setModel(model)

    def updateLMStudioClient(self) -> None:
        """Update the LM Studio client (fetch available models)."""
        self.lmstudio_client.updateClient()

    def getOllamaConnected(self) -> bool:
        """Get Ollama connection status.

        Returns True if connected and verified, False otherwise.
        """
        return self.ollama_connected

    def checkOllamaClient(self, root_path: str = None) -> bool:
        """Check if Ollama client is available.

        Returns True if Ollama is reachable, False otherwise.
        """
        self.ollama_client = _getRelativeClientModule("translation_ollama").OllamaClient(root_path=root_path)
        result = self.ollama_client.authenticationCheck()
        if result is False:
            self.ollama_client = None
            self.ollama_connected = False
        else:
            self.ollama_connected = True
        return result

    def getOllamaModelList(self, root_path: str = None) -> bool:
        """Initialize Ollama client and fetch available models.

        Returns True on success, False on failure.
        """
        if self.ollama_client is None:
            return []
        return self.ollama_client.getModelList()

    def setOllamaModel(self, model: str) -> bool:
        """Change the Ollama model used for translation.

        Returns True on success, False on failure.
        """
        if self.ollama_client is None:
            return False
        return self.ollama_client.setModel(model)

    def updateOllamaClient(self) -> None:
        """Update the Ollama client (fetch available models)."""
        self.ollama_client.updateClient()

    def changeCTranslate2Model(self, path: str, model_type: str, device: str = "cpu", device_index: int = 0, compute_type: str = "auto") -> None:
        """Load a CTranslate2 model from weights.

        This sets internal translator/tokenizer objects and flips
        ``is_loaded_ctranslate2_model`` on success.
        """
        self.is_loaded_ctranslate2_model = False
        directory_name = ctranslate2_weights[model_type]["directory_name"]
        weight_path = os_path.join(path, "weights", "ctranslate2", directory_name)

        if compute_type == "auto":
            compute_type = getBestComputeType(device, device_index)
        self.ctranslate2_translator = _getCtrTranslate2().Translator(
            weight_path,
            device=device,
            device_index=device_index,
            compute_type=compute_type,
            inter_threads=1,
            intra_threads=4,
        )
        try:
            self.ctranslate2_tokenizer = loadCTranslate2Tokenizer(path, model_type, local_files_only=True)
        except Exception:
            errorLogging()
            self.ctranslate2_tokenizer = loadCTranslate2Tokenizer(path, model_type, local_files_only=False, repair_cache=True)
        self.is_loaded_ctranslate2_model = True

    def isLoadedCTranslate2Model(self) -> bool:
        return self.is_loaded_ctranslate2_model

    def isChangedTranslatorParameters(self) -> bool:
        return self.is_changed_translator_parameters

    def setChangedTranslatorParameters(self, is_changed: bool) -> None:
        self.is_changed_translator_parameters = is_changed

    def translateCTranslate2(self, message: str, source_language: str, target_language, weight_type: str) -> Any:
        """Translate using a loaded CTranslate2 model.

        Returns a string on success or False on failure (keeps legacy behavior).
        """
        result: Any = False
        if self.is_loaded_ctranslate2_model is True:
            try:
                self.ctranslate2_tokenizer.src_lang = source_language
                source = self.ctranslate2_tokenizer.convert_ids_to_tokens(self.ctranslate2_tokenizer.encode(message))
                match weight_type:
                    case "m2m100_418M-ct2-int8" | "m2m100_1.2B-ct2-int8":
                        target_prefix = [self.ctranslate2_tokenizer.lang_code_to_token[target_language]]
                    case "nllb-200-distilled-1.3B-ct2-int8" | "nllb-200-3.3B-ct2-int8":
                        target_prefix = [target_language]
                    case _:
                        return False
                results = self.ctranslate2_translator.translate_batch([source], target_prefix=[target_prefix])
                target = results[0].hypotheses[0][1:]
                result = self.ctranslate2_tokenizer.decode(self.ctranslate2_tokenizer.convert_tokens_to_ids(target))
            except Exception:
                errorLogging()
        return result

    @staticmethod
    def getLanguageCode(translator_name: str, weight_type: str, target_country: str, source_language: str, target_language: str) -> Tuple[str, str]:
        """Resolve a friendly language name to translator-specific codes.

        Returns (source_code, target_code).
        """
        match translator_name:
            case "DeepL_API":
                if target_language == "English":
                    if target_country in ["United States", "Canada", "Philippines"]:
                        target_language = "English American"
                    else:
                        target_language = "English British"
                elif target_language == "Portuguese":
                    if target_country in ["Portugal"]:
                        target_language = "Portuguese European"
                    else:
                        target_language = "Portuguese Brazilian"
                source_language = translation_lang[translator_name]["source"][source_language]
                target_language = translation_lang[translator_name]["target"][target_language]
            case "CTranslate2":
                source_language = translation_lang[translator_name][weight_type]["source"][source_language]
                target_language = translation_lang[translator_name][weight_type]["target"][target_language]
            case _:
                source_language = translation_lang[translator_name]["source"][source_language]
                target_language = translation_lang[translator_name]["target"][target_language]
        return source_language, target_language

    def _translate_once(
        self,
        name: str,
        weight: str,
        source: str,
        target: str,
        country: str,
        message: str,
        context: Optional[list[dict]],
        timeout_seconds: float,
    ) -> Any:
        """Dispatch one provider call, leaving classification to the caller."""
        result: Any = False
        if self._web_translator is None:
            self._web_translator = _getWebTranslator()
            if self._web_translator is None:
                self.is_enable_translators = False
        source, target = self.getLanguageCode(name, weight, country, source, target)
        match name:
            case "DeepL":
                if self.is_enable_translators is True and self._web_translator is not None:
                    result = self._web_translator(
                        query_text=message,
                        translator="deepl",
                        from_language=source,
                        to_language=target,
                    )
            case "DeepL_API":
                if self.is_enable_translators is True:
                    if self.deepl_client is None:
                        result = False
                    else:
                        result = self.deepl_client.translate_text(
                            message,
                            source_lang=source,
                            target_lang=target,
                        ).text
            case "Plamo_API":
                if self.plamo_client is not None:
                    result = self._translate_context_provider(
                        name, self.plamo_client, message, source, target, context
                    )
            case "Gemini_API":
                if self.gemini_client is not None:
                    result = self._translate_context_provider(
                        name, self.gemini_client, message, source, target, context
                    )
            case "OpenAI_API":
                if self.openai_client is not None:
                    result = self._translate_context_provider(
                        name, self.openai_client, message, source, target, context
                    )
            case "Groq_API":
                if self.groq_client is not None:
                    result = self._translate_context_provider(
                        name, self.groq_client, message, source, target, context
                    )
            case "OpenRouter_API":
                if self.openrouter_client is not None:
                    result = self._translate_context_provider(
                        name, self.openrouter_client, message, source, target, context
                    )
            case "LMStudio":
                if self.lmstudio_client is not None:
                    result = self._translate_context_provider(
                        name, self.lmstudio_client, message, source, target, context
                    )
            case "Ollama":
                if self.ollama_client is not None:
                    result = self._translate_context_provider(
                        name, self.ollama_client, message, source, target, context
                    )
            case "Google":
                if self.is_enable_translators is True and self._web_translator is not None:
                    result = self._web_translator(
                        query_text=message,
                        translator="google",
                        from_language=source,
                        to_language=target,
                        timeout=timeout_seconds,
                    )
            case "Bing":
                if self.is_enable_translators is True and self._web_translator is not None:
                    result = self._web_translator(
                        query_text=message,
                        translator="bing",
                        from_language=source,
                        to_language=target,
                        timeout=timeout_seconds,
                    )
            case "Papago":
                if self.is_enable_translators is True and self._web_translator is not None:
                    result = self._web_translator(
                        query_text=message,
                        translator="papago",
                        from_language=source,
                        to_language=target,
                    )
            case "CTranslate2":
                result = self.translateCTranslate2(
                    message=message,
                    source_language=source,
                    target_language=target,
                    weight_type=weight,
                )
        return result

    def _translate_context_provider(
        self,
        name: str,
        client: Any,
        message: str,
        source: str,
        target: str,
        context: Optional[list[dict]],
    ) -> Any:
        """Keep one shared client's context mutation and invocation atomic."""
        with self._context_provider_locks[name]:
            if context:
                client.setContextHistory(context)
            return client.translate(
                message,
                input_lang=source,
                output_lang=target,
            )

    def translateAttempt(
        self,
        translator_name: str,
        weight_type: str,
        source_language: str,
        target_language: str,
        target_country: str,
        message: str,
        context_history: Optional[list[dict]] = None,
        timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    ) -> TranslationAttempt:
        """Attempt one provider once and return its structured outcome."""
        started_at = perf_counter()
        if source_language == target_language:
            return TranslationAttempt(
                status=TranslationStatus.SUCCESS,
                engine=translator_name,
                message=message,
                duration_ms=0,
                error_code=None,
            )

        try:
            result = self._translate_once(
                translator_name,
                weight_type,
                source_language,
                target_language,
                target_country,
                message,
                context_history,
                timeout_seconds,
            )
        except PROVIDER_TIMEOUT_EXCEPTIONS:
            return TranslationAttempt(
                status=TranslationStatus.TIMEOUT,
                engine=translator_name,
                message=None,
                duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
                error_code="provider_timeout",
            )
        except Exception:
            errorLogging()
            return TranslationAttempt(
                status=TranslationStatus.ERROR,
                engine=translator_name,
                message=None,
                duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
                error_code="provider_error",
            )

        duration_ms = max(0, round((perf_counter() - started_at) * 1000))
        if result:
            return TranslationAttempt(
                status=TranslationStatus.SUCCESS,
                engine=translator_name,
                message=str(result),
                duration_ms=duration_ms,
                error_code=None,
            )
        return TranslationAttempt(
            status=TranslationStatus.ERROR,
            engine=translator_name,
            message=None,
            duration_ms=duration_ms,
            error_code="empty_provider_result",
        )

    def translate(
        self,
        translator_name: str,
        weight_type: str,
        source_language: str,
        target_language: str,
        target_country: str,
        message: str,
        context_history: Optional[list[dict]] = None,
    ) -> Any:
        """Adapt a single structured attempt to the legacy string/False API."""
        attempt = self.translateAttempt(
            translator_name=translator_name,
            weight_type=weight_type,
            source_language=source_language,
            target_language=target_language,
            target_country=target_country,
            message=message,
            context_history=context_history,
            timeout_seconds=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        )
        if attempt.status is TranslationStatus.SUCCESS:
            return attempt.message
        return False

if __name__ == "__main__":
    translator = Translator()
    # test CTranslate2 model nllb-200-distilled-1.3B-ct2-int8
    translator.changeCTranslate2Model(path=".", model_type="nllb-200-distilled-1.3B-ct2-int8", device="cpu", device_index=0)
    result = translator.translate(
        translator_name="CTranslate2",
        weight_type="nllb-200-distilled-1.3B-ct2-int8",
        source_language="English",
        target_language="Japanese",
        target_country="Japan",
        message="Hello, world!"
        )
    print(result)
