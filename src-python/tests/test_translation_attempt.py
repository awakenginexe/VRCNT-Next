import copy
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.pipeline.pipeline_types import (
    FinalOutputTask,
    PipelineSource,
    TranslationAttempt,
    TranslationStatus,
    TranslationUpdate,
)
from models.translation import translation_translator
from models.translation.translation_translator import Translator
import controller as controller_module
import model as model_module

requests = translation_translator.requests


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


class BlockingContextClient:
    def __init__(self):
        self.current_context = None
        self.first_translate_entered = threading.Event()
        self.release_first_translate = threading.Event()
        self.second_context_set = threading.Event()
        self.active_calls = 0
        self.calls_overlapped = False
        self._active_lock = threading.Lock()

    def setContextHistory(self, context):
        self.current_context = context[0]["request"]
        if self.current_context == "B":
            self.second_context_set.set()

    def translate(self, message, input_lang, output_lang):
        request = self.current_context
        with self._active_lock:
            self.active_calls += 1
            if self.active_calls > 1:
                self.calls_overlapped = True
        try:
            if request == "A":
                self.first_translate_entered.set()
                self.release_first_translate.wait(timeout=2.0)
            return self.current_context
        finally:
            with self._active_lock:
                self.active_calls -= 1


class IndependentlyBlockingContextClient:
    def __init__(self, entered, release):
        self.context = None
        self.entered = entered
        self.release = release

    def setContextHistory(self, context):
        self.context = context[0]["request"]

    def translate(self, message, input_lang, output_lang):
        self.entered.set()
        self.release.wait(timeout=2.0)
        return self.context


class RecordingContextClient:
    def __init__(self):
        self.context = "unset"
        self.context_calls = []

    def setContextHistory(self, context):
        self.context_calls.append(context)
        self.context = context[0]["request"] if context else "empty"

    def translate(self, message, input_lang, output_lang):
        return self.context


