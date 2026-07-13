"""Runtime transcriber that wraps Google SpeechRecognition and faster-whisper.

This class focuses on converting incoming raw audio buffers into text using
either the Google web recognizer (online) or a local Whisper model (offline).
"""

import time
import importlib
from dataclasses import dataclass
from io import BytesIO
from threading import Event
from queue import Empty
import wave
from typing import Any, Callable, Dict, List, Optional, Union
from speech_recognition import Recognizer, AudioData, AudioFile
from speech_recognition.exceptions import UnknownValueError
from datetime import timedelta
from pyaudiowpatch import get_sample_size, paInt16
from models.pipeline.latest_queue import QueueClosed
from models.pipeline.pipeline_types import PipelineSource, PipelineStatusEvent
from .transcription_languages import transcription_lang
from .whisper_runtime import WhisperRuntimeLease

import json
import numpy as np
from pydub import AudioSegment
from utils import errorLogging

import warnings
warnings.simplefilter('ignore', RuntimeWarning)

PHRASE_TIMEOUT = 3
MAX_PHRASES = 10
MAX_AUDIO_BUFFER_SECONDS = 30
MAX_WHISPER_LIVE_AUDIO_SECONDS = 6
GOOGLE_RECOGNITION_TIMEOUT_SECONDS = 15
ENGINE_RECOVERY_FAILURE_THRESHOLD = 3


def _getTorch():
    try:
        return importlib.import_module("torch")
    except Exception:
        return None


def _getWhisperBeamSize(profile: str) -> int:
    module = importlib.import_module(".transcription_whisper", __package__)
    return module.getWhisperBeamSize(profile)


def _getVoskHelpers():
    module = importlib.import_module(".transcription_vosk", __package__)
    return module.getVoskRecognizer, module.checkVoskWeight


def _getParakeetHelpers():
    module = importlib.import_module(".transcription_parakeet", __package__)
    return module.getParakeetModel, module.checkParakeetWeight


def _getSenseVoiceHelpers():
    module = importlib.import_module(".transcription_sensevoice", __package__)
    return module.getSenseVoiceModel, module.checkSenseVoiceWeight


def _languageCode(engine: str, language: str, country: str) -> str:
    try:
        return transcription_lang[language][country].get(engine, "")
    except Exception:
        return ""


def _languageForCode(engine: str, code: Optional[str], languages: List[str], countries: List[str]) -> Optional[str]:
    if not code:
        return None
    for language, country in zip(languages, countries):
        if _languageCode(engine, language, country) == code:
            return language
    return None


@dataclass(frozen=True)
class TranscriberPipelineContext:
    source: PipelineSource
    whisper_runtime_lease: Optional[WhisperRuntimeLease]
    whisper_decoding_profile: str
    generation: int
    is_generation_current: Callable[[int], bool]
    emit_metric: Callable[[PipelineStatusEvent], None]
    request_recovery: Callable[[PipelineSource, int, str, Event], None]


