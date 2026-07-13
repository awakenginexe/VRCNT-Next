import copy
import gc
import asyncio
import json
from collections import deque
from subprocess import Popen
from os import makedirs as os_makedirs
from os import path as os_path
from datetime import datetime
from time import monotonic, sleep, time
from queue import Queue
from threading import Condition, Event, RLock, Thread, current_thread
from requests import get as requests_get
from typing import Callable, Optional, cast
from packaging.version import parse

from flashtext import KeywordProcessor

from device_manager import device_manager
from config import config

from models.translation.translation_translator import Translator
from models.osc.osc import OSCHandler
from models.transcription.transcription_recorder import SelectedMicEnergyAndAudioRecorder, SelectedSpeakerEnergyAndAudioRecorder
from models.transcription.transcription_recorder import SelectedMicEnergyRecorder, SelectedSpeakerEnergyRecorder
from models.pipeline.pipeline_types import (
    OutputConfigSnapshot,
    PipelineSource,
    PipelineStatusEvent,
)
from models.pipeline.latest_queue import LatestQueue
from models.pipeline.source_pipeline import SourcePipeline
from models.transcription.transcription_transcriber import (
    AudioTranscriber,
    TranscriberPipelineContext,
)
from models.transcription.whisper_runtime import (
    WhisperRuntimeKey,
    WhisperRuntimeLease,
    WhisperRuntimeManager,
)
from models.translation.translation_languages import translation_lang
from models.transcription.transcription_languages import transcription_lang
from models.translation.translation_utils import checkCTranslate2Weight, checkCTranslate2Tokenizer, downloadCTranslate2Weight, downloadCTranslate2Tokenizer, backwardCompatibleRenameWeightsDir
from models.transcription.transcription_whisper import (
    checkWhisperWeight,
    downloadWhisperWeight,
    resolveWhisperComputeType,
)
from models.transcription.transcription_vosk import checkVoskWeight, downloadVoskWeight
from models.transcription.transcription_parakeet import checkParakeetWeight, downloadParakeetWeight
from models.transcription.transcription_sensevoice import checkSenseVoiceWeight, downloadSenseVoiceWeight
from models.transliteration.transliteration_transliterator import Transliterator
from models.overlay.overlay import Overlay
from models.overlay.overlay_image import OverlayImage
from models.watchdog.watchdog import Watchdog
from models.websocket.websocket_server import WebSocketServer
from models.clipboard.clipboard import Clipboard
from models.telemetry import Telemetry
from utils import errorLogging, setupLogger, printLog
from errors import DeviceUnavailableError, ErrorCode

TRANSCRIPT_THREAD_JOIN_TIMEOUT = 2.0
TRANSCRIPT_STALL_RESTART_SECONDS = 90.0
TRANSCRIPT_STALL_CHECK_SECONDS = 5.0
DEFAULT_TRANSLATION_ENGINE = "CTranslate2"
TRANSCRIPTION_AUDIO_QUEUE_SIZE = 4
TRANSCRIPTION_PIPELINE_METRIC_HISTORY_SIZE = 256


class _MetricAudioQueue(LatestQueue):
    """Latest-only capture queue with non-blocking admission metrics."""

    def __init__(self, source: PipelineSource, emit_metric: Callable) -> None:
        super().__init__(TRANSCRIPTION_AUDIO_QUEUE_SIZE)
        self._source = source
        self._emit_metric = emit_metric
        self._dropped_count = 0

    def offer(self, item):
        result = super().offer(item)
        if result.accepted:
            self._emit_metric(
                self._source,
                stage="queue",
                outcome="waiting",
                queue_depth=result.depth,
                dropped_count=self._dropped_count,
            )
        return result

    def record_drop(self) -> None:
        self._dropped_count += 1
        self._emit_metric(
            self._source,
            stage="queue",
            outcome="skipped_overload",
            queue_depth=self.qsize(),
            dropped_count=self._dropped_count,
            error_code="audio_queue_overload",
        )


def normalizeTranslationEngineSelection(selection, fallback: str = DEFAULT_TRANSLATION_ENGINE) -> list[str]:
    if isinstance(selection, str):
        return [selection] if selection else [fallback]
    if isinstance(selection, list):
        engines = []
        for engine in selection:
            if isinstance(engine, str) and engine and engine not in engines:
                engines.append(engine)
        return engines or [fallback]
    return [fallback]


def boundedTranslationProviderSnapshot(selection) -> tuple[str, ...]:
    """Return at most two providers without local fallback behind online."""
    if isinstance(selection, str):
        values = [selection]
    elif isinstance(selection, (list, tuple)):
        values = selection
    else:
        values = []

    providers = []
    for value in values:
        if not isinstance(value, str):
            continue
        provider = value.strip()
        if not provider or provider in providers:
            continue
        if provider == "CTranslate2" and providers and providers[0] != "CTranslate2":
            continue
        providers.append(provider)
        if len(providers) == 2:
            break
    return tuple(providers)


def collapseTranslationProviderSnapshot(selection):
    """Collapse a bounded provider snapshot without inventing a provider."""
    providers = boundedTranslationProviderSnapshot(selection)
    if not providers:
        return ""
    if len(providers) == 1:
        return providers[0]
    return list(providers)


def collapseTranslationEngineSelection(engines: list[str], fallback: str = DEFAULT_TRANSLATION_ENGINE):
    normalized = normalizeTranslationEngineSelection(engines, fallback=fallback)
    if len(normalized) == 1:
        return normalized[0]
    return normalized[:2]

class threadFnc(Thread):
    """A tiny Thread wrapper that repeatedly calls a function.

    Usage: threadFnc(fnc, end_fnc=None, daemon=True, *args, **kwargs)
    The target function will be called repeatedly inside run().
    """
    def __init__(self, fnc, end_fnc=None, daemon: bool = True, *args, **kwargs):
        # Do not pass target to super; manage call explicitly so we can
        # store args/kwargs on the instance for later use.
        super(threadFnc, self).__init__(daemon=daemon)
        self.fnc = fnc
        self.end_fnc = end_fnc
        self.loop = True
        self._pause = False
        self._args = args
        self._kwargs = kwargs

    def stop(self) -> None:
        self.loop = False

    def pause(self) -> None:
        self._pause = True

    def resume(self) -> None:
        self._pause = False

    def run(self) -> None:
        try:
            while self.loop:
                try:
                    self.fnc(*self._args, **self._kwargs)
                except Exception:
                    # Protect the thread from terminating on user exceptions
                    errorLogging()
                while self._pause:
                    sleep(0.1)
        finally:
            if callable(self.end_fnc):
                try:
                    self.end_fnc()
                except Exception:
                    errorLogging()
        return