class CaptureStartedPipeline:
    """Deterministic source-session boundary for controller integration tests."""

    def __init__(self, emit_initial):
        self.emit_initial = emit_initial
        self.traces = []

    def submit_trace(self, trace):
        self.traces.append(trace)
        self.emit_initial(trace)
        return True


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
        timeout_exception = translation_translator.PROVIDER_TIMEOUT_EXCEPTIONS[1]
        self.assertIs(timeout_exception, requests.exceptions.Timeout)
        web_translator = Mock(side_effect=timeout_exception("late"))
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

    def test_same_provider_context_and_call_are_atomic(self):
        client = BlockingContextClient()
        self.translator._web_translator = Mock()
        self.translator.plamo_client = client
        results = {}
        second_attempting = threading.Event()

        def run_attempt(request):
            if request == "B":
                second_attempting.set()
            results[request] = self._attempt(
                translator_name="Plamo_API",
                context_history=[{"request": request}],
            )

        first = threading.Thread(target=run_attempt, args=("A",), daemon=True)
        second = threading.Thread(target=run_attempt, args=("B",), daemon=True)
        with patch.object(self.translator, "getLanguageCode", return_value=("ja", "en")):
            first.start()
            self.assertTrue(client.first_translate_entered.wait(timeout=1.0))
            second.start()
            self.assertTrue(second_attempting.wait(timeout=1.0))
            second_entered_before_release = client.second_context_set.wait(timeout=0.1)
            client.release_first_translate.set()
            first.join(timeout=1.0)
            second.join(timeout=1.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertFalse(second_entered_before_release)
        self.assertFalse(client.calls_overlapped)
        self.assertEqual(results["A"].message, "A")
        self.assertEqual(results["B"].message, "B")

    def test_same_provider_empty_context_clears_stale_history(self):
        client = RecordingContextClient()
        self.translator._web_translator = Mock()
        self.translator.plamo_client = client
        context_a = [{"request": "A"}]

        with patch.object(self.translator, "getLanguageCode", return_value=("ja", "en")):
            attempts = [
                self._attempt(translator_name="Plamo_API", context_history=context_a),
                self._attempt(translator_name="Plamo_API", context_history=[]),
                self._attempt(translator_name="Plamo_API", context_history=None),
            ]

        self.assertEqual([attempt.message for attempt in attempts], ["A", "empty", "empty"])
        self.assertEqual(client.context_calls, [context_a, [], []])

    def test_different_provider_context_calls_do_not_share_a_lock(self):
        release = threading.Event()
        plamo_entered = threading.Event()
        gemini_entered = threading.Event()
        self.translator._web_translator = Mock()
        self.translator.plamo_client = IndependentlyBlockingContextClient(plamo_entered, release)
        self.translator.gemini_client = IndependentlyBlockingContextClient(gemini_entered, release)
        results = []

        def run_attempt(provider, request):
            results.append(
                self._attempt(
                    translator_name=provider,
                    context_history=[{"request": request}],
                )
            )

        first = threading.Thread(target=run_attempt, args=("Plamo_API", "A"), daemon=True)
        second = threading.Thread(target=run_attempt, args=("Gemini_API", "B"), daemon=True)
        with patch.object(self.translator, "getLanguageCode", return_value=("ja", "en")):
            first.start()
            second.start()
            both_entered = plamo_entered.wait(timeout=1.0) and gemini_entered.wait(timeout=1.0)
            release.set()
            first.join(timeout=1.0)
            second.join(timeout=1.0)

        self.assertTrue(both_entered)
        self.assertEqual({attempt.message for attempt in results}, {"A", "B"})

    def test_duration_uses_rounded_milliseconds(self):
        with patch.object(self.translator, "_translate_once", return_value="translated"), patch.object(
            translation_translator, "perf_counter", side_effect=[3.0, 3.0016]
        ):
            attempt = self._attempt()

        self.assertEqual(attempt.duration_ms, 2)

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


class ControllerTranslationSanitizationTests(unittest.TestCase):
    def _config_patch(self, *, send_only_translated=False, overlay_only_translated=False):
        return patch.multiple(
            controller_module.config,
            _SELECTED_TAB_NO="1",
            _SELECTED_TRANSLATION_ENGINES={"1": ["Google", "Bing"]},
            _ENABLE_TRANSLATION=True,
            _USE_EXCLUDE_WORDS=False,
            _CONVERT_MESSAGE_TO_HIRAGANA=True,
            _CONVERT_MESSAGE_TO_ROMAJI=False,
            _SELECTED_TAB_TARGET_LANGUAGES_NO_LIST=["1", "2"],
            _SELECTED_YOUR_LANGUAGES={
                "1": {"1": {"enable": True, "language": "English", "country": "United States"}}
            },
            _SELECTED_TARGET_LANGUAGES={
                "1": {
                    "1": {"enable": True, "language": "Japanese", "country": "Japan"},
                    "2": {"enable": True, "language": "French", "country": "France"},
                }
            },
            _SELECTED_YOUR_TRANSLATION_LANGUAGES={
                "1": {
                    "1": {"enable": True, "language": "Japanese", "country": "Japan"},
                    "2": {"enable": True, "language": "French", "country": "France"},
                }
            },
            _SEND_MESSAGE_TO_VRC=True,
            _SEND_ONLY_TRANSLATED_MESSAGES=send_only_translated,
            _OVERLAY_SMALL_LOG=True,
            _OVERLAY_LARGE_LOG=True,
            _OVERLAY_SHOW_ONLY_TRANSLATED_MESSAGES=overlay_only_translated,
            _LOGGER_FEATURE=True,
            _ENABLE_TRANSCRIPTION_SEND=True,
            _ENABLE_TRANSCRIPTION_RECEIVE=True,
            _SEND_RECEIVED_MESSAGE_TO_VRC=True,
            _ENABLE_CLIPBOARD=True,
            _WEBSOCKET_SERVER=True,
            _SEND_MESSAGE_FORMAT_PARTS={
                "message": {"prefix": "", "suffix": ""},
                "separator": "\n",
                "translation": {"prefix": "", "separator": "\n", "suffix": ""},
                "translation_first": False,
            },
            _RECEIVED_MESSAGE_FORMAT_PARTS={
                "message": {"prefix": "", "suffix": ""},
                "separator": "\n",
                "translation": {"prefix": "", "separator": "\n", "suffix": ""},
                "translation_first": False,
            },
        )

    def _fake_model(self):
        fake_model = Mock()
        fake_model.checkKeywords.return_value = False
        fake_model.detectRepeatSendMessage.return_value = False
        fake_model.detectRepeatReceiveMessage.return_value = False
        fake_model.checkWebSocketServerAlive.return_value = True
        fake_model.logger = Mock()
        fake_model.createOverlayImageLargeLog.return_value = "large-image"
        fake_model.createOverlayImageSmallLog.return_value = "small-image"

        def transliterate(message, **kwargs):
            self.assertIs(type(message), str)
            self.assertTrue(message)
            return [{"orig": message, "hira": f"kana:{message}"}]

        fake_model.convertMessageToTransliteration.side_effect = transliterate
        fake_model.getTranslationHistory.return_value = []
        fake_model.getSourcePipelineGeneration.return_value = 0
        fake_model.isSourcePipelineGenerationCurrent.return_value = True

        def pipeline_transliterate(message, language, output_config):
            if language != "Japanese":
                return ()
            if (
                not output_config.convert_message_to_hiragana
                and not output_config.convert_message_to_romaji
            ):
                return ()
            return tuple(
                fake_model.convertMessageToTransliteration(
                    message,
                    hiragana=output_config.convert_message_to_hiragana,
                    romaji=output_config.convert_message_to_romaji,
                )
            )

        fake_model.transliterateTranscriptionMessage.side_effect = pipeline_transliterate
        return fake_model

    def _controller(self):
        controller = controller_module.Controller()
        controller.run_mapping = {
            "error_translation_engine": "/error/translation-engine",
            "transcription_mic": "/transcription/mic",
            "transcription_speaker": "/transcription/speaker",
            "word_filter": "/word-filter",
        }
        controller.run = Mock()
        controller.changeToCTranslate2Process = Mock()
        controller.messageFormatter = Mock(return_value="formatted")
        controller._is_overlay_available = Mock(return_value=True)
        return controller

    @staticmethod
    def _run_payload(controller, endpoint):
        return next(call.args[2] for call in controller.run.call_args_list if call.args[1] == endpoint)

    def _run_progressive_output(
        self,
        controller,
        fake_model,
        source,
        result,
        legacy_translation_result,
    ):
        pipeline = CaptureStartedPipeline(controller._emitInitialTranscriptionTrace)
        fake_model.getSourcePipeline.return_value = pipeline

        callback = (
            controller.micMessage
            if source is PipelineSource.MIC
            else controller.speakerMessage
        )
        callback({**result, "started_at_monotonic": 10.0})
        self.assertEqual(len(pipeline.traces), 1)
        trace = pipeline.traces[0]

        values, flags = legacy_translation_result
        updates = []
        for index, target in enumerate(trace.targets):
            value = values[index] if index < len(values) else None
            succeeded = (
                index < len(flags)
                and flags[index] is True
                and isinstance(value, str)
                and bool(value)
            )
            if succeeded:
                transliteration = fake_model.transliterateTranscriptionMessage(
                    value,
                    target.language,
                    trace.output_config,
                )
                update = TranslationUpdate(
                    trace_id=trace.trace_id,
                    target_slot=target.target_slot,
                    status=TranslationStatus.SUCCESS,
                    engine="Google",
                    message=value,
                    transliteration=transliteration,
                    duration_ms=1,
                    queue_position=0,
                    error_code=None,
                )
            else:
                update = TranslationUpdate(
                    trace_id=trace.trace_id,
                    target_slot=target.target_slot,
                    status=TranslationStatus.ERROR,
                    engine="Google",
                    message=None,
                    transliteration=(),
                    duration_ms=1,
                    queue_position=0,
                    error_code="provider_error",
                )
            updates.append(update)
            controller._emitTranslationUpdate(update)

        task = FinalOutputTask(
            trace_id=trace.trace_id,
            generation=trace.generation,
            source=trace.source,
            original_message=trace.original_message,
            source_language=trace.source_language,
            original_transliteration=trace.original_transliteration,
            targets=trace.targets,
            translations=tuple(updates),
            output_config=trace.output_config,
            started_at_monotonic=trace.started_at_monotonic,
        )
        if source is PipelineSource.MIC:
            controller._finalizeMicOutput(task)
        else:
            controller._finalizeSpeakerOutput(task)

        source_endpoint = (
            "/transcription/mic"
            if source is PipelineSource.MIC
            else "/transcription/speaker"
        )
        self.assertEqual(controller.run.call_args_list[0].args[1], source_endpoint)
        initial_payload = controller.run.call_args_list[0].args[2]
        self.assertTrue(
            all(item["message"] is None for item in initial_payload["translations"])
        )
        self.assertTrue(
            all(update.message is None or isinstance(update.message, str) for update in updates)
        )
        fake_model.getInputTranslate.assert_not_called()
        fake_model.getOutputTranslate.assert_not_called()
        return trace, tuple(updates)

    def test_chat_partial_failure_keeps_string_slots_and_sanitizes_side_effects(self):
        fake_model = self._fake_model()
        fake_model.getInputTranslate.return_value = (["translated", False], [True, False])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            result = controller.chatMessage({"id": "chat-1", "message": "hello"})
            current_selection = controller_module.config.SELECTED_TRANSLATION_ENGINES

        self.assertEqual(
            [item["message"] for item in result["result"]["translations"]],
            ["translated", ""],
        )
        expected_target = {
            "1": {"enable": True, "language": "Japanese", "country": "Japan"}
        }
        self.assertEqual(controller.messageFormatter.call_args.args[1], ["translated"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["translated"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["translated"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.logger.info.assert_called_once_with("[CHAT] hello (translated)")
        self.assertEqual(current_selection, {"1": ["Google", "Bing"]})
        controller.changeToCTranslate2Process.assert_not_called()

    def test_chat_total_failure_suppresses_translated_only_side_effects(self):
        fake_model = self._fake_model()
        fake_model.getInputTranslate.return_value = ([False, False], [False, False])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch(
            send_only_translated=True,
            overlay_only_translated=True,
        ):
            result = controller.chatMessage({"id": "chat-2", "message": "hello"})

        self.assertEqual(
            [item["message"] for item in result["result"]["translations"]],
            ["", ""],
        )
        fake_model.convertMessageToTransliteration.assert_not_called()
        controller.messageFormatter.assert_not_called()
        fake_model.oscSendMessage.assert_not_called()
        fake_model.createOverlayImageLargeLog.assert_not_called()
        self.assertEqual(fake_model.websocketSendMessage.call_args.args[0]["translation"], [])
        fake_model.logger.info.assert_called_once_with("[CHAT] hello")

    def test_chat_reversed_partial_failure_keeps_target_two_metadata_aligned(self):
        fake_model = self._fake_model()
        fake_model.getInputTranslate.return_value = ([False, "deux"], [False, True])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            result = controller.chatMessage({"id": "chat-3", "message": "hello"})

        expected_target = {
            "2": {"enable": True, "language": "French", "country": "France"}
        }
        self.assertEqual(
            [item["message"] for item in result["result"]["translations"]],
            ["", "deux"],
        )
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["deux"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["deux"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.convertMessageToTransliteration.assert_not_called()

    def test_mic_partial_failure_sanitizes_all_outputs_without_engine_mutation(self):
        fake_model = self._fake_model()
        fake_model.getInputTranslate.return_value = (["translated", False], [True, False])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            _, updates = self._run_progressive_output(
                controller,
                fake_model,
                PipelineSource.MIC,
                {"text": "spoken", "language": "English"},
                fake_model.getInputTranslate.return_value,
            )
            current_selection = controller_module.config.SELECTED_TRANSLATION_ENGINES

        payload = self._run_payload(controller, "/transcription/mic")
        self.assertEqual([item["message"] for item in payload["translations"]], [None, None])
        self.assertEqual([update.message for update in updates], ["translated", None])
        self.assertEqual(
            [update.status for update in updates],
            [TranslationStatus.SUCCESS, TranslationStatus.ERROR],
        )
        expected_target = {
            "1": {"enable": True, "language": "Japanese", "country": "Japan"}
        }
        controller.messageFormatter.assert_not_called()
        fake_model.oscSendMessage.assert_called_once_with("spoken\ntranslated")
        fake_model.setCopyToClipboardAndPasteFromClipboard.assert_called_once_with(
            "spoken\ntranslated"
        )
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["translated"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["translated"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.logger.info.assert_called_once_with("[SENT] spoken (translated)")
        fake_model.convertMessageToTransliteration.assert_called_once()
        self.assertEqual(current_selection, {"1": ["Google", "Bing"]})
        controller.changeToCTranslate2Process.assert_not_called()

    def test_mic_reversed_partial_failure_keeps_target_two_metadata_aligned(self):
        fake_model = self._fake_model()
        fake_model.getInputTranslate.return_value = ([False, "deux"], [False, True])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            _, updates = self._run_progressive_output(
                controller,
                fake_model,
                PipelineSource.MIC,
                {"text": "spoken", "language": "English"},
                fake_model.getInputTranslate.return_value,
            )

        expected_target = {
            "2": {"enable": True, "language": "French", "country": "France"}
        }
        payload = self._run_payload(controller, "/transcription/mic")
        self.assertEqual([item["message"] for item in payload["translations"]], [None, None])
        self.assertEqual([update.message for update in updates], [None, "deux"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["deux"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["deux"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.convertMessageToTransliteration.assert_not_called()

    def test_speaker_total_failure_uses_original_only_and_string_response_slot(self):
        fake_model = self._fake_model()
        fake_model.getOutputTranslate.return_value = ([False], [False])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            _, updates = self._run_progressive_output(
                controller,
                fake_model,
                PipelineSource.SPEAKER,
                {"text": "heard", "language": "English"},
                fake_model.getOutputTranslate.return_value,
            )
            current_selection = controller_module.config.SELECTED_TRANSLATION_ENGINES

        payload = self._run_payload(controller, "/transcription/speaker")
        self.assertEqual([item["message"] for item in payload["translations"]], [None, None])
        self.assertEqual([update.message for update in updates], [None, None])
        self.assertTrue(all(type(update.message) is not bool for update in updates))
        fake_model.convertMessageToTransliteration.assert_not_called()
        controller.messageFormatter.assert_not_called()
        fake_model.oscSendMessage.assert_called_once_with("heard")
        self.assertEqual(fake_model.createOverlayImageSmallLog.call_args.args[2], [])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], [])
        self.assertEqual(fake_model.websocketSendMessage.call_args.args[0]["translation"], [])
        fake_model.logger.info.assert_called_once_with("[RECEIVED] heard")
        self.assertEqual(current_selection, {"1": ["Google", "Bing"]})
        controller.changeToCTranslate2Process.assert_not_called()

    def test_speaker_first_success_keeps_target_one_metadata_aligned(self):
        fake_model = self._fake_model()
        fake_model.getOutputTranslate.return_value = (["ichi", False], [True, False])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            _, updates = self._run_progressive_output(
                controller,
                fake_model,
                PipelineSource.SPEAKER,
                {"text": "heard", "language": "English"},
                fake_model.getOutputTranslate.return_value,
            )

        expected_target = {
            "1": {"enable": True, "language": "Japanese", "country": "Japan"}
        }
        payload = self._run_payload(controller, "/transcription/speaker")
        self.assertEqual([item["message"] for item in payload["translations"]], [None, None])
        self.assertEqual([update.message for update in updates], ["ichi", None])
        self.assertEqual(fake_model.createOverlayImageSmallLog.call_args.args[2], ["ichi"])
        self.assertEqual(fake_model.createOverlayImageSmallLog.call_args.args[3], expected_target)
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["ichi"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["ichi"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.convertMessageToTransliteration.assert_called_once()

    def test_speaker_reversed_partial_failure_keeps_target_two_metadata_aligned(self):
        fake_model = self._fake_model()
        fake_model.getOutputTranslate.return_value = ([False, "deux"], [False, True])
        controller = self._controller()

        with patch.object(controller_module, "model", fake_model), self._config_patch():
            _, updates = self._run_progressive_output(
                controller,
                fake_model,
                PipelineSource.SPEAKER,
                {"text": "heard", "language": "English"},
                fake_model.getOutputTranslate.return_value,
            )

        expected_target = {
            "2": {"enable": True, "language": "French", "country": "France"}
        }
        payload = self._run_payload(controller, "/transcription/speaker")
        self.assertEqual([item["message"] for item in payload["translations"]], [None, None])
        self.assertEqual([update.message for update in updates], [None, "deux"])
        self.assertEqual(fake_model.createOverlayImageSmallLog.call_args.args[2], ["deux"])
        self.assertEqual(fake_model.createOverlayImageSmallLog.call_args.args[3], expected_target)
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[3], ["deux"])
        self.assertEqual(fake_model.createOverlayImageLargeLog.call_args.args[4], expected_target)
        websocket_payload = fake_model.websocketSendMessage.call_args.args[0]
        self.assertEqual(websocket_payload["translation"], ["deux"])
        self.assertEqual(websocket_payload["dst_languages"], expected_target)
        fake_model.convertMessageToTransliteration.assert_not_called()


if __name__ == "__main__":
    unittest.main()
