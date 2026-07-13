import copy
import importlib
import os
import sys
import unittest
from unittest.mock import Mock, patch

import requests

if not hasattr(requests, "exceptions"):
    sys.modules.pop("requests", None)
    requests = importlib.import_module("requests")


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import TranslationAttempt, TranslationStatus
from models.translation import translation_translator
from models.translation.translation_translator import Translator
import controller as controller_module
import model as model_module


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.providers = []

    def translate(self, **arguments):
        self.providers.append(arguments["translator_name"])
        index = len(self.providers) - 1
        if index >= len(self.responses):
            raise AssertionError("unexpected extra provider call")
        return self.responses[index]


class TranslationAttemptTests(unittest.TestCase):
    def setUp(self):
        self.translator = Translator()

    def _attempt(self, **overrides):
        arguments = {
            "translator_name": "Google",
            "weight_type": "unused",
            "source_language": "Japanese",
            "target_language": "English",
            "target_country": "United States",
            "message": "message",
            "context_history": None,
            "timeout_seconds": 5.0,
        }
        arguments.update(overrides)
        return self.translator.translateAttempt(**arguments)

    def test_google_and_bing_web_providers_receive_timeout(self):
        for provider in ("Google", "Bing"):
            with self.subTest(provider=provider):
                web_translator = Mock(return_value="translated")
                self.translator._web_translator = web_translator

                with patch.object(self.translator, "getLanguageCode", return_value=("ja", "en")):
                    result = self.translator._translate_once(
                        provider,
                        "unused",
                        "Japanese",
                        "English",
                        "United States",
                        "message",
                        None,
                        5.0,
                    )

                self.assertEqual(result, "translated")
                self.assertEqual(web_translator.call_count, 1)
                self.assertEqual(web_translator.call_args.kwargs["timeout"], 5.0)

    def test_timeout_exceptions_are_exact_and_classified(self):
        self.assertEqual(
            translation_translator.PROVIDER_TIMEOUT_EXCEPTIONS,
            (TimeoutError, requests.exceptions.Timeout),
        )

        for timeout_exception in (TimeoutError("late"), requests.exceptions.Timeout("late")):
            with self.subTest(exception=type(timeout_exception).__name__):
                with patch.object(self.translator, "_translate_once", side_effect=timeout_exception), patch.object(
                    translation_translator, "perf_counter", side_effect=[10.0, 10.025]
                ):
                    attempt = self._attempt()

                self.assertEqual(attempt.status, TranslationStatus.TIMEOUT)
                self.assertEqual(attempt.engine, "Google")
                self.assertIsNone(attempt.message)
                self.assertEqual(attempt.duration_ms, 25)
                self.assertEqual(attempt.error_code, "provider_timeout")

    def test_bing_timeout_is_classified_through_provider_dispatch(self):
        web_translator = Mock(side_effect=requests.exceptions.Timeout("late"))
        self.translator._web_translator = web_translator

        with patch.object(self.translator, "getLanguageCode", return_value=("ja", "en")), patch.object(
            translation_translator, "perf_counter", side_effect=[12.0, 12.01]
        ):
            attempt = self._attempt(translator_name="Bing")

        self.assertEqual(attempt.status, TranslationStatus.TIMEOUT)
        self.assertEqual(attempt.engine, "Bing")
        self.assertIsNone(attempt.message)
        self.assertEqual(attempt.error_code, "provider_timeout")
        self.assertEqual(web_translator.call_count, 1)
        self.assertEqual(web_translator.call_args.kwargs["timeout"], 5.0)

    def test_success_returns_structured_attempt(self):
        with patch.object(self.translator, "_translate_once", return_value="translated"), patch.object(
            translation_translator, "perf_counter", side_effect=[2.0, 2.008]
        ):
            attempt = self._attempt()

        self.assertEqual(attempt.status, TranslationStatus.SUCCESS)
        self.assertEqual(attempt.engine, "Google")
        self.assertEqual(attempt.message, "translated")
        self.assertEqual(attempt.duration_ms, 8)
        self.assertIsNone(attempt.error_code)

    def test_non_timeout_error_is_logged_and_classified(self):
        with patch.object(self.translator, "_translate_once", side_effect=ValueError("bad provider")), patch.object(
            translation_translator, "errorLogging"
        ) as error_logging, patch.object(translation_translator, "perf_counter", side_effect=[4.0, 4.003]):
            attempt = self._attempt()

        error_logging.assert_called_once_with()
        self.assertEqual(attempt.status, TranslationStatus.ERROR)
        self.assertEqual(attempt.engine, "Google")
        self.assertIsNone(attempt.message)
        self.assertEqual(attempt.duration_ms, 3)
        self.assertEqual(attempt.error_code, "provider_error")

    def test_empty_provider_results_are_errors(self):
        for empty_result in (False, "", None):
            with self.subTest(result=empty_result):
                with patch.object(self.translator, "_translate_once", return_value=empty_result):
                    attempt = self._attempt()

                self.assertEqual(attempt.status, TranslationStatus.ERROR)
                self.assertEqual(attempt.engine, "Google")
                self.assertIsNone(attempt.message)
                self.assertGreaterEqual(attempt.duration_ms, 0)
                self.assertEqual(attempt.error_code, "empty_provider_result")

    def test_same_language_returns_original_without_calling_provider(self):
        with patch.object(self.translator, "_translate_once") as translate_once:
            attempt = self._attempt(
                source_language="Japanese",
                target_language="Japanese",
                message="same",
            )

        translate_once.assert_not_called()
        self.assertEqual(attempt.status, TranslationStatus.SUCCESS)
        self.assertEqual(attempt.engine, "Google")
        self.assertEqual(attempt.message, "same")
        self.assertEqual(attempt.duration_ms, 0)
        self.assertIsNone(attempt.error_code)


