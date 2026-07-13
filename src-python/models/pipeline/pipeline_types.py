from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class PipelineSource(str, Enum):
    MIC = "mic"
    SPEAKER = "speaker"


class TranslationStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    FALLBACK = "fallback"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    SKIPPED_OVERLOAD = "skipped_overload"


@dataclass(frozen=True)
class AudioChunk:
    data: bytes
    spoken_at: datetime
    captured_at_monotonic: float

    def __iter__(self):
        yield self.data
        yield self.spoken_at


@dataclass(frozen=True)
class PipelineStatusEvent:
    schema_version: int
    trace_id: Optional[str]
    source: PipelineSource
    stage: str
    engine: Optional[str]
    target_slot: Optional[str]
    outcome: str
    queue_age_ms: Optional[int]
    duration_ms: Optional[int]
    queue_depth: int
    dropped_count: int
    observed_at_ms: int
    error_code: Optional[str]

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "source": self.source.value,
            "stage": self.stage,
            "engine": self.engine,
            "target_slot": self.target_slot,
            "outcome": self.outcome,
            "queue_age_ms": self.queue_age_ms,
            "duration_ms": self.duration_ms,
            "queue_depth": self.queue_depth,
            "dropped_count": self.dropped_count,
            "observed_at_ms": self.observed_at_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class TranslationTarget:
    target_slot: str
    language: str
    country: str


@dataclass(frozen=True)
class LanguageSlotSnapshot:
    target_slot: str
    language: str
    country: str
    enabled: bool


@dataclass(frozen=True)
class MessageFormatSnapshot:
    message_prefix: str
    message_suffix: str
    translation_prefix: str
    translation_suffix: str
    translation_separator: str
    message_translation_separator: str
    translation_first: bool


@dataclass(frozen=True)
class OutputConfigSnapshot:
    selected_tab_no: str
    translation_enabled: bool
    send_message_to_vrc: bool
    send_received_message_to_vrc: bool
    send_only_translated_messages: bool
    overlay_small_log: bool
    overlay_large_log: bool
    overlay_show_only_translated_messages: bool
    enable_clipboard: bool
    logger_feature: bool
    convert_message_to_hiragana: bool
    convert_message_to_romaji: bool
    websocket_requested: bool
    your_languages: tuple[LanguageSlotSnapshot, ...]
    your_translation_languages: tuple[LanguageSlotSnapshot, ...]
    target_languages: tuple[LanguageSlotSnapshot, ...]
    send_format: MessageFormatSnapshot
    received_format: MessageFormatSnapshot


@dataclass(frozen=True)
class TranscriptionTrace:
    trace_id: str
    generation: int
    source: PipelineSource
    original_message: str
    source_language: str
    original_transliteration: tuple[dict[str, str], ...]
    targets: tuple[TranslationTarget, ...]
    providers: tuple[str, ...]
    ctranslate2_weight_type: str
    context_history: tuple[dict[str, object], ...]
    started_at_monotonic: float
    output_config: OutputConfigSnapshot


@dataclass(frozen=True)
class TranslationJob:
    trace_id: str
    generation: int
    source: PipelineSource
    original_message: str
    source_language: str
    target: TranslationTarget
    providers: tuple[str, ...]
    ctranslate2_weight_type: str
    context_history: tuple[dict[str, object], ...]
    enqueued_at_monotonic: float


@dataclass(frozen=True)
class TranslationAttempt:
    status: TranslationStatus
    engine: str
    message: Optional[str]
    duration_ms: int
    error_code: Optional[str]


@dataclass(frozen=True)
class TranslationUpdate:
    trace_id: str
    target_slot: str
    status: TranslationStatus
    engine: Optional[str]
    message: Optional[str]
    transliteration: tuple[dict[str, str], ...]
    duration_ms: Optional[int]
    queue_position: int
    error_code: Optional[str]

    def to_payload(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "target_slot": self.target_slot,
            "status": self.status.value,
            "engine": self.engine,
            "message": self.message,
            "transliteration": list(self.transliteration),
            "duration_ms": self.duration_ms,
            "queue_position": self.queue_position,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class FinalOutputTask:
    trace_id: str
    generation: int
    source: PipelineSource
    original_message: str
    source_language: str
    original_transliteration: tuple[dict[str, str], ...]
    targets: tuple[TranslationTarget, ...]
    translations: tuple[TranslationUpdate, ...]
    output_config: OutputConfigSnapshot
    started_at_monotonic: float