class AudioTranscriber:
    """Convert queued audio buffers into transcripts.

    Public attributes set by the constructor:
    - speaker: bool
    - phrase_timeout: int
    - max_phrases: int

    Methods are intentionally permissive about input types to match the
    existing codebase; this wrapper adds typing for clarity.
    """

    def __init__(
        self,
        speaker: bool,
        source: Any,
        phrase_timeout: int,
        max_phrases: int,
        transcription_engine: str,
        root: Optional[str] = None,
        whisper_weight_type: Optional[str] = None,
        vosk_weight_type: Optional[str] = None,
        parakeet_weight_type: Optional[str] = None,
        sensevoice_weight_type: Optional[str] = None,
        device: str = "cpu",
        device_index: int = 0,
        compute_type: str = "auto",
        pipeline_context: Optional[TranscriberPipelineContext] = None,
    ) -> None:
        self.speaker = speaker
        self.phrase_timeout = phrase_timeout
        self.max_phrases = max_phrases
        self.transcript_data: List[Dict[str, Any]] = []
        self.transcript_changed_event = Event()
        self.audio_recognizer = Recognizer()
        self.audio_recognizer.operation_timeout = GOOGLE_RECOGNITION_TIMEOUT_SECONDS
        self.transcription_engine = "Google"
        self.vosk_recognizer = None
        self.parakeet_model = None
        self.sensevoice_model = None
        self.root = root
        self.whisper_weight_type = whisper_weight_type
        self.device = device
        self.device_index = device_index
        self.compute_type = compute_type
        self.pipeline_context = pipeline_context
        self._recognition_failure_count = 0
        self.audio_sources: Dict[str, Any] = {
            "sample_rate": source.SAMPLE_RATE,
            "sample_width": source.SAMPLE_WIDTH,
            "channels": source.channels,
            "last_sample": bytes(),
            "last_spoken": None,
            "new_phrase": True,
            "phrase_started_at_monotonic": None,
            "process_data_func": self.processSpeakerData if speaker else self.processMicData,
        }

        if transcription_engine == "Vosk":
            getVoskRecognizer, checkVoskWeight = _getVoskHelpers()
        elif transcription_engine == "Parakeet":
            getParakeetModel, checkParakeetWeight = _getParakeetHelpers()
        elif transcription_engine == "SenseVoice":
            getSenseVoiceModel, checkSenseVoiceWeight = _getSenseVoiceHelpers()

        if (
            transcription_engine == "Whisper"
            and pipeline_context is not None
            and pipeline_context.whisper_runtime_lease is not None
        ):
            self.transcription_engine = "Whisper"
        elif transcription_engine == "Vosk" and vosk_weight_type and checkVoskWeight(root, vosk_weight_type) is True:
            try:
                self.vosk_recognizer = getVoskRecognizer(root, vosk_weight_type)
                self.transcription_engine = "Vosk"
            except Exception:
                errorLogging()
        elif transcription_engine == "Parakeet" and parakeet_weight_type and checkParakeetWeight(root, parakeet_weight_type) is True:
            try:
                self.parakeet_model = getParakeetModel(
                    root, parakeet_weight_type, device=device, device_index=device_index
                )
                self.transcription_engine = "Parakeet"
            except Exception:
                errorLogging()
        elif transcription_engine == "SenseVoice" and sensevoice_weight_type and checkSenseVoiceWeight(root, sensevoice_weight_type) is True:
            try:
                self.sensevoice_model = getSenseVoiceModel(
                    root, sensevoice_weight_type, device="cpu", device_index=0
                )
                self.transcription_engine = "SenseVoice"
            except Exception:
                errorLogging()

    def _resetRecognizer(self) -> None:
        self.audio_recognizer = Recognizer()
        self.audio_recognizer.operation_timeout = GOOGLE_RECOGNITION_TIMEOUT_SECONDS

    def _handleRecognitionFailure(self) -> None:
        self._recognition_failure_count += 1
        if self._recognition_failure_count < ENGINE_RECOVERY_FAILURE_THRESHOLD:
            return
        self._recognition_failure_count = 0
        self.clearTranscriptData()
        match self.transcription_engine:
            case "Google":
                self._resetRecognizer()
            case _:
                pass

    def _handleRecognitionSuccess(self) -> None:
        self._recognition_failure_count = 0

    def clearLiveAudioSample(self) -> None:
        self.audio_sources["last_sample"] = bytes()

    @staticmethod
    def _queueDepth(audio_queue: Any) -> int:
        try:
            return max(0, int(audio_queue.qsize()))
        except Exception:
            return 0

    def _emitPipelineMetric(
        self,
        *,
        stage: str,
        outcome: str,
        queue_age_ms: Optional[int],
        duration_ms: Optional[int],
        queue_depth: int,
        error_code: Optional[str] = None,
    ) -> None:
        context = self.pipeline_context
        if context is None:
            return
        event = PipelineStatusEvent(
            schema_version=1,
            trace_id=None,
            source=context.source,
            stage=stage,
            engine=(self.transcription_engine if stage == "transcription" else None),
            target_slot=None,
            outcome=outcome,
            queue_age_ms=queue_age_ms,
            duration_ms=duration_ms,
            queue_depth=queue_depth,
            dropped_count=0,
            observed_at_ms=int(time.time() * 1000),
            error_code=error_code,
        )
        try:
            context.emit_metric(event)
        except Exception:
            errorLogging()

    def _isGenerationCurrent(self) -> bool:
        context = self.pipeline_context
        if context is None:
            return True
        try:
            return bool(context.is_generation_current(context.generation))
        except Exception:
            errorLogging()
            return False

    def transcribeAudioQueue(
        self,
        audio_queue: Any,
        languages: List[str],
        countries: List[str],
        avg_logprob: float = -0.8,
        no_speech_prob: float = 0.6,
        no_repeat_ngram_size: int = 0,
        vad_filter: bool = False,
        vad_parameters: Optional[Union[dict, Any]] = None,
    ) -> bool:
        try:
            if audio_queue.empty():
                time.sleep(0.01)
                return False
            chunk = audio_queue.get()
        except (Empty, QueueClosed):
            return False

        self.updateLastSampleAndPhraseStatus(
            chunk.data,
            chunk.spoken_at,
            chunk.captured_at_monotonic,
        )
        final_chunk = self.drainAudioQueue(audio_queue) or chunk
        dequeued_at = time.perf_counter()
        queue_age_ms = max(
            0,
            int((dequeued_at - final_chunk.captured_at_monotonic) * 1000),
        )
        queue_depth = self._queueDepth(audio_queue)
        self._emitPipelineMetric(
            stage="queue",
            outcome="success",
            queue_age_ms=queue_age_ms,
            duration_ms=None,
            queue_depth=queue_depth,
        )
        self._emitPipelineMetric(
            stage="transcription",
            outcome="running",
            queue_age_ms=queue_age_ms,
            duration_ms=None,
            queue_depth=queue_depth,
        )

        confidences: List[Dict[str, Any]] = [{"confidence": 0, "text": "", "language": None}]
        inference_started_at = time.perf_counter()
        safe_to_restart: Optional[Event] = None
        terminal_metric_emitted = False

        def emit_terminal_metric(
            outcome: str,
            error_code: Optional[str] = None,
        ) -> None:
            nonlocal terminal_metric_emitted
            if terminal_metric_emitted:
                return
            terminal_metric_emitted = True
            self._emitPipelineMetric(
                stage="transcription",
                outcome=outcome,
                queue_age_ms=queue_age_ms,
                duration_ms=max(
                    0,
                    int(
                        (time.perf_counter() - inference_started_at) * 1000
                    ),
                ),
                queue_depth=queue_depth,
                error_code=error_code,
            )

        def request_whisper_recovery() -> None:
            nonlocal safe_to_restart
            context = self.pipeline_context
            if context is None or safe_to_restart is not None:
                return
            safe_to_restart = Event()
            try:
                context.request_recovery(
                    context.source,
                    context.generation,
                    "whisper_inference_failed",
                    safe_to_restart,
                )
            except Exception:
                errorLogging()

        try:
            if not languages or not countries:
                emit_terminal_metric(
                    "error",
                    "transcription_languages_unavailable",
                )
                return False
            audio_data = self.audio_sources["process_data_func"]()
            match self.transcription_engine:
                case "Google":
                    google_error_count = 0
                    for language, country in zip(languages, countries):
                        try:
                            text, confidence = self.audio_recognizer.recognize_google(
                                audio_data,
                                language=transcription_lang[language][country][self.transcription_engine],
                                with_confidence=True
                                )
                            confidences.append({"confidence": confidence, "text": text, "language": language})
                        except UnknownValueError:
                            pass
                        except Exception:
                            google_error_count += 1
                            errorLogging()
                            pass
                    if len(languages) > 0 and google_error_count >= len(languages):
                        self._handleRecognitionFailure()
                        self.clearLiveAudioSample()
                        emit_terminal_metric(
                            "error",
                            "google_recognition_failed",
                        )
                        return False
                case "Whisper":
                    try:
                        context = self.pipeline_context
                        lease = (
                            context.whisper_runtime_lease
                            if context is not None
                            else None
                        )
                        if lease is None:
                            raise RuntimeError("Whisper runtime lease is unavailable")
                        audio_data = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        torch = _getTorch()
                        if torch is not None and isinstance(audio_data, torch.Tensor):
                            audio_data = audio_data.detach().numpy()
                        if audio_data.size == 0 or not np.any(audio_data):
                            if self._isGenerationCurrent():
                                emit_terminal_metric("success")
                            return True
                        max_samples = 16000 * MAX_WHISPER_LIVE_AUDIO_SECONDS
                        if audio_data.size > max_samples:
                            audio_data = audio_data[-max_samples:]

                        source_language = _languageCode("Whisper", languages[0], countries[0]) if len(languages) == 1 else None
                        inference_result = lease.transcribe(
                            audio_data,
                            beam_size=_getWhisperBeamSize(
                                context.whisper_decoding_profile
                            ),
                            temperature=0.0,
                            log_prob_threshold=avg_logprob,
                            no_speech_threshold=no_speech_prob,
                            language=source_language,
                            word_timestamps=False,
                            without_timestamps=True,
                            task="transcribe",
                            no_repeat_ngram_size=no_repeat_ngram_size,
                            vad_filter=vad_filter,
                            vad_parameters=vad_parameters,
                        )
                        segments = inference_result.segments
                        info = inference_result.info
                        text = ""
                        for s in segments:
                            if s.avg_logprob < avg_logprob or s.no_speech_prob > no_speech_prob:
                                continue
                            text += s.text

                        result_language = (
                            languages[0] if len(languages) == 1
                            else _languageForCode("Whisper", getattr(info, "language", None), languages, countries)
                        )
                        if result_language:
                            confidences.append({
                                "confidence": info.language_probability,
                                "text": text,
                                "language": result_language,
                            })
                    except Exception:
                        errorLogging()
                        request_whisper_recovery()
                        emit_terminal_metric(
                            "error",
                            "whisper_inference_failed",
                        )
                        return False
                case "Vosk":
                    if self.vosk_recognizer is None:
                        pass
                    else:
                        try:
                            pcm16 = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
                            result_text = self.vosk_recognizer.transcribe(pcm16, sample_rate=16000)
                        except Exception:
                            errorLogging()
                            self.clearLiveAudioSample()
                            emit_terminal_metric(
                                "error",
                                "vosk_inference_failed",
                            )
                            return False
                        if result_text:
                            primary = languages[0] if languages else None
                            confidences.append({"confidence": 1.0, "text": result_text, "language": primary})
                case "Parakeet":
                    if self.parakeet_model is None:
                        pass
                    else:
                        pcm = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        try:
                            result_text = self.parakeet_model.transcribe(pcm, sample_rate=16000)
                        except Exception:
                            errorLogging()
                            self.clearLiveAudioSample()
                            emit_terminal_metric(
                                "error",
                                "parakeet_inference_failed",
                            )
                            return False
                        if result_text:
                            primary = languages[0] if languages else None
                            confidences.append({"confidence": 1.0, "text": result_text, "language": primary})
                case "SenseVoice":
                    if self.sensevoice_model is None:
                        pass
                    else:
                        pcm = np.frombuffer(
                            audio_data.get_raw_data(convert_rate=16000, convert_width=2), np.int16
                        ).flatten().astype(np.float32) / 32768.0
                        try:
                            source_language = (
                                _languageCode("SenseVoice", languages[0], countries[0])
                                if len(languages) == 1
                                else "auto"
                            )
                            recognize = getattr(self.sensevoice_model, "recognize", None)
                            if callable(recognize):
                                result = recognize(pcm, sample_rate=16000, language=source_language)
                                result_text = result.get("text", "")
                                result_language_code = result.get("language", "")
                            else:
                                result_text = self.sensevoice_model.transcribe(
                                    pcm, sample_rate=16000, language=source_language
                                )
                                result_language_code = source_language if source_language != "auto" else ""
                        except Exception:
                            errorLogging()
                            self.clearLiveAudioSample()
                            emit_terminal_metric(
                                "error",
                                "sensevoice_inference_failed",
                            )
                            return False
                        if result_text:
                            primary = (
                                languages[0] if len(languages) == 1
                                else _languageForCode("SenseVoice", result_language_code, languages, countries)
                            )
                            if primary:
                                confidences.append({"confidence": 1.0, "text": result_text, "language": primary})

        except UnknownValueError:
            if self.transcription_engine == "Whisper":
                request_whisper_recovery()
                emit_terminal_metric(
                    "error",
                    "whisper_inference_failed",
                )
                return False
        except Exception:
            errorLogging()
            if self.transcription_engine == "Whisper":
                request_whisper_recovery()
                emit_terminal_metric(
                    "error",
                    "whisper_inference_failed",
                )
                return False
            else:
                self._handleRecognitionFailure()
                self.clearLiveAudioSample()
                emit_terminal_metric(
                    "error",
                    "audio_processing_failed",
                )
                return False
        finally:
            if self.transcription_engine == "Whisper":
                self.clearLiveAudioSample()
            if safe_to_restart is not None:
                safe_to_restart.set()

        result = max(confidences, key=lambda x: x["confidence"])
        result["started_at_monotonic"] = self.audio_sources[
            "phrase_started_at_monotonic"
        ]
        if not self._isGenerationCurrent():
            return True
        emit_terminal_metric("success")
        if result["text"] != "":
            self._handleRecognitionSuccess()
            if not self._isGenerationCurrent():
                return True
            self.updateTranscript(result)
        return True

    def resetAudioSource(self, source: Any) -> None:
        self.audio_sources["sample_rate"] = source.SAMPLE_RATE
        self.audio_sources["sample_width"] = source.SAMPLE_WIDTH
        self.audio_sources["channels"] = source.channels
        self.audio_sources["last_sample"] = bytes()
        self.audio_sources["last_spoken"] = None
        self.audio_sources["new_phrase"] = True
        self.audio_sources["phrase_started_at_monotonic"] = None

    def drainAudioQueue(self, audio_queue: Any) -> Optional[Any]:
        final_chunk = None
        while True:
            try:
                chunk = audio_queue.get_nowait()
            except (Empty, QueueClosed):
                break
            self.updateLastSampleAndPhraseStatus(
                chunk.data,
                chunk.spoken_at,
                chunk.captured_at_monotonic,
            )
            final_chunk = chunk
        return final_chunk

    def updateLastSampleAndPhraseStatus(
        self,
        data: bytes,
        time_spoken,
        captured_at_monotonic: Optional[float] = None,
    ) -> None:
        source_info = self.audio_sources
        if source_info["last_spoken"] and time_spoken - source_info["last_spoken"] > timedelta(seconds=self.phrase_timeout):
            source_info["last_sample"] = bytes()
            source_info["new_phrase"] = True
            source_info["phrase_started_at_monotonic"] = captured_at_monotonic
        else:
            source_info["new_phrase"] = False
            if source_info["phrase_started_at_monotonic"] is None:
                source_info["phrase_started_at_monotonic"] = captured_at_monotonic

        source_info["last_sample"] += data
        source_info["last_spoken"] = time_spoken
        self.trimLastSampleToMaxDuration()

    def trimLastSampleToMaxDuration(self) -> None:
        source_info = self.audio_sources
        try:
            frame_width = max(1, int(source_info["sample_width"]) * int(source_info["channels"]))
            max_frames = int(source_info["sample_rate"]) * MAX_AUDIO_BUFFER_SECONDS
            max_bytes = max(frame_width, max_frames * frame_width)
            if len(source_info["last_sample"]) > max_bytes:
                source_info["last_sample"] = source_info["last_sample"][-max_bytes:]
        except Exception:
            errorLogging()

    def processMicData(self) -> AudioData:
        audio_data = AudioData(
            self.audio_sources["last_sample"], self.audio_sources["sample_rate"], self.audio_sources["sample_width"]
        )
        return audio_data

    def processSpeakerData(self) -> AudioData:
        temp_file = BytesIO()
        with wave.open(temp_file, 'wb') as wf:
            wf.setnchannels(self.audio_sources["channels"])
            wf.setsampwidth(get_sample_size(paInt16))
            wf.setframerate(self.audio_sources["sample_rate"])
            wf.writeframes(self.audio_sources["last_sample"])
        temp_file.seek(0)

        if self.audio_sources["channels"] > 2:
            audio = AudioSegment.from_file(temp_file, format="wav")
            mono_audio = audio.set_channels(1)
            temp_file = BytesIO()
            mono_audio.export(temp_file, format="wav")
            temp_file.seek(0)

        with AudioFile(temp_file) as source:
            audio = self.audio_recognizer.record(source)
        return audio

    def updateTranscript(self, result: dict) -> None:
        source_info = self.audio_sources
        transcript = self.transcript_data

        if source_info["new_phrase"] or len(transcript) == 0:
            if len(transcript) > self.max_phrases:
                transcript.pop(-1)
            transcript.insert(0, result)
        else:
            transcript[0] = result

    def getTranscript(self) -> dict:
        if len(self.transcript_data) > 0:
            result = self.transcript_data.pop(-1)
        else:
            result = {
                "confidence": 0,
                "text": "",
                "language": None,
                "started_at_monotonic": None,
            }
        return result

    def clearTranscriptData(self) -> None:
        self.transcript_data.clear()
        self.audio_sources["last_sample"] = bytes()
        self.audio_sources["last_spoken"] = None
        self.audio_sources["new_phrase"] = True
        self.audio_sources["phrase_started_at_monotonic"] = None