class LegacyTranslationTests(unittest.TestCase):
    def _legacy_arguments(self):
        return {
            "translator_name": "Google",
            "weight_type": "unused",
            "source_language": "Japanese",
            "target_language": "English",
            "target_country": "United States",
            "message": "message",
        }

    def test_legacy_translate_adapts_one_attempt_to_string_or_false(self):
        translator = Translator()
        success = TranslationAttempt(
            status=TranslationStatus.SUCCESS,
            engine="Google",
            message="translated",
            duration_ms=1,
            error_code=None,
        )
        failure = TranslationAttempt(
            status=TranslationStatus.ERROR,
            engine="Google",
            message=None,
            duration_ms=1,
            error_code="provider_error",
        )
        with patch.object(translator, "translateAttempt", side_effect=[success, failure]) as translate_attempt:
            self.assertEqual(translator.translate(**self._legacy_arguments()), "translated")
            self.assertIs(translator.translate(**self._legacy_arguments()), False)

        self.assertEqual(translate_attempt.call_count, 2)
        self.assertEqual(translate_attempt.call_args.kwargs["timeout_seconds"], 5.0)

    def test_bounded_snapshot_preserves_order_and_never_injects_ctranslate2(self):
        snapshot = model_module.boundedTranslationProviderSnapshot(
            [" Google ", "", "Google", " Bing ", "CTranslate2"]
        )
        self.assertIs(type(snapshot), tuple)
        self.assertEqual(
            snapshot,
            ("Google", "Bing"),
        )
        self.assertEqual(model_module.boundedTranslationProviderSnapshot(["", "  ", None]), ())
        self.assertEqual(model_module.boundedTranslationProviderSnapshot("  Google  "), ("Google",))

    def _make_model(self, selection, provider):
        instance = object.__new__(model_module.Model)
        instance._inited = True
        instance.translator = provider
        instance.translation_history = []
        instance._translation_round_robin_indexes = {}
        target_languages = {
            "1": {
                "1": {
                    "enable": True,
                    "language": "English",
                    "country": "United States",
                }
            }
        }
        your_translation_languages = {
            "1": {
                "1": {
                    "enable": True,
                    "language": "English",
                    "country": "United States",
                }
            }
        }
        config_patch = patch.multiple(
            model_module.config,
            _SELECTED_TAB_NO="1",
            _SELECTED_TRANSLATION_ENGINES={"1": selection},
            _SELECTED_TARGET_LANGUAGES=target_languages,
            _SELECTED_YOUR_TRANSLATION_LANGUAGES=your_translation_languages,
            _CTRANSLATE2_WEIGHT_TYPE="unused",
        )
        return instance, config_patch

    def test_input_and_output_try_at_most_two_selected_providers(self):
        for method_name in ("getInputTranslate", "getOutputTranslate"):
            with self.subTest(method=method_name):
                provider = FakeProvider([False, False])
                instance, config_patch = self._make_model(
                    ["Google", "Bing", "CTranslate2"],
                    provider,
                )
                with config_patch:
                    translations, success = getattr(instance, method_name)(
                        "message",
                        source_language="Japanese",
                    )

                self.assertEqual(provider.providers, ["Google", "Bing"])
                self.assertEqual(translations, [False])
                self.assertEqual(success, [False])

    def test_empty_selection_terminates_without_calling_a_provider(self):
        for method_name in ("getInputTranslate", "getOutputTranslate"):
            with self.subTest(method=method_name):
                provider = FakeProvider([])
                instance, config_patch = self._make_model(["", "  "], provider)
                with config_patch:
                    translations, success = getattr(instance, method_name)(
                        "message",
                        source_language="Japanese",
                    )

                self.assertEqual(provider.providers, [])
                self.assertEqual(translations, [False])
                self.assertEqual(success, [False])

    def test_ctranslate2_is_only_used_when_present_in_original_snapshot(self):
        provider = FakeProvider([False])
        instance, config_patch = self._make_model(["Google"], provider)
        with config_patch:
            translations, success = instance.getInputTranslate("message", source_language="Japanese")

        self.assertEqual(provider.providers, ["Google"])
        self.assertEqual(translations, [False])
        self.assertEqual(success, [False])

        provider = FakeProvider([False, "local translation"])
        instance, config_patch = self._make_model(["Google", "CTranslate2"], provider)
        with config_patch:
            translations, success = instance.getInputTranslate("message", source_language="Japanese")

        self.assertEqual(provider.providers, ["Google", "CTranslate2"])
        self.assertEqual(translations, ["local translation"])
        self.assertEqual(success, [True])