class Model:
    _instance = None
    _transcription_metric_state_init_lock = RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Model, cls).__new__(cls)
            # Do NOT call init() here to avoid heavy import-time work.
            # Callers should call `model.init()` explicitly or rely on
            # `ensure_initialized()` which will lazy-initialize on demand.
            cls._instance._inited = False
        return cls._instance

    def init(self):
        """Perform full initialization of resources.

        This method performs heavy construction (models, overlay, threads)
        and is intentionally not called at import time. Call explicitly
        or let `ensure_initialized()` call it lazily.
        """
        if getattr(self, '_inited', False):
            return

        recovery_callback = getattr(
            self,
            "_transcription_recovery_callback",
            None,
        )
        metric_callback = getattr(
            self,
            "_transcription_pipeline_metric_callback",
            None,
        )
        self.logger = None
        self.th_check_device = None
        self.mic_print_transcript = None
        self.mic_audio_recorder = None
        self.mic_transcriber = None
        self.mic_whisper_runtime_lease = None
        self.mic_transcript_stop_event = None
        self.mic_energy_recorder = None
        self.mic_energy_plot_progressbar = None
        self.speaker_print_transcript = None
        self.speaker_audio_queue = None
        self.speaker_audio_recorder = None
        self.speaker_transcriber = None
        self.speaker_whisper_runtime_lease = None
        self.speaker_transcript_stop_event = None
        self.speaker_energy_recorder = None
        self.speaker_energy_plot_progressbar = None

        self.previous_send_message = ""
        self.previous_receive_message = ""
        self.translator = Translator()
        self._translation_round_robin_indexes: dict[str, int] = {}
        self.keyword_processor = KeywordProcessor()
        self.translation_history: list[dict] = []
        self.translation_history_max_items = 20
        overlay_small_log_settings = copy.deepcopy(config.OVERLAY_SMALL_LOG_SETTINGS)
        overlay_large_log_settings = copy.deepcopy(config.OVERLAY_LARGE_LOG_SETTINGS)
        overlay_large_log_settings["ui_scaling"] = overlay_large_log_settings["ui_scaling"] * 0.25
        overlay_settings = {
            "small": overlay_small_log_settings,
            "large": overlay_large_log_settings,
        }
        self.overlay = Overlay(overlay_settings)
        self.overlay_image = OverlayImage(config.PATH_LOCAL)
        self.mic_audio_queue = None
        self.mic_mute_status = None
        self.transliterator = None
        self.watchdog = Watchdog(config.WATCHDOG_TIMEOUT, config.WATCHDOG_INTERVAL)
        self.osc_handler = OSCHandler(config.OSC_IP_ADDRESS, config.OSC_PORT)
        self.websocket_server = None
        self.websocket_server_loop = False
        self.websocket_server_alive = False
        self.th_websocket_server = None
        # default no-op callbacks for energy check functions
        self.check_mic_energy_fnc: Callable[[float], None] = lambda v: None
        self.check_speaker_energy_fnc: Callable[[float], None] = lambda v: None
        self.clipboard = Clipboard()
        self.telemetry = Telemetry()
        self.whisper_runtime_manager = WhisperRuntimeManager()
        self._transcription_pipeline_metric_lock = RLock()
        self.transcription_pipeline_metrics = deque(
            maxlen=TRANSCRIPTION_PIPELINE_METRIC_HISTORY_SIZE
        )
        self._transcription_pipeline_metric_callback = metric_callback
        self._transcription_recovery_callback = recovery_callback
        self.mic_source_pipeline: Optional[SourcePipeline] = None
        self.speaker_source_pipeline: Optional[SourcePipeline] = None
        self._source_pipeline_generations: dict[PipelineSource, int] = {}
        self._source_pipeline_generation_counters: dict[PipelineSource, int] = {
            PipelineSource.MIC: 0,
            PipelineSource.SPEAKER: 0,
        }
        self._source_transcription_sessions: dict[PipelineSource, dict] = {}
        self._source_heartbeat_timestamps: dict[PipelineSource, float] = {}
        self._source_session_lock = RLock()
        self._source_session_condition = Condition(self._source_session_lock)
        self._source_pipeline_transitions: set[PipelineSource] = set()

        self._inited = True

    def _acquireWhisperRuntimeLease(self) -> Optional[WhisperRuntimeLease]:
        if config.SELECTED_TRANSCRIPTION_ENGINE != "Whisper":
            return None
        if checkWhisperWeight(config.PATH_DATA, config.WHISPER_WEIGHT_TYPE) is not True:
            return None
        device = config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device"]
        device_index = config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device_index"]
        compute_type = resolveWhisperComputeType(
            device,
            device_index,
            config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
        )
        key = WhisperRuntimeKey(
            weight_type=config.WHISPER_WEIGHT_TYPE,
            device=device,
            device_index=device_index,
            compute_type=compute_type,
        )
        return self.whisper_runtime_manager.acquire(config.PATH_DATA, key)

    def _recordTranscriptionPipelineMetric(
        self,
        event: PipelineStatusEvent,
    ) -> None:
        self._ensureTranscriptionMetricState()
        with self._transcription_pipeline_metric_lock:
            self.transcription_pipeline_metrics.append(event)
            callback = self._transcription_pipeline_metric_callback
        if callable(callback):
            try:
                callback(event)
            except Exception:
                # Status transport is diagnostic and must never terminate a
                # capture, queue, or transcription worker.
                errorLogging()

    def _ensureTranscriptionMetricState(self) -> None:
        """Backfill bounded metric state for focused/bare Model instances."""
        with self._transcription_metric_state_init_lock:
            if not hasattr(self, "_transcription_pipeline_metric_lock"):
                self._transcription_pipeline_metric_lock = RLock()
            if not hasattr(self, "transcription_pipeline_metrics"):
                self.transcription_pipeline_metrics = deque(
                    maxlen=TRANSCRIPTION_PIPELINE_METRIC_HISTORY_SIZE
                )
            if not hasattr(self, "_transcription_pipeline_metric_callback"):
                self._transcription_pipeline_metric_callback = None

    def setTranscriptionPipelineMetricCallback(
        self,
        callback: Optional[Callable[[PipelineStatusEvent], None]],
    ) -> None:
        """Register the optional non-owning Controller status callback."""
        if callback is not None and not callable(callback):
            raise TypeError("metric callback must be callable or None")
        self._ensureTranscriptionMetricState()
        with self._transcription_pipeline_metric_lock:
            self._transcription_pipeline_metric_callback = callback

    def clearTranscriptionPipelineMetricCallback(
        self,
        callback: Callable[[PipelineStatusEvent], None],
    ) -> bool:
        """Detach ``callback`` without clearing a newer Controller owner."""
        self._ensureTranscriptionMetricState()
        with self._transcription_pipeline_metric_lock:
            if self._transcription_pipeline_metric_callback != callback:
                return False
            self._transcription_pipeline_metric_callback = None
            return True

    def _ensureTranscriptionLifecycleState(self) -> None:
        """Backfill lifecycle-only state for focused/bare Model instances."""
        if not hasattr(self, "_source_session_lock"):
            self._source_session_lock = RLock()
        with self._source_session_lock:
            if not hasattr(self, "_source_session_condition"):
                self._source_session_condition = Condition(
                    self._source_session_lock
                )
            if not hasattr(self, "_source_pipeline_transitions"):
                self._source_pipeline_transitions = set()
            if not hasattr(self, "_source_pipeline_generations"):
                self._source_pipeline_generations = {}
            if not hasattr(self, "_source_pipeline_generation_counters"):
                self._source_pipeline_generation_counters = {
                    PipelineSource.MIC: 0,
                    PipelineSource.SPEAKER: 0,
                }
            if not hasattr(self, "_source_transcription_sessions"):
                self._source_transcription_sessions = {}
            if not hasattr(self, "_source_heartbeat_timestamps"):
                self._source_heartbeat_timestamps = {}
        self._ensureTranscriptionMetricState()

    def _emitTranscriptionLifecycleMetric(
        self,
        source: PipelineSource,
        *,
        stage: str,
        outcome: str,
        queue_depth: int = 0,
        dropped_count: int = 0,
        error_code: Optional[str] = None,
        engine: Optional[str] = None,
    ) -> None:
        self._recordTranscriptionPipelineMetric(
            PipelineStatusEvent(
                schema_version=1,
                trace_id=None,
                source=source,
                stage=stage,
                engine=engine,
                target_slot=None,
                outcome=outcome,
                queue_age_ms=None,
                duration_ms=None,
                queue_depth=max(0, queue_depth),
                dropped_count=max(0, dropped_count),
                observed_at_ms=int(time() * 1000),
                error_code=error_code,
            )
        )

    def setTranscriptionRecoveryCallback(
        self,
        callback: Optional[
            Callable[[PipelineSource, int, str, Event], None]
        ],
    ) -> None:
        """Register the non-blocking Controller recovery offer callback."""
        self._transcription_recovery_callback = callback

    def _recordTranscriptionRecoveryRequest(
        self,
        source: PipelineSource,
        generation: int,
        error_code: str,
        safe_to_restart: Event,
    ) -> None:
        callback = getattr(self, "_transcription_recovery_callback", None)
        if callable(callback):
            callback(source, generation, error_code, safe_to_restart)

    def recordTranscriptionRecovery(
        self,
        source: PipelineSource,
        error_code: str,
    ) -> None:
        self._emitTranscriptionLifecycleMetric(
            source,
            stage="transcription",
            outcome="recovered",
            error_code=error_code,
            engine="Whisper",
        )

    def recordTranscriptionRecoveryFailure(
        self,
        source: PipelineSource,
        error_code: str,
    ) -> None:
        del error_code  # Never expose provider/runtime exception text in metrics.
        self._emitTranscriptionLifecycleMetric(
            source,
            stage="transcription",
            outcome="error",
            error_code="recovery_failed",
            engine="Whisper",
        )

    @staticmethod
    def _sourcePipelineAttribute(source: PipelineSource) -> str:
        if source is PipelineSource.MIC:
            return "mic_source_pipeline"
        if source is PipelineSource.SPEAKER:
            return "speaker_source_pipeline"
        raise ValueError(f"unknown pipeline source: {source}")

    def getSourcePipeline(self, source: PipelineSource) -> Optional[SourcePipeline]:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            return getattr(self, self._sourcePipelineAttribute(source), None)

    def getSourcePipelineGeneration(
        self,
        source: PipelineSource,
    ) -> Optional[int]:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            return self._source_pipeline_generations.get(source)

    def nextSourcePipelineGeneration(self, source: PipelineSource) -> int:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            current = self._source_pipeline_generation_counters.get(source, 0)
            generation = current + 1
            self._source_pipeline_generation_counters[source] = generation
            return generation

    def isTranscriptionSourceActive(self, source: PipelineSource) -> bool:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            session = self._source_transcription_sessions.get(source)
            current_generation = self._source_pipeline_generations.get(source)
            pipeline = getattr(
                self,
                self._sourcePipelineAttribute(source),
                None,
            )
            return bool(
                session is not None
                and not session["stop_event"].is_set()
                and current_generation == session["generation"]
                and pipeline is not None
            )

    def isSourcePipelineGenerationCurrent(
        self,
        source: PipelineSource,
        generation: int,
    ) -> bool:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            return (
                self._source_pipeline_generations.get(source) == generation
                and getattr(
                    self,
                    self._sourcePipelineAttribute(source),
                    None,
                )
                is not None
            )

    def _beginSourcePipelineTransition(self, source: PipelineSource) -> None:
        self._ensureTranscriptionLifecycleState()
        with self._source_session_condition:
            while source in self._source_pipeline_transitions:
                self._source_session_condition.wait()
            self._source_pipeline_transitions.add(source)

    def _endSourcePipelineTransition(self, source: PipelineSource) -> None:
        with self._source_session_condition:
            self._source_pipeline_transitions.discard(source)
            self._source_session_condition.notify_all()

    def transliterateTranscriptionMessage(
        self,
        message: str,
        language: str,
        output_config: OutputConfigSnapshot,
    ) -> tuple[dict[str, str], ...]:
        if language != "Japanese":
            return ()
        if (
            not output_config.convert_message_to_hiragana
            and not output_config.convert_message_to_romaji
        ):
            return ()
        return tuple(
            deepcopy(
                self.convertMessageToTransliteration(
                    message,
                    hiragana=output_config.convert_message_to_hiragana,
                    romaji=output_config.convert_message_to_romaji,
                )
            )
        )

    def ensureSourcePipeline(
        self,
        source: PipelineSource,
        callbacks: dict[str, Callable],
        generation: int,
    ) -> SourcePipeline:
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        required_callbacks = (
            "emit_initial",
            "emit_update",
            "emit_metric",
            "emit_final",
        )
        missing = [name for name in required_callbacks if not callable(callbacks.get(name))]
        if missing:
            raise ValueError(
                "missing source pipeline callbacks: " + ", ".join(missing)
            )

        self._beginSourcePipelineTransition(source)
        pipeline = None
        try:
            attribute = self._sourcePipelineAttribute(source)
            with self._source_session_lock:
                self._source_pipeline_generation_counters[source] = max(
                    generation,
                    self._source_pipeline_generation_counters.get(source, 0),
                )
                current = getattr(self, attribute, None)
                current_generation = self._source_pipeline_generations.get(source)
                if current is not None and current_generation == generation:
                    return current

                # Publication and detachment are one identity transaction.
                setattr(self, attribute, None)
                self._source_pipeline_generations.pop(source, None)

            # Destruction may join workers and invoke third-party cleanup, so it
            # must happen after the atomic detach and outside the identity lock.
            self._stopDetachedSourcePipeline(current, current_generation)

            pipeline = SourcePipeline(
                source=source,
                translator=self.translator,
                transliterate=self.transliterateTranscriptionMessage,
                emit_initial=callbacks["emit_initial"],
                emit_update=callbacks["emit_update"],
                emit_metric=callbacks["emit_metric"],
                emit_final=callbacks["emit_final"],
                is_generation_current=lambda candidate: (
                    self.isSourcePipelineGenerationCurrent(source, candidate)
                ),
            )
            pipeline.start(generation)
            with self._source_session_lock:
                setattr(self, attribute, pipeline)
                self._source_pipeline_generations[source] = generation
            return pipeline
        except Exception:
            if pipeline is not None:
                try:
                    pipeline.stop(generation, discard_pending=True)
                except Exception:
                    errorLogging()
            raise
        finally:
            self._endSourcePipelineTransition(source)

    def stopSourcePipeline(self, source: PipelineSource) -> None:
        self.ensure_initialized()
        self._beginSourcePipelineTransition(source)
        try:
            pipeline, generation = self._detachSourcePipeline(source)
            self._stopDetachedSourcePipeline(pipeline, generation)
        finally:
            self._endSourcePipelineTransition(source)

    def _detachSourcePipeline(
        self,
        source: PipelineSource,
    ) -> tuple[Optional[SourcePipeline], Optional[int]]:
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            attribute = self._sourcePipelineAttribute(source)
            pipeline = getattr(self, attribute, None)
            generation = self._source_pipeline_generations.pop(source, None)
            if getattr(self, attribute, None) is pipeline:
                setattr(self, attribute, None)
            return pipeline, generation

    @staticmethod
    def _stopDetachedSourcePipeline(
        pipeline: Optional[SourcePipeline],
        generation: Optional[int],
    ) -> None:
        if pipeline is not None and generation is not None:
            pipeline.stop(generation, discard_pending=True)

    def _makeTranscriberPipelineContext(
        self,
        source: PipelineSource,
        lease: Optional[WhisperRuntimeLease],
        generation: Optional[int] = None,
    ) -> TranscriberPipelineContext:
        self._ensureTranscriptionLifecycleState()
        if generation is None:
            generation = self.getSourcePipelineGeneration(source)
        if generation is None:
            generation = self.nextSourcePipelineGeneration(source)
        return TranscriberPipelineContext(
            source=source,
            whisper_runtime_lease=lease,
            whisper_decoding_profile=config.WHISPER_DECODING_PROFILE,
            generation=generation,
            is_generation_current=lambda candidate: (
                self.isSourcePipelineGenerationCurrent(source, candidate)
            ),
            emit_metric=self._recordTranscriptionPipelineMetric,
            request_recovery=self._recordTranscriptionRecoveryRequest,
        )

    def _closeWhisperRuntimeLease(
        self,
        lease: Optional[WhisperRuntimeLease],
    ) -> None:
        if lease is None:
            return
        try:
            lease.close()
        except Exception:
            errorLogging()
            try:
                self.whisper_runtime_manager.retry_failed_unload()
            except Exception:
                errorLogging()
                raise

    def ensure_initialized(self) -> None:
        """Ensure the model has been initialized. This is safe to call from
        public methods that require initialized resources.
        """
        if not getattr(self, '_inited', False):
            try:
                self.init()
            except Exception:
                # Log and continue; callers should handle missing features.
                errorLogging()

    def backwardCompatibleTranslatorCTranslate2ModelRenameWeightsDir(self):
        return backwardCompatibleRenameWeightsDir(config.PATH_DATA)
        
    def checkTranslatorCTranslate2ModelWeight(self, weight_type:str):
        return checkCTranslate2Weight(config.PATH_DATA, weight_type)

    def checkTranslatorCTranslate2ModelTokenizer(self, weight_type:str):
        return checkCTranslate2Tokenizer(config.PATH_DATA, weight_type)

    def changeTranslatorCTranslate2Model(self):
        self.ensure_initialized()
        self.translator.changeCTranslate2Model(
            path=config.PATH_DATA,
            model_type=config.CTRANSLATE2_WEIGHT_TYPE,
            device=config.SELECTED_TRANSLATION_COMPUTE_DEVICE["device"],
            device_index=config.SELECTED_TRANSLATION_COMPUTE_DEVICE["device_index"],
            compute_type=config.SELECTED_TRANSLATION_COMPUTE_TYPE
            )

    def downloadCTranslate2ModelWeight(self, weight_type, callback=None, end_callback=None):
        return downloadCTranslate2Weight(config.PATH_DATA, weight_type, callback, end_callback)

    def downloadCTranslate2ModelTokenizer(self, weight_type):
        return downloadCTranslate2Tokenizer(config.PATH_DATA, weight_type)

    def isLoadedCTranslate2Model(self):
        self.ensure_initialized()
        return self.translator.isLoadedCTranslate2Model()

    def isChangedTranslatorParameters(self):
        self.ensure_initialized()
        return self.translator.isChangedTranslatorParameters()

    def setChangedTranslatorParameters(self, is_changed):
        self.ensure_initialized()
        self.translator.setChangedTranslatorParameters(is_changed)

    def checkTranscriptionWhisperModelWeight(self, weight_type:str):
        return checkWhisperWeight(config.PATH_DATA, weight_type)

    def downloadWhisperModelWeight(self, weight_type, callback=None, end_callback=None):
        return downloadWhisperWeight(config.PATH_DATA, weight_type, callback, end_callback)

    def checkTranscriptionVoskModelWeight(self, weight_type:str):
        return checkVoskWeight(config.PATH_DATA, weight_type)

    def downloadVoskModelWeight(self, weight_type, callback=None, end_callback=None):
        return downloadVoskWeight(config.PATH_DATA, weight_type, callback, end_callback)

    def checkTranscriptionParakeetModelWeight(self, weight_type:str):
        return checkParakeetWeight(config.PATH_DATA, weight_type)

    def downloadParakeetModelWeight(self, weight_type, callback=None, end_callback=None):
        return downloadParakeetWeight(config.PATH_DATA, weight_type, callback, end_callback)

    def checkTranscriptionSenseVoiceModelWeight(self, weight_type:str):
        return checkSenseVoiceWeight(config.PATH_DATA, weight_type)

    def downloadSenseVoiceModelWeight(self, weight_type, callback=None, end_callback=None):
        return downloadSenseVoiceWeight(config.PATH_DATA, weight_type, callback, end_callback)

    def resetKeywordProcessor(self):
        self.ensure_initialized()
        del self.keyword_processor
        self.keyword_processor = KeywordProcessor()

    def authenticationTranslatorDeepLAuthKey(self, auth_key: str) -> bool:
        self.ensure_initialized()
        result = self.translator.authenticationDeepLAuthKey(auth_key)
        return result

    def authenticationTranslatorPlamoAuthKey(self, auth_key: str) -> bool:
        result = self.translator.authenticationPlamoAuthKey(auth_key, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorPlamoModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getPlamoModelList()

    def setTranslatorPlamoModel(self, model: str) -> bool:
        self.ensure_initialized()
        result = self.translator.setPlamoModel(model=model)
        return result

    def updateTranslatorPlamoClient(self) -> None:
        self.ensure_initialized()
        self.translator.updatePlamoClient()

    def authenticationTranslatorGeminiAuthKey(self, auth_key: str) -> bool:
        result = self.translator.authenticationGeminiAuthKey(auth_key, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorGeminiModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getGeminiModelList()

    def setTranslatorGeminiModel(self, model: str) -> bool:
        self.ensure_initialized()
        result = self.translator.setGeminiModel(model=model)
        return result

    def updateTranslatorGeminiClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateGeminiClient()

    def authenticationTranslatorOpenAIAuthKey(self, auth_key: str, base_url: Optional[str] = None) -> bool:
        result = self.translator.authenticationOpenAIAuthKey(auth_key, base_url=base_url, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorOpenAIModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getOpenAIModelList()

    def setTranslatorOpenAIModel(self, model: str) -> bool:
        self.ensure_initialized()
        result = self.translator.setOpenAIModel(model=model)
        return result

    def updateTranslatorOpenAIClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateOpenAIClient()

    def authenticationTranslatorGroqAuthKey(self, auth_key: str) -> bool:
        result = self.translator.authenticationGroqAuthKey(auth_key, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorGroqModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getGroqModelList()

    def setTranslatorGroqModel(self, model: str) -> bool:
        self.ensure_initialized()
        result = self.translator.setGroqModel(model=model)
        return result

    def updateTranslatorGroqClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateGroqClient()

    def authenticationTranslatorOpenRouterAuthKey(self, auth_key: str) -> bool:
        result = self.translator.authenticationOpenRouterAuthKey(auth_key, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorOpenRouterModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getOpenRouterModelList()

    def setTranslatorOpenRouterModel(self, model: str) -> bool:
        self.ensure_initialized()
        result = self.translator.setOpenRouterModel(model=model)
        return result

    def updateTranslatorOpenRouterClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateOpenRouterClient()

    def getTranslatorLMStudioConnected(self) -> bool:
        self.ensure_initialized()
        return self.translator.getLMStudioConnected()

    def authenticationTranslatorLMStudio(self, base_url: str) -> bool:
        result = self.translator.setLMStudioClientURL(base_url=base_url, root_path=config.PATH_LOCAL)
        return result

    def getTranslatorLMStudioModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getLMStudioModelList()

    def setTranslatorLMStudioModel(self, model: str) -> bool:
        self.ensure_initialized()
        return self.translator.setLMStudioModel(model=model)

    def updateTranslatorLMStudioClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateLMStudioClient()

    def getTranslatorOllamaConnected(self) -> bool:
        self.ensure_initialized()
        return self.translator.getOllamaConnected()

    def authenticationTranslatorOllama(self) -> bool:
        result = self.translator.checkOllamaClient(root_path=config.PATH_LOCAL)
        return result

    def getTranslatorOllamaModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getOllamaModelList()

    def setTranslatorOllamaModel(self, model: str) -> bool:
        self.ensure_initialized()
        return self.translator.setOllamaModel(model=model)

    def updateTranslatorOllamaClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateOllamaClient()

    def startLogger(self):
        self.ensure_initialized()
        os_makedirs(config.PATH_LOGS, exist_ok=True)
        file_name = os_path.join(config.PATH_LOGS, f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")
        self.logger = setupLogger("log", file_name)
        self.logger.disabled = False

    def stopLogger(self):
        self.ensure_initialized()
        self.logger.disabled = True
        self.logger = None

    def getListLanguageAndCountry(self):
        transcription_langs = list(transcription_lang.keys())
        translation_langs = []
        for tl_key in translation_lang.keys():
            if tl_key == "CTranslate2":
                for lang in translation_lang[tl_key][config.CTRANSLATE2_WEIGHT_TYPE]["source"]:
                    translation_langs.append(lang)
            else:
                for lang in translation_lang[tl_key]["source"]:
                    translation_langs.append(lang)
        translation_langs = list(set(translation_langs))
        supported_langs = list(filter(lambda x: x in transcription_langs, translation_langs))

        languages = []
        for language in supported_langs:
            for country in transcription_lang[language]:
                languages.append(
                    {
                        "language" : language,
                        "country" : country,
                    }
                )
        languages = sorted(languages, key=lambda x: x['language'])
        return languages

    def findTranslationEngines(self, source_lang, target_lang, engines_status):
        selectable_engines = [key for key, value in engines_status.items() if value is True]
        compatible_engines = []
        for engine in list(translation_lang.keys()):
            if engine == "CTranslate2":
                languages = translation_lang.get(engine, {}).get(config.CTRANSLATE2_WEIGHT_TYPE, {}).get("source", {})
            else:
                languages = translation_lang.get(engine, {}).get("source", {})
            source_langs = [e["language"] for e in list(source_lang.values()) if e["enable"] is True]
            target_langs = [e["language"] for e in list(target_lang.values()) if e["enable"] is True]
            language_list = list(languages.keys())

            if all(e in language_list for e in source_langs) and all(e in language_list for e in target_langs):
                if engine in selectable_engines:
                    compatible_engines.append(engine)

        return compatible_engines

    def addTranslationHistory(self, source: str, text: str) -> None:
        """Add a message to translation context history.
        
        Args:
            source: "chat" | "mic" | "speaker"
            text: message content
        """
        self.ensure_initialized()
        if not text or not text.strip():
            return
        
        history_item = {
            "source": source,
            "text": text.strip(),
            "timestamp": datetime.now().isoformat(),
        }
        self.translation_history.append(history_item)
        
        # 最大件数を超えた場合は古いものを削除
        if len(self.translation_history) > self.translation_history_max_items:
            self.translation_history = self.translation_history[-self.translation_history_max_items:]
    
    def getTranslationHistory(self, max_items: int = None) -> list[dict]:
        """Get recent translation context history.
        
        Args:
            max_items: Maximum number of items to return (newest first)
        
        Returns:
            List of history items
        """
        self.ensure_initialized()
        if max_items is None or max_items <= 0:
            return self.translation_history
        return self.translation_history[-max_items:]
    
    def clearTranslationHistory(self) -> None:
        """Clear all translation context history."""
        self.ensure_initialized()
        self.translation_history = []

    def _getSelectedTranslationEngineCandidates(self) -> tuple[str, ...]:
        selected = config.SELECTED_TRANSLATION_ENGINES.get(config.SELECTED_TAB_NO)
        return boundedTranslationProviderSnapshot(selected)

    def getTranslate(self, translator_name, source_language, target_language, target_country, message, fallback_to_ctranslate2=True):
        self.ensure_initialized()
        if source_language == target_language:
            return message, True

        success_flag = False
        
        # Get context history for LLM-based translators
        history = self.getTranslationHistory()
        
        translation = self.translator.translate(
                        translator_name=translator_name,
                        weight_type=config.CTRANSLATE2_WEIGHT_TYPE,
                        source_language=source_language,
                        target_language=target_language,
                        target_country=target_country,
                        message=message,
                        context_history=history
                )

        if isinstance(translation, str):
            success_flag = True
        return translation, success_flag

    def getTranslateWithTranslatorCandidates(self, translator_names, source_language, target_language, target_country, message):
        last_translation = False
        for provider in boundedTranslationProviderSnapshot(translator_names):
            translation, success_flag = self.getTranslate(
                provider,
                source_language,
                target_language,
                target_country,
                message,
                fallback_to_ctranslate2=False,
            )
            if success_flag is True:
                return translation, True
            last_translation = translation
        return last_translation, False

    def getInputTranslate(self, message, source_language=None):
        self.ensure_initialized()
        translator_names = self._getSelectedTranslationEngineCandidates()
        if source_language is None:
            source_language=config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"]
        target_languages=config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO]

        translations = []
        success_flags = []
        for value in target_languages.values():
            if value["enable"] is True:
                target_language = value["language"]
                target_country = value["country"]
                if target_language is not None or target_country is not None:
                    translation, success_flag = self.getTranslateWithTranslatorCandidates(
                        translator_names,
                        source_language,
                        target_language,
                        target_country,
                        message
                        )
                    translations.append(translation)
                    success_flags.append(success_flag)

        return translations, success_flags

    def getOutputTranslate(self, message, source_language=None):
        self.ensure_initialized()
        translator_names = self._getSelectedTranslationEngineCandidates()
        if source_language is None:
            source_language=config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"]
        target_language=config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO]["1"]["language"]
        target_country=config.SELECTED_YOUR_TRANSLATION_LANGUAGES[config.SELECTED_TAB_NO]["1"]["country"]

        translation, success_flag = self.getTranslateWithTranslatorCandidates(
            translator_names,
            source_language,
            target_language,
            target_country,
            message
            )
        return [translation], [success_flag]

    def addKeywords(self):
        self.ensure_initialized()
        for f in config.MIC_WORD_FILTER:
            self.keyword_processor.add_keyword(f)

    def checkKeywords(self, message):
        self.ensure_initialized()
        return len(self.keyword_processor.extract_keywords(message)) != 0

    def detectRepeatSendMessage(self, message):
        repeat_flag = False
        if self.previous_send_message == message:
            repeat_flag = True
        self.previous_send_message = message
        return repeat_flag

    def detectRepeatReceiveMessage(self, message):
        repeat_flag = False
        if self.previous_receive_message == message:
            repeat_flag = True
        self.previous_receive_message = message
        return repeat_flag

    def startTransliteration(self):
        self.ensure_initialized()
        if self.transliterator is None:
            self.transliterator = Transliterator()

    def stopTransliteration(self):
        self.ensure_initialized()
        if self.transliterator is not None:
            self.transliterator = None

    def convertMessageToTransliteration(self, message: str, hiragana: bool=True, romaji: bool=True) -> list:
        self.ensure_initialized()
        if hiragana is False and romaji is False:
            return []

        keys_to_keep = {"orig"}
        if hiragana:
            keys_to_keep.add("hira")
        if romaji:
            keys_to_keep.add("hepburn")

        if self.transliterator is None:
            self.startTransliteration()

        data_list = self.transliterator.analyze(message, use_macron=False)
        filtered_list = [
            {key: value for key, value in item.items() if key in keys_to_keep}
            for item in data_list
        ]
        return filtered_list

    def setOscIpAddress(self, ip_address):
        self.ensure_initialized()
        self.osc_handler.setOscIpAddress(ip_address)

    def setOscPort(self, port):
        self.ensure_initialized()
        self.osc_handler.setOscPort(port)

    def oscStartSendTyping(self):
        self.ensure_initialized()
        self.osc_handler.sendTyping(flag=True)

    def oscStopSendTyping(self):
        self.ensure_initialized()
        self.osc_handler.sendTyping(flag=False)

    def oscSendMessage(self, message:str):
        self.ensure_initialized()
        self.osc_handler.sendMessage(message=message, notification=config.NOTIFICATION_VRC_SFX)

    def setMuteSelfStatus(self):
        self.ensure_initialized()
        self.mic_mute_status = self.osc_handler.getOSCParameterMuteSelf()

    def startReceiveOSC(self):
        self.ensure_initialized()
        def changeHandlerMute(address, osc_arguments):
            if config.ENABLE_TRANSCRIPTION_SEND is True:
                if osc_arguments is True and self.mic_mute_status is False:
                    self.mic_mute_status = osc_arguments
                    self.changeMicTranscriptStatus()
                elif osc_arguments is False and self.mic_mute_status is True:
                    self.mic_mute_status = osc_arguments
                    self.changeMicTranscriptStatus()

        dict_filter_and_target = {
            self.osc_handler.osc_parameter_muteself: changeHandlerMute,
        }
        self.osc_handler.setDictFilterAndTarget(dict_filter_and_target)
        self.osc_handler.receiveOscParameters()

    def stopReceiveOSC(self):
        self.ensure_initialized()
        self.osc_handler.oscServerStop()

    def getIsOscQueryEnabled(self):
        self.ensure_initialized()
        return self.osc_handler.getIsOscQueryEnabled()

    @staticmethod
    def checkSoftwareUpdated():
        update_flag = False
        version = ""
        release_url = config.UPDATER_URL
        try:
            update_info_url = config.LATEST_JSON_URL or config.GITHUB_URL
            if not update_info_url:
                return {
                    "is_update_available": False,
                    "new_version": "",
                    "release_url": release_url,
                }

            response = requests_get(update_info_url, timeout=5)
            response.raise_for_status()
            json_data = response.json()
            version = json_data.get("version", "")
            if isinstance(version, str):
                version = version.strip().lstrip("v")
                new_version = parse(version)
                current_version = parse(config.VERSION)
                if new_version > current_version:
                    update_flag = True
        except Exception:
            errorLogging()
        return {
            "is_update_available": update_flag,
            "new_version": version,
            "release_url": release_url,
        }

    @staticmethod
    def updateSoftware():
        if config.UPDATER_URL:
            Popen(["cmd", "/c", "start", "", config.UPDATER_URL], shell=False)

    def getListMicHost(self):
        self.ensure_initialized()
        try:
            dm = device_manager.getMicDevices()
            result = [host for host in dm.keys()]
        except Exception:
            errorLogging()
            result = []
        return result

    def getMicDefaultDevice(self):
        self.ensure_initialized()
        try:
            dm = device_manager.getMicDevices()
            result = dm.get(config.SELECTED_MIC_HOST, [{"name": "NoDevice"}])[0]["name"]
        except Exception:
            errorLogging()
            result = "NoDevice"
        return result

    def getListMicDevice(self):
        self.ensure_initialized()
        try:
            dm = device_manager.getMicDevices()
            result = [device["name"] for device in dm.get(config.SELECTED_MIC_HOST, [{"name": "NoDevice"}])]
        except Exception:
            errorLogging()
            result = ["NoDevice"]
        return result

    def getListSpeakerDevice(self):
        self.ensure_initialized()
        try:
            sd = device_manager.getSpeakerDevices()
            result = [device["name"] for device in sd]
        except Exception:
            errorLogging()
            result = ["NoDevice"]
        return result

    def _recordCaptureHeartbeat(
        self,
        source: PipelineSource,
        generation: int,
        captured_at: float,
    ) -> None:
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            session = self._source_transcription_sessions.get(source)
            if (
                session is not None
                and session.get("generation") == generation
                and not session["stop_event"].is_set()
                and self._source_pipeline_generations.get(source) == generation
                and getattr(
                    self,
                    self._sourcePipelineAttribute(source),
                    None,
                )
                is not None
            ):
                self._source_heartbeat_timestamps[source] = captured_at
                session["heartbeat_at"] = captured_at

    def _recorderCallbacks(
        self,
        source: PipelineSource,
        generation: int,
        audio_queue: _MetricAudioQueue,
    ) -> dict[str, Callable]:
        return {
            "on_drop": lambda _chunk: audio_queue.record_drop(),
            "on_heartbeat": lambda captured_at: self._recordCaptureHeartbeat(
                source,
                generation,
                captured_at,
            ),
        }

    def _recordIntoTranscriptionQueue(
        self,
        recorder,
        source: PipelineSource,
        generation: int,
        audio_queue: _MetricAudioQueue,
    ) -> None:
        callbacks = self._recorderCallbacks(source, generation, audio_queue)
        try:
            recorder.recordIntoQueue(audio_queue, None, **callbacks)
        except TypeError as error:
            # Focused compatibility adapters may implement the historical
            # two-argument recorder seam. Runtime recorders accept callbacks.
            if "unexpected keyword argument" not in str(error):
                raise
            recorder.recordIntoQueue(audio_queue, None)

    def restartRecorder(
        self,
        source: PipelineSource,
        generation: int,
    ) -> bool:
        """Replace only capture for a current session; keep its worker/lease."""
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        with self._source_session_lock:
            session = self._source_transcription_sessions.get(source)
            if (
                session is None
                or session["generation"] != generation
                or session["stop_event"].is_set()
                or not self.isSourcePipelineGenerationCurrent(source, generation)
                or session.get("recorder_restarting") is True
            ):
                return False
            session["recorder_restarting"] = True
            old_recorder = session["recorder"]
            recorder_factory = session["recorder_factory"]
            audio_queue = session["audio_queue"]

        self._requestRecorderStop(old_recorder, resume_first=True)
        try:
            new_recorder = recorder_factory()
            self._recordIntoTranscriptionQueue(
                new_recorder,
                source,
                generation,
                audio_queue,
            )
        except Exception:
            with self._source_session_lock:
                session = self._source_transcription_sessions.get(source)
                if session is not None and session["generation"] == generation:
                    session["recorder_restarting"] = False
            self._emitTranscriptionLifecycleMetric(
                source,
                stage="capture",
                outcome="error",
                error_code="recorder_restart_failed",
            )
            errorLogging()
            return False

        with self._source_session_lock:
            session = self._source_transcription_sessions.get(source)
            if (
                session is None
                or session["generation"] != generation
                or session["stop_event"].is_set()
                or not self.isSourcePipelineGenerationCurrent(source, generation)
            ):
                if session is not None and session["generation"] == generation:
                    session["recorder_restarting"] = False
                stale = True
            else:
                stale = False
                session["recorder_restarting"] = False
                session["recorder"] = new_recorder
                session["heartbeat_at"] = monotonic()
                self._source_heartbeat_timestamps[source] = session["heartbeat_at"]
                if source is PipelineSource.MIC:
                    self.mic_audio_recorder = new_recorder
                else:
                    self.speaker_audio_recorder = new_recorder
                transcriber = session.get("transcriber")

        if stale:
            self._requestRecorderStop(new_recorder, resume_first=True)
            return False
        if isinstance(transcriber, AudioTranscriber):
            try:
                transcriber.resetAudioSource(new_recorder.source)
            except Exception:
                self._emitTranscriptionLifecycleMetric(
                    source,
                    stage="capture",
                    outcome="error",
                    error_code="recorder_source_reset_failed",
                )
                errorLogging()

        self._emitTranscriptionLifecycleMetric(
            source,
            stage="capture",
            outcome="recovered",
        )
        return True

    def _startCaptureHeartbeatWatchdog(
        self,
        source: PipelineSource,
        generation: int,
        stop_event: Event,
        stall_seconds: float,
    ) -> None:
        def watchCaptureHeartbeat():
            last_recovery_heartbeat = None
            while not stop_event.wait(TRANSCRIPT_STALL_CHECK_SECONDS):
                with self._source_session_lock:
                    session = self._source_transcription_sessions.get(source)
                    if (
                        session is None
                        or session.get("generation") != generation
                        or session.get("stop_event") is not stop_event
                        or self._source_pipeline_generations.get(source)
                        != generation
                        or getattr(
                            self,
                            self._sourcePipelineAttribute(source),
                            None,
                        )
                        is None
                    ):
                        return
                    heartbeat_at = self._source_heartbeat_timestamps.get(source)
                if heartbeat_at is None or monotonic() - heartbeat_at <= stall_seconds:
                    continue
                if heartbeat_at == last_recovery_heartbeat:
                    continue
                try:
                    recovered = self.restartRecorder(source, generation)
                except Exception:
                    errorLogging()
                    recovered = False
                if recovered is True:
                    last_recovery_heartbeat = heartbeat_at

        watchdog_thread = Thread(
            target=watchCaptureHeartbeat,
            name=f"{source.value}-capture-watchdog-{generation}",
            daemon=True,
        )
        watchdog_thread.start()

    def startMicTranscript(self, fnc, generation: Optional[int] = None) -> bool:
        self.ensure_initialized()
        if (
            isinstance(self.mic_print_transcript, threadFnc)
            or isinstance(
                self.mic_audio_recorder,
                SelectedMicEnergyAndAudioRecorder,
            )
            or self.mic_whisper_runtime_lease is not None
        ):
            self.stopMicTranscript(stop_pipeline=False)
        try:
            return self._startMicTranscript(fnc, generation=generation)
        except Exception:
            try:
                self.stopMicTranscript(stop_pipeline=False)
            except Exception:
                errorLogging()
            raise

    def validateMicTranscriptDevice(self) -> dict:
        """Return the selected mic or fail before source workers are created."""
        self.ensure_initialized()
        mic_host_name = config.SELECTED_MIC_HOST
        mic_device_name = config.SELECTED_MIC_DEVICE
        mic_device_list = device_manager.getMicDevices().get(
            mic_host_name,
            [{"name": "NoDevice"}],
        )
        selected_mic_device = [
            device
            for device in mic_device_list
            if device["name"] == mic_device_name
        ]
        if len(selected_mic_device) == 0 or mic_device_name == "NoDevice":
            raise DeviceUnavailableError(ErrorCode.DEVICE_NO_MIC)
        return selected_mic_device[0]

    def _startMicTranscript(self, fnc, generation: Optional[int] = None) -> bool:
        self.ensure_initialized()
        if config.ENABLE_TRANSCRIPTION_SEND is False:
            return False
        if generation is None:
            generation = self.getSourcePipelineGeneration(PipelineSource.MIC)
        if generation is None:
            generation = self.nextSourcePipelineGeneration(PipelineSource.MIC)
        mic_device = self.validateMicTranscriptDevice()
        if mic_device is not None:
            self.mic_audio_queue = _MetricAudioQueue(
                PipelineSource.MIC,
                self._emitTranscriptionLifecycleMetric,
            )
            # self.mic_energy_queue = Queue()

            record_timeout = config.MIC_RECORD_TIMEOUT
            phrase_timeout = config.MIC_PHRASE_TIMEOUT
            if record_timeout > phrase_timeout:
                record_timeout = phrase_timeout

            def recorder_factory():
                return SelectedMicEnergyAndAudioRecorder(
                    device=mic_device,
                    energy_threshold=config.MIC_THRESHOLD,
                    dynamic_energy_threshold=config.MIC_AUTOMATIC_THRESHOLD,
                    phrase_time_limit=record_timeout,
                    phrase_timeout=phrase_timeout,
                    record_timeout=record_timeout,
                )

            try:
                self.mic_audio_recorder = recorder_factory()
            except Exception:
                self._emitTranscriptionLifecycleMetric(
                    PipelineSource.MIC,
                    stage="capture",
                    outcome="error",
                    error_code="recorder_construction_failed",
                )
                raise
            # self.mic_audio_recorder.recordIntoQueue(self.mic_audio_queue, mic_energy_queue)
            self._recordIntoTranscriptionQueue(
                self.mic_audio_recorder,
                PipelineSource.MIC,
                generation,
                self.mic_audio_queue,
            )
            self._emitTranscriptionLifecycleMetric(
                PipelineSource.MIC,
                stage="capture",
                outcome="running",
            )
            whisper_runtime_lease = None
            try:
                whisper_runtime_lease = self._acquireWhisperRuntimeLease()
                self.mic_whisper_runtime_lease = whisper_runtime_lease
                self.mic_transcriber = AudioTranscriber(
                    speaker=False,
                    source=self.mic_audio_recorder.source,
                    phrase_timeout=phrase_timeout,
                    max_phrases=config.MIC_MAX_PHRASES,
                    transcription_engine=config.SELECTED_TRANSCRIPTION_ENGINE,
                    root=config.PATH_DATA,
                    whisper_weight_type=config.WHISPER_WEIGHT_TYPE,
                    vosk_weight_type=config.VOSK_WEIGHT_TYPE,
                    parakeet_weight_type=config.PARAKEET_WEIGHT_TYPE,
                    sensevoice_weight_type=config.SENSEVOICE_WEIGHT_TYPE,
                    device=config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device"],
                    device_index=config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device_index"],
                    compute_type=config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
                    pipeline_context=self._makeTranscriberPipelineContext(
                        PipelineSource.MIC,
                        whisper_runtime_lease,
                        generation,
                    ),
                )
            except Exception:
                raise

            if config.ENABLE_TRANSCRIPTION_SEND is False:
                self.stopMicTranscript(stop_pipeline=False)
                return False

            audio_queue = self.mic_audio_queue
            transcriber = self.mic_transcriber
            stop_event = Event()
            self.mic_transcript_stop_event = stop_event
            heartbeat_at = monotonic()
            with self._source_session_lock:
                self._source_heartbeat_timestamps[PipelineSource.MIC] = heartbeat_at
                self._source_transcription_sessions[PipelineSource.MIC] = {
                    "generation": generation,
                    "callback": fnc,
                    "audio_queue": audio_queue,
                    "recorder": self.mic_audio_recorder,
                    "recorder_factory": recorder_factory,
                    "transcriber": transcriber,
                    "worker": None,
                    "lease": whisper_runtime_lease,
                    "stop_event": stop_event,
                    "heartbeat_at": heartbeat_at,
                }
            stall_seconds = max(
                TRANSCRIPT_STALL_RESTART_SECONDS,
                float(record_timeout) * 4.0,
                float(phrase_timeout) * 4.0,
            )

            def sendMicTranscript():
                if stop_event.is_set():
                    return
                try:
                    selected_your_languages = config.SELECTED_YOUR_LANGUAGES[config.SELECTED_TAB_NO]
                    languages = [data["language"] for data in selected_your_languages.values() if data["enable"] is True]
                    countries = [data["country"] for data in selected_your_languages.values() if data["enable"] is True]
                    if isinstance(transcriber, AudioTranscriber) is True:
                        res = transcriber.transcribeAudioQueue(
                            audio_queue,
                            languages,
                            countries,
                            config.MIC_AVG_LOGPROB,
                            config.MIC_NO_SPEECH_PROB,
                            config.MIC_NO_REPEAT_NGRAM_SIZE,
                            config.MIC_VAD_FILTER,
                            config.MIC_VAD_PARAMETERS,
                        )
                        if (
                            res
                            and not stop_event.is_set()
                            and self.isSourcePipelineGenerationCurrent(
                                PipelineSource.MIC,
                                generation,
                            )
                        ):
                            result = transcriber.getTranscript()
                            fnc(result)
                except Exception:
                    errorLogging()

            def endMicTranscript():
                stop_event.set()
                audio_queue.drain()
                # while not self.mic_energy_queue.empty():
                #     self.mic_energy_queue.get()
                if self.mic_audio_queue is audio_queue:
                    self.mic_audio_queue = None
                if self.mic_transcriber is transcriber:
                    self.mic_transcriber = None
                if self.mic_transcript_stop_event is stop_event:
                    self.mic_transcript_stop_event = None
                gc.collect()

            # def sendMicEnergy():
            #     if mic_energy_queue.empty() is False:
            #         energy = mic_energy_queue.get()
            #         # print("mic energy:", energy)
            #         try:
            #             fnc(energy)
            #         except Exception:
            #             pass
            #     sleep(0.01)

            self.mic_print_transcript = threadFnc(sendMicTranscript, end_fnc=endMicTranscript)
            self.mic_print_transcript.daemon = True
            self.mic_print_transcript.start()
            with self._source_session_lock:
                session = self._source_transcription_sessions.get(PipelineSource.MIC)
                if session is not None and session["generation"] == generation:
                    session["worker"] = self.mic_print_transcript

            self._startTranscriptStallWatchdog(
                "Mic",
                stop_event,
                {
                    "source": PipelineSource.MIC,
                    "generation": generation,
                },
                stall_seconds,
                lambda: self.restartRecorder(PipelineSource.MIC, generation),
            )

            # self.mic_get_energy = threadFnc(sendMicEnergy)
            # self.mic_get_energy.daemon = True
            # self.mic_get_energy.start()

            self.changeMicTranscriptStatus()
            return True

    def resumeMicTranscript(self):
        self.ensure_initialized()
        # キューをクリア
        if hasattr(self.mic_audio_queue, "drain"):
            self.mic_audio_queue.drain()
        elif isinstance(self.mic_audio_queue, Queue):
            while not self.mic_audio_queue.empty():
                self.mic_audio_queue.get()

        # 文字起こしを再開
        # if isinstance(self.mic_print_transcript, threadFnc):
        #     self.mic_print_transcript.resume()

        # 音声のレコードを再開
        if isinstance(self.mic_audio_recorder, SelectedMicEnergyAndAudioRecorder):
            self.mic_audio_recorder.resume()

    def pauseMicTranscript(self):
        self.ensure_initialized()
        # 文字起こしを一時停止
        # if isinstance(self.mic_print_transcript, threadFnc):
        #     self.mic_print_transcript.pause()

        # 音声のレコードを一時停止
        if isinstance(self.mic_audio_recorder, SelectedMicEnergyAndAudioRecorder):
            self.mic_audio_recorder.pause()

    # VRAM 不足エラーを検出するメソッドを追加
    def detectVRAMError(self, error):
        error_str = str(error)
        if isinstance(error, ValueError) and len(error.args) > 0 and error.args[0] == "VRAM_OUT_OF_MEMORY":
            return True, error.args[1] if len(error.args) > 1 else "VRAM out of memory"
        if "CUDA out of memory" in error_str or "CUBLAS_STATUS_ALLOC_FAILED" in error_str:
            return True, error_str
        return False, None

    @staticmethod
    def _requestRecorderStop(recorder, resume_first: bool = False) -> None:
        if recorder is None:
            return
        try:
            if resume_first:
                resume = getattr(recorder, "resume", None)
                if callable(resume):
                    resume()

            stop = getattr(recorder, "stop", None)
            if callable(stop):
                try:
                    stop(False)
                except TypeError:
                    stop()
        except Exception:
            errorLogging()

    @staticmethod
    def _requestTranscriptThreadStop(thread) -> bool:
        if not isinstance(thread, threadFnc):
            return True
        try:
            thread.stop()
            if thread is current_thread():
                return False
            # A provider call already in progress cannot be cancelled safely.
            # Google recognition has a bounded operation timeout; other
            # third-party providers must cooperate or shutdown waits here.
            thread.join(timeout=TRANSCRIPT_THREAD_JOIN_TIMEOUT)
            if thread.is_alive():
                thread.join()
            return not thread.is_alive()
        except Exception:
            errorLogging()
            return False

    def _startTranscriptStallWatchdog(
        self,
        label: str,
        stop_event: Event,
        activity_state: dict,
        stall_seconds: float,
        restart_callback: Callable[[], None],
    ) -> None:
        source = activity_state.get("source")
        generation = activity_state.get("generation")
        if isinstance(source, PipelineSource) and isinstance(generation, int):
            self._startCaptureHeartbeatWatchdog(
                source,
                generation,
                stop_event,
                stall_seconds,
            )
            return

        def watchTranscriptStall():
            while stop_event.wait(TRANSCRIPT_STALL_CHECK_SECONDS) is False:
                busy_since = activity_state.get("busy_since")
                if busy_since is None:
                    continue
                if monotonic() - busy_since <= stall_seconds:
                    continue
                try:
                    printLog(
                        f"{label} transcription stalled; restarting",
                        {"stall_seconds": round(monotonic() - busy_since, 2)},
                    )
                except Exception:
                    errorLogging()
                stop_event.set()
                try:
                    restart_callback()
                except Exception:
                    errorLogging()
                break

        watchdog_thread = Thread(target=watchTranscriptStall)
        watchdog_thread.daemon = True
        watchdog_thread.start()

    def changeMicTranscriptStatus(self):
        if config.VRC_MIC_MUTE_SYNC is True:
            match self.mic_mute_status:
                case True:
                    self.pauseMicTranscript()
                case False:
                    self.resumeMicTranscript()
                case None:
                    # mute selfの状態が不明な場合は一時停止しない
                    self.resumeMicTranscript()
                case _:
                    pass
        else:
            self.resumeMicTranscript()

    def stopMicTranscript(self, stop_pipeline: bool = True):
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        detached_pipeline = (None, None)
        transition_started = False
        if stop_pipeline:
            self._beginSourcePipelineTransition(PipelineSource.MIC)
            transition_started = True
            try:
                # Invalidate output admission atomically; worker joins still
                # follow recorder/queue order below.
                detached_pipeline = self._detachSourcePipeline(
                    PipelineSource.MIC
                )
            except Exception:
                self._endSourcePipelineTransition(PipelineSource.MIC)
                raise

        stop_event = self.mic_transcript_stop_event
        if hasattr(stop_event, "set"):
            stop_event.set()
        try:
            recorder = self.mic_audio_recorder
            self.mic_audio_recorder = None
            self._requestRecorderStop(recorder, resume_first=True)

            audio_queue = self.mic_audio_queue
            if hasattr(audio_queue, "close"):
                audio_queue.close()

            thread = self.mic_print_transcript
            self._requestTranscriptThreadStop(thread)
            if self.mic_print_transcript is thread:
                self.mic_print_transcript = None
        finally:
            if transition_started:
                try:
                    self._stopDetachedSourcePipeline(*detached_pipeline)
                finally:
                    self._endSourcePipelineTransition(PipelineSource.MIC)

        whisper_runtime_lease = self.mic_whisper_runtime_lease
        close_error = None
        try:
            self._closeWhisperRuntimeLease(whisper_runtime_lease)
        except Exception as error:
            close_error = error
        else:
            if self.mic_whisper_runtime_lease is whisper_runtime_lease:
                self.mic_whisper_runtime_lease = None
        finally:
            with self._source_session_lock:
                session = self._source_transcription_sessions.get(
                    PipelineSource.MIC
                )
                if session is not None and session.get("worker") is thread:
                    self._source_transcription_sessions.pop(
                        PipelineSource.MIC,
                        None,
                    )
                    self._source_heartbeat_timestamps.pop(
                        PipelineSource.MIC,
                        None,
                    )
            self.mic_transcriber = None
            self.mic_audio_queue = None
            self.mic_transcript_stop_event = None
        if close_error is not None:
            raise close_error
        # if isinstance(self.mic_get_energy, threadFnc):
        #     self.mic_get_energy.stop()
        #     self.mic_get_energy = None

    def startCheckMicEnergy(self, fnc:Optional[Callable[[float], None]]=None) -> None:
        self.ensure_initialized()
        # fnc may be None or a callable. Use cast after checking for None to satisfy type checker.
        if fnc is not None:
            self.check_mic_energy_fnc = cast(Callable[[float], None], fnc)

        mic_host_name = config.SELECTED_MIC_HOST
        mic_device_name = config.SELECTED_MIC_DEVICE

        mic_device_list = device_manager.getMicDevices().get(mic_host_name, [{"name": "NoDevice"}])
        selected_mic_device = [device for device in mic_device_list if device["name"] == mic_device_name]

        if len(selected_mic_device) == 0 or mic_device_name == "NoDevice":
            self.check_mic_energy_fnc(False)
        else:
            def sendMicEnergy():
                if mic_energy_queue.empty() is False:
                    energy = mic_energy_queue.get()
                    try:
                        self.check_mic_energy_fnc(energy)
                    except Exception:
                        errorLogging()
                sleep(0.01)

            mic_energy_queue: Queue = Queue()
            mic_device = selected_mic_device[0]
            try:
                self.mic_energy_recorder = SelectedMicEnergyRecorder(mic_device)
                self.mic_energy_recorder.recordIntoQueue(mic_energy_queue)
                self.mic_energy_plot_progressbar = threadFnc(sendMicEnergy)
                self.mic_energy_plot_progressbar.daemon = True
                self.mic_energy_plot_progressbar.start()
            except Exception:
                try:
                    self.stopCheckMicEnergy()
                except Exception:
                    errorLogging()
                raise

    def stopCheckMicEnergy(self):
        self.ensure_initialized()
        self._stopEnergyCheckResources(
            "mic_energy_plot_progressbar",
            "mic_energy_recorder",
            SelectedMicEnergyRecorder,
        )

    def startSpeakerTranscript(
        self,
        fnc: Optional[Callable[[dict], None]] = None,
        generation: Optional[int] = None,
    ) -> bool:
        self.ensure_initialized()
        if (
            isinstance(self.speaker_print_transcript, threadFnc)
            or isinstance(
                self.speaker_audio_recorder,
                SelectedSpeakerEnergyAndAudioRecorder,
            )
            or self.speaker_whisper_runtime_lease is not None
        ):
            self.stopSpeakerTranscript(stop_pipeline=False)
        try:
            return self._startSpeakerTranscript(fnc, generation=generation)
        except Exception:
            try:
                self.stopSpeakerTranscript(stop_pipeline=False)
            except Exception:
                errorLogging()
            raise

    def validateSpeakerTranscriptDevice(self) -> dict:
        """Return the selected speaker or fail before source workers start."""
        self.ensure_initialized()
        speaker_device_name = config.SELECTED_SPEAKER_DEVICE
        speaker_device_list = device_manager.getSpeakerDevices()
        selected_speaker_device = [
            device
            for device in speaker_device_list
            if device["name"] == speaker_device_name
        ]
        if (
            len(selected_speaker_device) == 0
            or speaker_device_name == "NoDevice"
        ):
            raise DeviceUnavailableError(ErrorCode.DEVICE_NO_SPEAKER)
        return selected_speaker_device[0]

    def _startSpeakerTranscript(
        self,
        fnc: Optional[Callable[[dict], None]] = None,
        generation: Optional[int] = None,
    ) -> bool:
        self.ensure_initialized()
        if config.ENABLE_TRANSCRIPTION_RECEIVE is False:
            return False
        if generation is None:
            generation = self.getSourcePipelineGeneration(PipelineSource.SPEAKER)
        if generation is None:
            generation = self.nextSourcePipelineGeneration(PipelineSource.SPEAKER)
        speaker_device = self.validateSpeakerTranscriptDevice()
        if speaker_device is not None:
            speaker_audio_queue = _MetricAudioQueue(
                PipelineSource.SPEAKER,
                self._emitTranscriptionLifecycleMetric,
            )
            self.speaker_audio_queue = speaker_audio_queue
            record_timeout = config.SPEAKER_RECORD_TIMEOUT
            phrase_timeout = config.SPEAKER_PHRASE_TIMEOUT
            if record_timeout > phrase_timeout:
                record_timeout = phrase_timeout

            def recorder_factory():
                return SelectedSpeakerEnergyAndAudioRecorder(
                    device=speaker_device,
                    energy_threshold=config.SPEAKER_THRESHOLD,
                    dynamic_energy_threshold=config.SPEAKER_AUTOMATIC_THRESHOLD,
                    phrase_time_limit=record_timeout,
                    phrase_timeout=phrase_timeout,
                    record_timeout=record_timeout,
                )

            try:
                self.speaker_audio_recorder = recorder_factory()
            except Exception:
                self._emitTranscriptionLifecycleMetric(
                    PipelineSource.SPEAKER,
                    stage="capture",
                    outcome="error",
                    error_code="recorder_construction_failed",
                )
                raise
            # self.speaker_audio_recorder.recordIntoQueue(speaker_audio_queue, speaker_energy_queue)
            self._recordIntoTranscriptionQueue(
                self.speaker_audio_recorder,
                PipelineSource.SPEAKER,
                generation,
                speaker_audio_queue,
            )
            self._emitTranscriptionLifecycleMetric(
                PipelineSource.SPEAKER,
                stage="capture",
                outcome="running",
            )
            whisper_runtime_lease = None
            try:
                whisper_runtime_lease = self._acquireWhisperRuntimeLease()
                self.speaker_whisper_runtime_lease = whisper_runtime_lease
                self.speaker_transcriber = AudioTranscriber(
                    speaker=True,
                    source=self.speaker_audio_recorder.source,
                    phrase_timeout=phrase_timeout,
                    max_phrases=config.SPEAKER_MAX_PHRASES,
                    transcription_engine=config.SELECTED_TRANSCRIPTION_ENGINE,
                    root=config.PATH_DATA,
                    whisper_weight_type=config.WHISPER_WEIGHT_TYPE,
                    vosk_weight_type=config.VOSK_WEIGHT_TYPE,
                    parakeet_weight_type=config.PARAKEET_WEIGHT_TYPE,
                    sensevoice_weight_type=config.SENSEVOICE_WEIGHT_TYPE,
                    device=config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device"],
                    device_index=config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE["device_index"],
                    compute_type=config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE,
                    pipeline_context=self._makeTranscriberPipelineContext(
                        PipelineSource.SPEAKER,
                        whisper_runtime_lease,
                        generation,
                    ),
                )
            except Exception:
                raise

            if config.ENABLE_TRANSCRIPTION_RECEIVE is False:
                self.stopSpeakerTranscript(stop_pipeline=False)
                return False

            transcriber = self.speaker_transcriber
            stop_event = Event()
            self.speaker_transcript_stop_event = stop_event
            heartbeat_at = monotonic()
            with self._source_session_lock:
                self._source_heartbeat_timestamps[PipelineSource.SPEAKER] = heartbeat_at
                self._source_transcription_sessions[PipelineSource.SPEAKER] = {
                    "generation": generation,
                    "callback": fnc,
                    "audio_queue": speaker_audio_queue,
                    "recorder": self.speaker_audio_recorder,
                    "recorder_factory": recorder_factory,
                    "transcriber": transcriber,
                    "worker": None,
                    "lease": whisper_runtime_lease,
                    "stop_event": stop_event,
                    "heartbeat_at": heartbeat_at,
                }
            stall_seconds = max(
                TRANSCRIPT_STALL_RESTART_SECONDS,
                float(record_timeout) * 4.0,
                float(phrase_timeout) * 4.0,
            )

            def sendSpeakerTranscript():
                if stop_event.is_set():
                    return
                try:
                    selected_target_languages = config.SELECTED_TARGET_LANGUAGES[config.SELECTED_TAB_NO]
                    languages = [data["language"] for data in selected_target_languages.values() if data["enable"] is True]
                    countries = [data["country"] for data in selected_target_languages.values() if data["enable"] is True]
                    if isinstance(transcriber, AudioTranscriber) is True:
                        res = transcriber.transcribeAudioQueue(
                            speaker_audio_queue,
                            languages,
                            countries,
                            config.SPEAKER_AVG_LOGPROB,
                            config.SPEAKER_NO_SPEECH_PROB,
                            config.SPEAKER_NO_REPEAT_NGRAM_SIZE,
                            config.SPEAKER_VAD_FILTER,
                            config.SPEAKER_VAD_PARAMETERS,
                        )
                        if (
                            res
                            and not stop_event.is_set()
                            and self.isSourcePipelineGenerationCurrent(
                                PipelineSource.SPEAKER,
                                generation,
                            )
                        ):
                            result = transcriber.getTranscript()
                            if callable(fnc):
                                fnc(result)
                except Exception:
                    errorLogging()

            def endSpeakerTranscript():
                stop_event.set()
                speaker_audio_queue.drain()
                # while not speaker_energy_queue.empty():
                #     speaker_energy_queue.get()
                if self.speaker_audio_queue is speaker_audio_queue:
                    self.speaker_audio_queue = None
                if self.speaker_transcriber is transcriber:
                    self.speaker_transcriber = None
                if self.speaker_transcript_stop_event is stop_event:
                    self.speaker_transcript_stop_event = None
                gc.collect()

            # def sendSpeakerEnergy():
            #     if speaker_energy_queue.empty() is False:
            #         energy = speaker_energy_queue.get()
            #         # print("speaker energy:", energy)
            #         try:
            #             fnc(energy)
            #         except Exception:
            #             pass
            #     sleep(0.01)

            self.speaker_print_transcript = threadFnc(sendSpeakerTranscript, end_fnc=endSpeakerTranscript)
            self.speaker_print_transcript.daemon = True
            self.speaker_print_transcript.start()
            with self._source_session_lock:
                session = self._source_transcription_sessions.get(
                    PipelineSource.SPEAKER
                )
                if session is not None and session["generation"] == generation:
                    session["worker"] = self.speaker_print_transcript

            self._startTranscriptStallWatchdog(
                "Speaker",
                stop_event,
                {
                    "source": PipelineSource.SPEAKER,
                    "generation": generation,
                },
                stall_seconds,
                lambda: self.restartRecorder(
                    PipelineSource.SPEAKER,
                    generation,
                ),
            )

            # self.speaker_get_energy = threadFnc(sendSpeakerEnergy)
            # self.speaker_get_energy.daemon = True
            # self.speaker_get_energy.start()
            return True

    def stopSpeakerTranscript(self, stop_pipeline: bool = True):
        self.ensure_initialized()
        self._ensureTranscriptionLifecycleState()
        detached_pipeline = (None, None)
        transition_started = False
        if stop_pipeline:
            self._beginSourcePipelineTransition(PipelineSource.SPEAKER)
            transition_started = True
            try:
                detached_pipeline = self._detachSourcePipeline(
                    PipelineSource.SPEAKER
                )
            except Exception:
                self._endSourcePipelineTransition(PipelineSource.SPEAKER)
                raise

        stop_event = self.speaker_transcript_stop_event
        if hasattr(stop_event, "set"):
            stop_event.set()
        try:
            recorder = self.speaker_audio_recorder
            self.speaker_audio_recorder = None
            self._requestRecorderStop(recorder, resume_first=True)

            audio_queue = self.speaker_audio_queue
            if hasattr(audio_queue, "close"):
                audio_queue.close()

            thread = self.speaker_print_transcript
            self._requestTranscriptThreadStop(thread)
            if self.speaker_print_transcript is thread:
                self.speaker_print_transcript = None
        finally:
            if transition_started:
                try:
                    self._stopDetachedSourcePipeline(*detached_pipeline)
                finally:
                    self._endSourcePipelineTransition(PipelineSource.SPEAKER)

        whisper_runtime_lease = self.speaker_whisper_runtime_lease
        close_error = None
        try:
            self._closeWhisperRuntimeLease(whisper_runtime_lease)
        except Exception as error:
            close_error = error
        else:
            if self.speaker_whisper_runtime_lease is whisper_runtime_lease:
                self.speaker_whisper_runtime_lease = None
        finally:
            with self._source_session_lock:
                session = self._source_transcription_sessions.get(
                    PipelineSource.SPEAKER
                )
                if session is not None and session.get("worker") is thread:
                    self._source_transcription_sessions.pop(
                        PipelineSource.SPEAKER,
                        None,
                    )
                    self._source_heartbeat_timestamps.pop(
                        PipelineSource.SPEAKER,
                        None,
                    )
            self.speaker_transcriber = None
            self.speaker_audio_queue = None
            self.speaker_transcript_stop_event = None
        if close_error is not None:
            raise close_error
        # if isinstance(self.speaker_get_energy, threadFnc):
        #     self.speaker_get_energy.stop()
        #     self.speaker_get_energy = None

    def startCheckSpeakerEnergy(self, fnc:Optional[Callable[[float], None]]=None) -> None:
        self.ensure_initialized()
        # Accept None as default and assign safely with cast after None-check
        if fnc is not None:
            self.check_speaker_energy_fnc = cast(Callable[[float], None], fnc)

        speaker_device_name = config.SELECTED_SPEAKER_DEVICE
        speaker_device_list = device_manager.getSpeakerDevices()
        selected_speaker_device = [device for device in speaker_device_list if device["name"] == speaker_device_name]

        if len(selected_speaker_device) == 0 or speaker_device_name == "NoDevice":
            self.check_speaker_energy_fnc(False)
        else:
            def sendSpeakerEnergy():
                if not speaker_energy_queue.empty():
                    energy = speaker_energy_queue.get()
                    try:
                        self.check_speaker_energy_fnc(energy)
                    except Exception:
                        errorLogging()
                sleep(0.01)

            speaker_energy_queue: Queue = Queue()
            speaker_device = selected_speaker_device[0]
            try:
                self.speaker_energy_recorder = SelectedSpeakerEnergyRecorder(speaker_device)
                self.speaker_energy_recorder.recordIntoQueue(speaker_energy_queue)
                self.speaker_energy_plot_progressbar = threadFnc(sendSpeakerEnergy)
                self.speaker_energy_plot_progressbar.daemon = True
                self.speaker_energy_plot_progressbar.start()
            except Exception:
                try:
                    self.stopCheckSpeakerEnergy()
                except Exception:
                    errorLogging()
                raise

    def stopCheckSpeakerEnergy(self):
        self.ensure_initialized()
        self._stopEnergyCheckResources(
            "speaker_energy_plot_progressbar",
            "speaker_energy_recorder",
            SelectedSpeakerEnergyRecorder,
        )

    def _stopEnergyCheckResources(
        self,
        thread_attribute: str,
        recorder_attribute: str,
        recorder_type,
    ) -> None:
        """Detach and stop one energy monitor, preserving the first error."""
        progress_thread = getattr(self, thread_attribute, None)
        recorder = getattr(self, recorder_attribute, None)
        setattr(self, thread_attribute, None)
        setattr(self, recorder_attribute, None)

        first_error = None
        if isinstance(progress_thread, threadFnc):
            try:
                progress_thread.stop()
            except Exception as error:
                first_error = error
            try:
                progress_thread.join()
            except Exception as error:
                if first_error is None:
                    first_error = error

        if isinstance(recorder, recorder_type):
            try:
                recorder.resume()
            except Exception as error:
                if first_error is None:
                    first_error = error
            try:
                recorder.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error

        if first_error is not None:
            raise first_error

    @staticmethod
    def _overlayTargetLanguageList(
        target_language: Optional[dict],
        translation_target_slots: Optional[list[str]] = None,
    ) -> list:
        if not isinstance(target_language, dict):
            return []
        if translation_target_slots is not None:
            target_items = (
                target_language.get(str(slot)) for slot in translation_target_slots
            )
        else:
            target_items = target_language.values()
        return [
            data.get("language")
            for data in target_items
            if isinstance(data, dict) and data.get("enable") is True
        ]

    def createOverlayImageSmallLog(
        self,
        message: Optional[str],
        your_language: Optional[str],
        translation: list,
        target_language: Optional[dict],
        transliteration_message: Optional[dict] = None,
        transliteration_translation: Optional[list] = None,
        translation_target_slots: Optional[list[str]] = None,
    ) -> object:
        self.ensure_initialized()
        target_language_list = self._overlayTargetLanguageList(
            target_language,
            translation_target_slots,
        )

        # 翻訳行ルビ (任意) が指定されていれば渡す。後方互換のため None / 不正型は空リストに。
        if not isinstance(transliteration_message, list):
            transliteration_message = []
        if not isinstance(transliteration_translation, list):
            transliteration_translation = [[] for _ in translation]

        return self.overlay_image.createOverlayImageSmallLog(
            message,
            your_language,
            translation,
            target_language_list,
            transliteration_message=transliteration_message,
            transliteration_translation=transliteration_translation,
            accent_color=config.OVERLAY_SMALL_LOG_SETTINGS.get("accent_color", "theme-neon-cyan"),
            background_mode=config.OVERLAY_SMALL_LOG_SETTINGS.get("background_mode", "transparent_black"),
        )

    def createOverlayImageSmallMessage(self, message):
        self.ensure_initialized()
        ui_language = config.UI_LANGUAGE
        convert_languages = {
            "en": "Default",
            "jp": "Japanese",
            "ko":"Korean",
            "zh-Hans":"Chinese Simplified",
            "zh-Hant":"Chinese Traditional",
        }
        language = convert_languages.get(ui_language, "Default")
        return self.overlay_image.createOverlayImageSmallLog(
            message,
            language,
            accent_color=config.OVERLAY_SMALL_LOG_SETTINGS.get("accent_color", "theme-neon-cyan"),
            background_mode=config.OVERLAY_SMALL_LOG_SETTINGS.get("background_mode", "transparent_black"),
        )

    def clearOverlayImageSmallLog(self):
        self.ensure_initialized()
        self.overlay.clearImage("small")

    def updateOverlaySmallLog(self, img):
        self.ensure_initialized()
        self.overlay.updateImage(img, "small")

    def updateOverlaySmallLogSettings(self):
        self.ensure_initialized()
        size = "small"

        if (self.overlay.settings[size]["x_pos"] != config.OVERLAY_SMALL_LOG_SETTINGS["x_pos"] or
            self.overlay.settings[size]["y_pos"] != config.OVERLAY_SMALL_LOG_SETTINGS["y_pos"] or
            self.overlay.settings[size]["z_pos"] != config.OVERLAY_SMALL_LOG_SETTINGS["z_pos"] or
            self.overlay.settings[size]["x_rotation"] != config.OVERLAY_SMALL_LOG_SETTINGS["x_rotation"] or
            self.overlay.settings[size]["y_rotation"] != config.OVERLAY_SMALL_LOG_SETTINGS["y_rotation"] or
            self.overlay.settings[size]["z_rotation"] != config.OVERLAY_SMALL_LOG_SETTINGS["z_rotation"] or
            self.overlay.settings[size]["tracker"] != config.OVERLAY_SMALL_LOG_SETTINGS["tracker"]):
            self.overlay.updatePosition(
                config.OVERLAY_SMALL_LOG_SETTINGS["x_pos"],
                config.OVERLAY_SMALL_LOG_SETTINGS["y_pos"],
                config.OVERLAY_SMALL_LOG_SETTINGS["z_pos"],
                config.OVERLAY_SMALL_LOG_SETTINGS["x_rotation"],
                config.OVERLAY_SMALL_LOG_SETTINGS["y_rotation"],
                config.OVERLAY_SMALL_LOG_SETTINGS["z_rotation"],
                config.OVERLAY_SMALL_LOG_SETTINGS["tracker"],
                size,
            )
        if (self.overlay.settings[size]["display_duration"] != config.OVERLAY_SMALL_LOG_SETTINGS["display_duration"]):
            self.overlay.updateDisplayDuration(config.OVERLAY_SMALL_LOG_SETTINGS["display_duration"], size)
        if (self.overlay.settings[size]["fadeout_duration"] != config.OVERLAY_SMALL_LOG_SETTINGS["fadeout_duration"]):
            self.overlay.updateFadeoutDuration(config.OVERLAY_SMALL_LOG_SETTINGS["fadeout_duration"], size)
        if (self.overlay.settings[size]["opacity"] != config.OVERLAY_SMALL_LOG_SETTINGS["opacity"]):
            self.overlay.updateOpacity(config.OVERLAY_SMALL_LOG_SETTINGS["opacity"], size, True)
        if (self.overlay.settings[size]["ui_scaling"] != config.OVERLAY_SMALL_LOG_SETTINGS["ui_scaling"]):
            self.overlay.updateUiScaling(config.OVERLAY_SMALL_LOG_SETTINGS["ui_scaling"], size)

    def createOverlayImageLargeLog(
        self,
        message_type: str,
        message: Optional[str],
        your_language: Optional[str],
        translation: list,
        target_language: Optional[dict] = None,
        transliteration_message: Optional[list] = None,
        transliteration_translation: Optional[list] = None,
        translation_target_slots: Optional[list[str]] = None,
    ) -> object:
        self.ensure_initialized()
        target_language_list = self._overlayTargetLanguageList(
            target_language,
            translation_target_slots,
        )
        newest_first = config.OVERLAY_LARGE_LOG_SETTINGS.get("log_order") == "newest_first"
        return self.overlay_image.createOverlayImageLargeLog(
            message_type,
            message,
            your_language,
            translation,
            target_language_list,
            transliteration_message,
            transliteration_translation,
            newest_first=newest_first,
            accent_color=config.OVERLAY_LARGE_LOG_SETTINGS.get("accent_color", "theme-neon-cyan"),
            background_mode=config.OVERLAY_LARGE_LOG_SETTINGS.get("background_mode", "transparent_black"),
        )

    def createOverlayImageLargeMessage(self, message):
        self.ensure_initialized()
        ui_language = config.UI_LANGUAGE
        convert_languages = {
            "en": "Default",
            "jp": "Japanese",
            "ko":"Korean",
            "zh-Hans":"Chinese Simplified",
            "zh-Hant":"Chinese Traditional",
        }
        language = convert_languages.get(ui_language, "Default")
        overlay_image = OverlayImage(config.PATH_LOCAL)
        accent_color = config.OVERLAY_LARGE_LOG_SETTINGS.get("accent_color", "theme-neon-cyan")
        background_mode = config.OVERLAY_LARGE_LOG_SETTINGS.get("background_mode", "transparent_black")

        for _ in range(2):
            overlay_image.createOverlayImageLargeLog("send", message, language, newest_first=config.OVERLAY_LARGE_LOG_SETTINGS.get("log_order") == "newest_first", accent_color=accent_color, background_mode=background_mode)
            overlay_image.createOverlayImageLargeLog("receive", message, language, newest_first=config.OVERLAY_LARGE_LOG_SETTINGS.get("log_order") == "newest_first", accent_color=accent_color, background_mode=background_mode)
        return overlay_image.createOverlayImageLargeLog("send", message, language, newest_first=config.OVERLAY_LARGE_LOG_SETTINGS.get("log_order") == "newest_first", accent_color=accent_color, background_mode=background_mode)

    def clearOverlayImageLargeLog(self):
        self.ensure_initialized()
        self.overlay.clearImage("large")

    def updateOverlayLargeLog(self, img):
        self.ensure_initialized()
        self.overlay.updateImage(img, "large")

    def updateOverlayLargeLogSettings(self):
        self.ensure_initialized()
        size = "large"
        if (self.overlay.settings[size]["x_pos"] != config.OVERLAY_LARGE_LOG_SETTINGS["x_pos"] or
            self.overlay.settings[size]["y_pos"] != config.OVERLAY_LARGE_LOG_SETTINGS["y_pos"] or
            self.overlay.settings[size]["z_pos"] != config.OVERLAY_LARGE_LOG_SETTINGS["z_pos"] or
            self.overlay.settings[size]["x_rotation"] != config.OVERLAY_LARGE_LOG_SETTINGS["x_rotation"] or
            self.overlay.settings[size]["y_rotation"] != config.OVERLAY_LARGE_LOG_SETTINGS["y_rotation"] or
            self.overlay.settings[size]["z_rotation"] != config.OVERLAY_LARGE_LOG_SETTINGS["z_rotation"] or
            self.overlay.settings[size]["tracker"] != config.OVERLAY_LARGE_LOG_SETTINGS["tracker"]):
            self.overlay.updatePosition(
                config.OVERLAY_LARGE_LOG_SETTINGS["x_pos"],
                config.OVERLAY_LARGE_LOG_SETTINGS["y_pos"],
                config.OVERLAY_LARGE_LOG_SETTINGS["z_pos"],
                config.OVERLAY_LARGE_LOG_SETTINGS["x_rotation"],
                config.OVERLAY_LARGE_LOG_SETTINGS["y_rotation"],
                config.OVERLAY_LARGE_LOG_SETTINGS["z_rotation"],
                config.OVERLAY_LARGE_LOG_SETTINGS["tracker"],
                size,
            )
        if (self.overlay.settings[size]["display_duration"] != config.OVERLAY_LARGE_LOG_SETTINGS["display_duration"]):
            self.overlay.updateDisplayDuration(config.OVERLAY_LARGE_LOG_SETTINGS["display_duration"], size)
        if (self.overlay.settings[size]["fadeout_duration"] != config.OVERLAY_LARGE_LOG_SETTINGS["fadeout_duration"]):
            self.overlay.updateFadeoutDuration(config.OVERLAY_LARGE_LOG_SETTINGS["fadeout_duration"], size)
        if (self.overlay.settings[size]["opacity"] != config.OVERLAY_LARGE_LOG_SETTINGS["opacity"]):
            self.overlay.updateOpacity(config.OVERLAY_LARGE_LOG_SETTINGS["opacity"], size, True)
        if (self.overlay.settings[size]["ui_scaling"] != config.OVERLAY_LARGE_LOG_SETTINGS["ui_scaling"]):
            self.overlay.updateUiScaling(config.OVERLAY_LARGE_LOG_SETTINGS["ui_scaling"] * 0.25, size)

    def startOverlay(self):
        self.ensure_initialized()
        self.overlay.startOverlay()

    def shutdownOverlay(self):
        self.ensure_initialized()
        self.overlay.shutdownOverlay()

    def startWatchdog(self):
        self.ensure_initialized()
        self.th_watchdog = threadFnc(self.watchdog.start)
        self.th_watchdog.daemon = True
        self.th_watchdog.start()

    def feedWatchdog(self):
        self.ensure_initialized()
        self.watchdog.feed()

    def setWatchdogCallback(self, callback):
        self.ensure_initialized()
        self.watchdog.setCallback(callback)

    def stopWatchdog(self):
        self.ensure_initialized()
        if isinstance(self.th_watchdog, threadFnc):
            self.th_watchdog.stop()
            self.th_watchdog.join()
            self.th_watchdog = None

    def message_handler(websocket, message):
        """WebSocketメッセージ受信時の処理"""
        pass

    def startWebSocketServer(self, host, port):
        """WebSocketサーバーを起動し、別スレッドで実行する"""
        self.ensure_initialized()
        if self.websocket_server_alive is True:
            # サーバーが既に起動している場合は何もしない
            return

        self.websocket_server_loop = True
        self.websocket_server_alive = False  # 初期状態を明示

        async def WebSocketServerMain():
            try:
                self.websocket_server = WebSocketServer(
                    host=host,
                    port=port,
                )
                self.websocket_server.set_message_handler(self.message_handler)
                self.websocket_server.start()
                self.websocket_server_alive = True

                # イベントループが終了するまで待機
                while self.websocket_server_loop:
                    # self.websocket_server.send("Server is running...")
                    await asyncio.sleep(0.5)  # 応答性向上のため間隔短縮

            except Exception:
                errorLogging()
                # 具体的なエラー内容をログに残す場合
                # self.logger.error(f"WebSocket server error: {str(e)}")
            finally:
                # 確実にサーバーを停止
                if hasattr(self, 'websocket_server') and self.websocket_server:
                    self.websocket_server.stop()
                self.websocket_server_alive = False

        self.th_websocket_server = Thread(target=lambda: asyncio.run(WebSocketServerMain()))
        self.th_websocket_server.daemon = True
        self.th_websocket_server.start()

    def stopWebSocketServer(self):
        """WebSocketサーバーを停止する"""
        self.ensure_initialized()
        if not hasattr(self, 'th_websocket_server') or self.th_websocket_server is None:
            return

        self.websocket_server_loop = False

        try:
            # 一定時間待機してからタイムアウト
            self.th_websocket_server.join(timeout=2.0)

            if self.th_websocket_server.is_alive():
                # タイムアウト後もスレッドが生きている場合の処理
                self.logger.warning("WebSocket server thread did not terminate properly")
        except Exception:
            errorLogging()
        finally:
            self.th_websocket_server = None
            self.websocket_server = None
            self.websocket_server_alive = False

    def checkWebSocketServerAlive(self):
        """WebSocketサーバーの稼働状態を確認する"""
        self.ensure_initialized()
        return self.websocket_server_alive

    def websocketSendMessage(self, message_dict:dict):
        """
        WebSocketサーバーから全クライアントにメッセージを送信する
        :param message_dict: 送信するメッセージの辞書
        :return: 送信成功したかどうか
        """
        self.ensure_initialized()
        if not self.websocket_server_alive or not self.websocket_server:
            return False
        try:
            message_json = json.dumps(message_dict)
            return self.websocket_server.send(message_json)
        except Exception:
            errorLogging()
            return False

    def setCopyToClipboardAndPasteFromClipboard(self, text:str) -> bool:
        self.ensure_initialized()
        try:
            if isinstance(self.clipboard, Clipboard):
                self.clipboard.copy_and_paste(text)
                return True
            else:
                return False
        except Exception:
            errorLogging()
            return False

    def telemetryInit(self, enabled: bool, app_version: str):
        """Model 内で Telemetry を初期化"""
        self.telemetry.init(enabled=enabled, app_version=app_version)

    def shutdownTranscriptionPipelines(self) -> None:
        """Stop both source pipelines, then retire the one shared runtime.

        Recorder and pipeline callbacks already executing are cooperative
        boundaries: they cannot be force-cancelled, so shutdown waits for them.
        Google recognition itself is bounded by its configured operation timeout.
        """
        if not getattr(self, "_inited", False):
            return
        first_error = None
        for stop in (
            self.stopCheckMicEnergy,
            self.stopCheckSpeakerEnergy,
            self.stopMicTranscript,
            self.stopSpeakerTranscript,
        ):
            try:
                stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
                errorLogging()
        try:
            self.whisper_runtime_manager.shutdown()
        except Exception as error:
            if first_error is None:
                first_error = error
            errorLogging()
        if first_error is not None:
            raise first_error

    def telemetryShutdown(self):
        """Model cleanup on application shutdown."""
        # Telemetry 終了（app_closed 送信）
        if hasattr(self, "telemetry") and self.telemetry:
            self.telemetry.shutdown()

    def telemetryTrack(self, event: str, payload: dict = None):
        """汎用テレメトリイベント送信 (Model ラッパー)"""
        if hasattr(self, "telemetry") and self.telemetry:
            self.telemetry.track(event, payload)

    def telemetryTrackCoreFeature(self, feature: str):
        """コア機能テレメトリイベント送信 (Model ラッパー)"""
        if hasattr(self, "telemetry") and self.telemetry:
            self.telemetry.track_core_feature(feature)

model = Model()