class TypedChatTranslationTests(unittest.TestCase):
    def test_two_failures_report_existing_error_without_mutating_engine_selection(self):
        selected_engines = {"1": ["Google", "Bing"]}
        original_selection = copy.deepcopy(selected_engines)
        fake_model = Mock()
        fake_model.getInputTranslate.return_value = ([False, False], [False, False])
        fake_model.checkWebSocketServerAlive.return_value = False

        controller = controller_module.Controller()
        controller.run_mapping = {"error_translation_engine": "/error/translation-engine"}
        controller.run = Mock()
        controller.changeToCTranslate2Process = Mock()

        with patch.object(controller_module, "model", fake_model), patch.multiple(
            controller_module.config,
            _SELECTED_TAB_NO="1",
            _SELECTED_TRANSLATION_ENGINES=selected_engines,
            _ENABLE_TRANSLATION=True,
            _USE_EXCLUDE_WORDS=False,
            _CONVERT_MESSAGE_TO_HIRAGANA=False,
            _CONVERT_MESSAGE_TO_ROMAJI=False,
            _SELECTED_TAB_TARGET_LANGUAGES_NO_LIST=["1", "2"],
            _SEND_MESSAGE_TO_VRC=False,
            _OVERLAY_LARGE_LOG=False,
            _LOGGER_FEATURE=False,
        ):
            result = controller.chatMessage({"id": "chat-1", "message": "hello"})
            current_selection = controller_module.config.SELECTED_TRANSLATION_ENGINES

        self.assertEqual(result["status"], 200)
        controller.changeToCTranslate2Process.assert_not_called()
        self.assertEqual(current_selection, original_selection)
        self.assertTrue(
            any(call.args[1] == "/error/translation-engine" for call in controller.run.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
