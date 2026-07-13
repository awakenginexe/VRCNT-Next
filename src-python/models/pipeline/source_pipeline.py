"""Per-source progressive translation and output scheduling.

The scheduler deliberately keeps provider work and final output work off the
transcription callback.  A submission publishes its initial progressive state
synchronously, then only mutates aggregation state or offers bounded work.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import logging
from queue import Empty, Full, Queue
from threading import Condition, Event, Lock, RLock, Thread, current_thread
from time import monotonic, time
from typing import Callable, Optional

from .latest_queue import LatestQueue, QueueClosed
from .pipeline_types import (
    FinalOutputTask,
    PipelineSource,
    PipelineStatusEvent,
    TranscriptionTrace,
    TranslationAttempt,
    TranslationJob,
    TranslationStatus,
    TranslationUpdate,
)


MAX_TRANSLATION_JOBS_PER_SOURCE = 8
MAX_OUTPUT_TASKS_PER_SOURCE = 4
MAX_ACTIVE_TRACES_PER_SOURCE = 16
WORKER_POLL_SECONDS = 0.1
PROVIDER_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)


class _LifecycleState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class _TraceRecord:
    trace: TranscriptionTrace
    target_slots: tuple[str, ...]
    lock: Lock = field(default_factory=Lock)
    translations: dict[str, TranslationUpdate] = field(default_factory=dict)
    terminal_slots: set[str] = field(default_factory=set)
    ready: bool = False
    final_submitted: bool = False


class SourcePipeline:
    """Own one translation worker and one output worker for a pipeline source."""

    def __init__(
        self,
        source: PipelineSource,
        translator: object,
        transliterate: Callable[[str, str, object], tuple[dict[str, str], ...]],
        emit_initial: Callable[[TranscriptionTrace], None],
        emit_update: Callable[[TranslationUpdate], None],
        emit_metric: Callable[[PipelineStatusEvent], None],
        emit_final: Callable[[FinalOutputTask], None],
        is_generation_current: Callable[[int], bool],
    ) -> None:
        self.source = source
        self._translator = translator
        self._transliterate = transliterate
        self._emit_initial = emit_initial
        self._emit_update = emit_update
        self._emit_metric = emit_metric
        self._emit_final = emit_final
        self._is_generation_current_callback = is_generation_current

        self._translation_queue: LatestQueue[TranslationJob] = LatestQueue(
            MAX_TRANSLATION_JOBS_PER_SOURCE
        )
        self._output_queue: Queue[object] = Queue(MAX_OUTPUT_TASKS_PER_SOURCE)
        self._ready_event = Event()
        self._stop_event = Event()
        self._output_stop_sentinel = object()

        self._lifecycle_condition = Condition(RLock())
        self._lifecycle_state = _LifecycleState.NEW
        self._stopping_generation: Optional[int] = None
        self._translation_exited = False
        self._output_exited = False
        self._shutdown_cleanup_complete = False
        self._submit_lock = Lock()
        self._records_lock = Lock()
        self._records: dict[str, _TraceRecord] = {}
        self._generation: Optional[int] = None
        self._accepting = False
        self._dropped_count = 0

        self._translation_thread: Optional[Thread] = None
        self._output_thread: Optional[Thread] = None

    def start(self, generation: int) -> None:
        """Start the source's two daemon workers for one generation."""
        with self._lifecycle_condition:
            if self._lifecycle_state is not _LifecycleState.NEW:
                raise RuntimeError("source pipeline has already been started")
            self._lifecycle_state = _LifecycleState.STARTING
            self._generation = generation
            self._accepting = False
            self._translation_exited = False
            self._output_exited = False
            self._shutdown_cleanup_complete = False
            self._translation_thread = Thread(
                target=self._translation_worker,
                name=f"{self.source.value}-translation-{generation}",
                daemon=True,
            )
            self._output_thread = Thread(
                target=self._output_worker,
                name=f"{self.source.value}-output-{generation}",
                daemon=True,
            )
            translation_thread = self._translation_thread
            output_thread = self._output_thread
            self._lifecycle_condition.notify_all()

        started_threads: list[Thread] = []
        try:
            output_thread.start()
            started_threads.append(output_thread)
            translation_thread.start()
            started_threads.append(translation_thread)
        except Exception:
            with self._lifecycle_condition:
                self._accepting = False
                self._generation = None
                self._stopping_generation = generation
                self._lifecycle_state = _LifecycleState.STOPPING
                self._stop_event.set()
                self._lifecycle_condition.notify_all()
            self._translation_queue.close()
            self._ready_event.set()
            for thread in started_threads:
                thread.join()
            with self._lifecycle_condition:
                self._lifecycle_state = _LifecycleState.STOPPED
                self._lifecycle_condition.notify_all()
            raise

        with self._lifecycle_condition:
            self._accepting = True
            self._lifecycle_state = _LifecycleState.RUNNING
            self._lifecycle_condition.notify_all()

    def stop(self, generation: int, discard_pending: bool = True) -> None:
        """Close intake, invalidate the generation, wake workers, and join them.

        Provider and finalizer callbacks cannot be cancelled safely.  If either is
        already running, this method waits for that callback to return before the
        corresponding worker can exit.  ``discard_pending=False`` is intentionally
        unsupported: invalidation is cancellation, not a graceful-drain promise.
        """
        if not discard_pending:
            raise ValueError("discard_pending=False is not supported")

        initiator = False
        while not initiator:
            with self._lifecycle_condition:
                while self._lifecycle_state is _LifecycleState.STARTING:
                    self._lifecycle_condition.wait()
                if self._lifecycle_state is _LifecycleState.STOPPING:
                    if self._stopping_generation != generation:
                        return
                    if current_thread() in (
                        self._translation_thread,
                        self._output_thread,
                    ):
                        return
                    while self._lifecycle_state is not _LifecycleState.STOPPED:
                        self._lifecycle_condition.wait()
                    return
                if self._lifecycle_state is _LifecycleState.STOPPED:
                    return
                if (
                    self._lifecycle_state is not _LifecycleState.RUNNING
                    or self._generation != generation
                ):
                    return

            # Serialize only the state transition with admission/job offers.
            # The lifecycle condition is never held while waiting on this lock.
            with self._submit_lock:
                with self._lifecycle_condition:
                    if (
                        self._lifecycle_state is _LifecycleState.RUNNING
                        and self._generation == generation
                    ):
                        self._accepting = False
                        self._generation = None
                        self._stopping_generation = generation
                        self._lifecycle_state = _LifecycleState.STOPPING
                        self._stop_event.set()
                        self._lifecycle_condition.notify_all()
                        initiator = True

        self._translation_queue.close()
        self._ready_event.set()
        if discard_pending:
            self._translation_queue.drain()

        translation_thread = self._translation_thread
        if (
            translation_thread is not None
            and translation_thread is not current_thread()
        ):
            translation_thread.join()

        # No translation producer remains after this point, so draining and
        # adding the wake sentinel cannot race with a stale final-task put.
        if discard_pending:
            self._drain_output_queue()
        output_thread = self._output_thread
        if output_thread is not None and output_thread.is_alive():
            try:
                self._output_queue.put_nowait(self._output_stop_sentinel)
            except Full:
                # A full queue means the output worker is already runnable.  It
                # polls the stop flag after the in-flight finalizer returns.
                pass
        if output_thread is not None and output_thread is not current_thread():
            output_thread.join()

        with self._records_lock:
            self._records.clear()
        with self._lifecycle_condition:
            self._shutdown_cleanup_complete = True
            self._finish_stop_if_workers_exited_locked()

    def _worker_exited(self, worker: str) -> None:
        with self._lifecycle_condition:
            if worker == "translation":
                self._translation_exited = True
            else:
                self._output_exited = True
            self._finish_stop_if_workers_exited_locked()

    def _finish_stop_if_workers_exited_locked(self) -> None:
        if (
            self._lifecycle_state is _LifecycleState.STOPPING
            and self._shutdown_cleanup_complete
            and self._translation_exited
            and self._output_exited
        ):
            self._lifecycle_state = _LifecycleState.STOPPED
            self._lifecycle_condition.notify_all()

    def submit_trace(self, trace: TranscriptionTrace) -> bool:
        """Synchronously publish a trace and non-blockingly schedule its slots."""
        if not self._external_generation_current(trace.generation):
            return False
        record: Optional[_TraceRecord] = None
        with self._submit_lock:
            if not self._can_accept(trace.generation):
                return False
            trace = self._snapshot_trace(trace)

            with self._records_lock:
                duplicate_trace = trace.trace_id in self._records
                over_capacity = len(self._records) >= MAX_ACTIVE_TRACES_PER_SOURCE
                if not duplicate_trace and not over_capacity:
                    record = _TraceRecord(
                        trace=trace,
                        target_slots=tuple(target.target_slot for target in trace.targets),
                    )
                    self._records[trace.trace_id] = record

        if duplicate_trace:
            self._reject_duplicate_trace(trace)
            return False

        try:
            self._emit_initial(deepcopy(trace))
        except Exception:
            if record is not None:
                self._remove_record(trace.trace_id, record)
            raise

        if over_capacity:
            self._reject_over_capacity(trace)
            return False

        if record is None or not self._record_is_current(record):
            return False

        if not trace.output_config.translation_enabled or not trace.targets:
            with record.lock:
                record.ready = True
            self._ready_event.set()
            return True

        queued_updates: list[TranslationUpdate] = []
        jobs: list[TranslationJob] = []
        with self._submit_lock:
            if (
                not self._is_locally_current(trace.generation)
                or not self._record_is_active(record)
            ):
                self._remove_record(trace.trace_id, record)
                return False
            providers = tuple(trace.providers[:2])
            base_position = self._translation_queue.qsize()
            for slot_order, target in enumerate(trace.targets, start=1):
                queued = TranslationUpdate(
                    trace_id=trace.trace_id,
                    target_slot=target.target_slot,
                    status=TranslationStatus.QUEUED,
                    engine=None,
                    message=None,
                    transliteration=(),
                    duration_ms=None,
                    queue_position=base_position + slot_order,
                    error_code=None,
                )
                queued_updates.append(self._store_update(record, queued))
                jobs.append(
                    TranslationJob(
                        trace_id=trace.trace_id,
                        generation=trace.generation,
                        source=trace.source,
                        original_message=trace.original_message,
                        source_language=trace.source_language,
                        target=target,
                        providers=providers,
                        ctranslate2_weight_type=trace.ctranslate2_weight_type,
                        context_history=tuple(deepcopy(trace.context_history)),
                        enqueued_at_monotonic=monotonic(),
                    )
                )

        for update in queued_updates:
            self._safe_emit_update(update)
            if not self._record_is_current(record):
                self._remove_record(trace.trace_id, record)
                return False

        if not providers:
            for job in jobs:
                update = TranslationUpdate(
                    trace_id=job.trace_id,
                    target_slot=job.target.target_slot,
                    status=TranslationStatus.ERROR,
                    engine=None,
                    message=None,
                    transliteration=(),
                    duration_ms=0,
                    queue_position=0,
                    error_code="no_provider_configured",
                )
                if not self._publish_terminal(
                    record,
                    job,
                    update,
                    queue_depth=self._translation_queue.qsize(),
                ):
                    self._remove_record(trace.trace_id, record)
                    return False
            return True

        dropped_jobs: list[tuple[TranslationJob, int]] = []
        with self._submit_lock:
            if (
                not self._is_locally_current(trace.generation)
                or not self._record_is_active(record)
            ):
                self._remove_record(trace.trace_id, record)
                return False
            for job in jobs:
                result = self._translation_queue.offer(job)
                if not result.accepted:
                    self._remove_record(trace.trace_id, record)
                    return False
                if result.dropped is not None:
                    dropped_jobs.append((result.dropped, result.depth))
        for dropped, depth in dropped_jobs:
            if not self._drop_waiting_job(dropped, depth):
                return False
        self._ready_event.set()
        return True

    def _can_accept(self, generation: int) -> bool:
        with self._lifecycle_condition:
            return (
                self._lifecycle_state is _LifecycleState.RUNNING
                and self._accepting
                and self._generation == generation
                and not self._stop_event.is_set()
            )

    def _is_locally_current(self, generation: int) -> bool:
        with self._lifecycle_condition:
            return (
                self._lifecycle_state is _LifecycleState.RUNNING
                and self._generation == generation
                and not self._stop_event.is_set()
            )

    def _external_generation_current(self, generation: int) -> bool:
        try:
            return bool(self._is_generation_current_callback(generation))
        except Exception:
            return False

    def _is_current(self, generation: int) -> bool:
        if not self._is_locally_current(generation):
            return False
        if not self._external_generation_current(generation):
            return False
        return self._is_locally_current(generation)

    def _record_is_current(self, record: _TraceRecord) -> bool:
        return self._is_current(
            record.trace.generation
        ) and self._record_is_active(record)

    def _job_is_current(self, record: _TraceRecord, job: TranslationJob) -> bool:
        if not self._record_is_current(record):
            return False
        with record.lock:
            return (
                not record.final_submitted
                and job.target.target_slot not in record.terminal_slots
            )

    def _reject_over_capacity(self, trace: TranscriptionTrace) -> None:
        for target in trace.targets:
            self._safe_emit_update(
                TranslationUpdate(
                    trace_id=trace.trace_id,
                    target_slot=target.target_slot,
                    status=TranslationStatus.SKIPPED_OVERLOAD,
                    engine=None,
                    message=None,
                    transliteration=(),
                    duration_ms=0,
                    queue_position=0,
                    error_code="active_trace_limit",
                )
            )
            if not self._is_current(trace.generation):
                return
        if not self._is_current(trace.generation):
            return
        self._safe_emit_metric(
            self._metric(
                trace_id=trace.trace_id,
                stage="output",
                engine=None,
                target_slot=None,
                outcome=TranslationStatus.SKIPPED_OVERLOAD.value,
                queue_age_ms=0,
                duration_ms=0,
                queue_depth=self._output_queue.qsize(),
                error_code="active_trace_limit",
            )
        )

    def _reject_duplicate_trace(self, trace: TranscriptionTrace) -> None:
        self._safe_emit_metric(
            self._metric(
                trace_id=trace.trace_id,
                stage="admission",
                engine=None,
                target_slot=None,
                outcome=TranslationStatus.ERROR.value,
                queue_age_ms=0,
                duration_ms=0,
                queue_depth=self._translation_queue.qsize(),
                error_code="duplicate_trace_id",
            )
        )

    def _drop_waiting_job(self, job: TranslationJob, queue_depth: int) -> bool:
        with self._lifecycle_condition:
            self._dropped_count += 1
        record = self._get_record(job.trace_id)
        if record is None:
            return True
        if not self._job_is_current(record, job):
            self._remove_record(job.trace_id, record)
            return False
        update = TranslationUpdate(
            trace_id=job.trace_id,
            target_slot=job.target.target_slot,
            status=TranslationStatus.SKIPPED_OVERLOAD,
            engine=None,
            message=None,
            transliteration=(),
            duration_ms=0,
            queue_position=0,
            error_code="translation_queue_overload",
        )
        return self._publish_terminal(record, job, update, queue_depth=queue_depth)

    def _store_update(
        self,
        record: _TraceRecord,
        update: TranslationUpdate,
    ) -> TranslationUpdate:
        stored = deepcopy(update)
        with record.lock:
            record.translations[stored.target_slot] = stored
        return stored

    def _publish_update(
        self,
        record: _TraceRecord,
        update: TranslationUpdate,
    ) -> TranslationUpdate:
        stored = self._store_update(record, update)
        self._safe_emit_update(stored)
        return stored

    def _publish_terminal(
        self,
        record: _TraceRecord,
        job: TranslationJob,
        update: TranslationUpdate,
        *,
        queue_depth: int,
    ) -> bool:
        stored = self._store_update(record, update)
        self._safe_emit_update(stored)
        if not self._job_is_current(record, job):
            return False
        self._emit_translation_metric(job, stored, queue_depth=queue_depth)
        if not self._job_is_current(record, job):
            return False
        self._mark_terminal(record, stored.target_slot)
        return True

    def _mark_terminal(self, record: _TraceRecord, target_slot: str) -> None:
        became_ready = False
        with record.lock:
            record.terminal_slots.add(target_slot)
            if len(record.terminal_slots) == len(record.target_slots):
                record.ready = True
                became_ready = True
        if became_ready:
            self._ready_event.set()

    def _translation_worker(self) -> None:
        try:
            self._translation_worker_loop()
        finally:
            self._worker_exited("translation")

    def _translation_worker_loop(self) -> None:
        while True:
            self._ready_event.clear()
            self._flush_ready_records()
            if self._stop_event.is_set():
                break
            try:
                job = self._translation_queue.get_nowait()
            except Empty:
                self._ready_event.wait(WORKER_POLL_SECONDS)
                self._flush_ready_records()
                continue
            except QueueClosed:
                break

            if not self._is_current(job.generation):
                self._remove_record(job.trace_id)
                continue
            record = self._get_record(job.trace_id)
            if record is None:
                continue
            if not self._job_is_current(record, job):
                continue

            self._run_translation_job(record, job)
            self._flush_ready_records()

        # A stop invalidates the generation, so this scan only removes stale
        # ready records and never puts new output work.
        self._flush_ready_records()

    def _run_translation_job(self, record: _TraceRecord, job: TranslationJob) -> None:
        providers = tuple(job.providers[:2])
        for provider_index, provider in enumerate(providers):
            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return

            sending = TranslationUpdate(
                trace_id=job.trace_id,
                target_slot=job.target.target_slot,
                status=TranslationStatus.SENDING,
                engine=provider,
                message=None,
                transliteration=(),
                duration_ms=None,
                queue_position=0,
                error_code=None,
            )
            self._publish_update(record, sending)
            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return
            try:
                attempt = self._translator.translateAttempt(
                    translator_name=provider,
                    weight_type=job.ctranslate2_weight_type,
                    source_language=job.source_language,
                    target_language=job.target.language,
                    target_country=job.target.country,
                    message=job.original_message,
                    context_history=list(deepcopy(job.context_history)),
                    timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
                )
            except Exception:
                attempt = TranslationAttempt(
                    status=TranslationStatus.ERROR,
                    engine=provider,
                    message=None,
                    duration_ms=0,
                    error_code="provider_error",
                )

            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return

            if attempt.status is TranslationStatus.SUCCESS and attempt.message is not None:
                tokens = self._safe_transliterate(
                    attempt.message,
                    job.target.language,
                    record.trace.output_config,
                )
                if not self._job_is_current(record, job):
                    self._remove_record(job.trace_id, record)
                    return
                success = TranslationUpdate(
                    trace_id=job.trace_id,
                    target_slot=job.target.target_slot,
                    status=TranslationStatus.SUCCESS,
                    engine=attempt.engine,
                    message=attempt.message,
                    transliteration=tokens,
                    duration_ms=attempt.duration_ms,
                    queue_position=0,
                    error_code=None,
                )
                if not self._publish_terminal(
                    record,
                    job,
                    success,
                    queue_depth=self._translation_queue.qsize(),
                ):
                    self._remove_record(job.trace_id, record)
                return

            failure_status = (
                attempt.status
                if attempt.status in (TranslationStatus.TIMEOUT, TranslationStatus.ERROR)
                else TranslationStatus.ERROR
            )
            failure = TranslationUpdate(
                trace_id=job.trace_id,
                target_slot=job.target.target_slot,
                status=failure_status,
                engine=attempt.engine,
                message=None,
                transliteration=(),
                duration_ms=attempt.duration_ms,
                queue_position=0,
                error_code=attempt.error_code or "provider_error",
            )
            last_provider = provider_index == len(providers) - 1
            if last_provider:
                if not self._publish_terminal(
                    record,
                    job,
                    failure,
                    queue_depth=self._translation_queue.qsize(),
                ):
                    self._remove_record(job.trace_id, record)
                return
            self._publish_update(record, failure)
            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return
            self._emit_translation_metric(
                job,
                failure,
                queue_depth=self._translation_queue.qsize(),
            )
            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return

            fallback = TranslationUpdate(
                trace_id=job.trace_id,
                target_slot=job.target.target_slot,
                status=TranslationStatus.FALLBACK,
                engine=providers[provider_index + 1],
                message=None,
                transliteration=(),
                duration_ms=None,
                queue_position=0,
                error_code=None,
            )
            self._publish_update(record, fallback)
            if not self._job_is_current(record, job):
                self._remove_record(job.trace_id, record)
                return

    def _flush_ready_records(self) -> None:
        self._ready_event.clear()
        with self._records_lock:
            records = list(self._records.values())
        for record in records:
            generation_current = self._is_current(record.trace.generation)
            with record.lock:
                if not record.ready or record.final_submitted:
                    continue
                if not generation_current:
                    stale = True
                    task = None
                else:
                    stale = False
                    record.final_submitted = True
                    task = FinalOutputTask(
                        trace_id=record.trace.trace_id,
                        generation=record.trace.generation,
                        source=record.trace.source,
                        original_message=record.trace.original_message,
                        source_language=record.trace.source_language,
                        original_transliteration=record.trace.original_transliteration,
                        targets=record.trace.targets,
                        translations=tuple(
                            record.translations[slot]
                            for slot in record.target_slots
                            if slot in record.translations
                        ),
                        output_config=record.trace.output_config,
                        started_at_monotonic=record.trace.started_at_monotonic,
                    )
            if stale:
                self._remove_record(record.trace.trace_id, record)
                continue
            while self._is_current(task.generation):
                try:
                    self._output_queue.put(task, timeout=WORKER_POLL_SECONDS)
                    break
                except Full:
                    continue
            else:
                self._remove_record(record.trace.trace_id, record)

    def _output_worker(self) -> None:
        try:
            self._output_worker_loop()
        finally:
            self._worker_exited("output")

    def _output_worker_loop(self) -> None:
        while True:
            try:
                task = self._output_queue.get(timeout=WORKER_POLL_SECONDS)
            except Empty:
                if self._stop_event.is_set():
                    break
                continue
            if task is self._output_stop_sentinel:
                break
            if not isinstance(task, FinalOutputTask):
                continue
            if not self._task_is_current(task):
                self._remove_record(task.trace_id)
                continue

            self._safe_emit_metric(
                self._metric(
                    trace_id=task.trace_id,
                    stage="output",
                    engine=None,
                    target_slot=None,
                    outcome="running",
                    queue_age_ms=None,
                    duration_ms=None,
                    queue_depth=self._output_queue.qsize(),
                    error_code=None,
                )
            )
            if not self._task_is_current(task):
                self._remove_record(task.trace_id)
                continue
            outcome = "success"
            error_code = None
            try:
                self._emit_final(deepcopy(task))
            except Exception:
                outcome = "error"
                error_code = "output_error"
            finally:
                duration_ms = max(0, round((monotonic() - task.started_at_monotonic) * 1000))
                try:
                    if self._task_is_current(task):
                        self._safe_emit_metric(
                            self._metric(
                                trace_id=task.trace_id,
                                stage="output",
                                engine=None,
                                target_slot=None,
                                outcome=outcome,
                                queue_age_ms=None,
                                duration_ms=duration_ms,
                                queue_depth=self._output_queue.qsize(),
                                error_code=error_code,
                            )
                        )
                finally:
                    self._remove_record(task.trace_id)

    def _emit_translation_metric(
        self,
        job: TranslationJob,
        update: TranslationUpdate,
        *,
        queue_depth: int,
    ) -> None:
        self._safe_emit_metric(
            self._metric(
                trace_id=job.trace_id,
                stage="translation",
                engine=update.engine,
                target_slot=job.target.target_slot,
                outcome=update.status.value,
                queue_age_ms=max(0, round((monotonic() - job.enqueued_at_monotonic) * 1000)),
                duration_ms=update.duration_ms,
                queue_depth=queue_depth,
                error_code=update.error_code,
            )
        )

    def _task_is_current(self, task: FinalOutputTask) -> bool:
        if not self._is_current(task.generation):
            return False
        record = self._get_record(task.trace_id)
        return record is not None and record.trace.generation == task.generation

    def _safe_transliterate(
        self,
        message: str,
        language: str,
        output_config: object,
    ) -> tuple[dict[str, str], ...]:
        try:
            result = self._transliterate(
                message,
                language,
                deepcopy(output_config),
            )
            return tuple(deepcopy(result))
        except Exception:
            logger.exception("translation transliteration callback failed")
            return ()

    def _safe_emit_update(self, update: TranslationUpdate) -> None:
        try:
            self._emit_update(deepcopy(update))
        except Exception:
            logger.exception("translation update callback failed")

    def _safe_emit_metric(self, metric: PipelineStatusEvent) -> None:
        try:
            self._emit_metric(deepcopy(metric))
        except Exception:
            logger.exception("pipeline metric callback failed")

    def _metric(
        self,
        *,
        trace_id: Optional[str],
        stage: str,
        engine: Optional[str],
        target_slot: Optional[str],
        outcome: str,
        queue_age_ms: Optional[int],
        duration_ms: Optional[int],
        queue_depth: int,
        error_code: Optional[str],
    ) -> PipelineStatusEvent:
        with self._lifecycle_condition:
            dropped_count = self._dropped_count
        return PipelineStatusEvent(
            schema_version=1,
            trace_id=trace_id,
            source=self.source,
            stage=stage,
            engine=engine,
            target_slot=target_slot,
            outcome=outcome,
            queue_age_ms=queue_age_ms,
            duration_ms=duration_ms,
            queue_depth=queue_depth,
            dropped_count=dropped_count,
            observed_at_ms=round(time() * 1000),
            error_code=error_code,
        )

    def _get_record(self, trace_id: str) -> Optional[_TraceRecord]:
        with self._records_lock:
            return self._records.get(trace_id)

    def _record_is_active(self, record: _TraceRecord) -> bool:
        with self._records_lock:
            return self._records.get(record.trace.trace_id) is record

    @staticmethod
    def _snapshot_trace(trace: TranscriptionTrace) -> TranscriptionTrace:
        seen_slots: set[str] = set()
        unique_targets = []
        for target in trace.targets:
            if target.target_slot in seen_slots:
                continue
            seen_slots.add(target.target_slot)
            unique_targets.append(target)
        return TranscriptionTrace(
            trace_id=trace.trace_id,
            generation=trace.generation,
            source=trace.source,
            original_message=trace.original_message,
            source_language=trace.source_language,
            original_transliteration=tuple(deepcopy(trace.original_transliteration)),
            targets=tuple(unique_targets),
            providers=tuple(trace.providers),
            ctranslate2_weight_type=trace.ctranslate2_weight_type,
            context_history=tuple(deepcopy(trace.context_history)),
            started_at_monotonic=trace.started_at_monotonic,
            output_config=deepcopy(trace.output_config),
        )

    def _remove_record(
        self,
        trace_id: str,
        expected: Optional[_TraceRecord] = None,
    ) -> None:
        with self._records_lock:
            current = self._records.get(trace_id)
            if current is not None and (expected is None or current is expected):
                del self._records[trace_id]

    def _drain_output_queue(self) -> None:
        while True:
            try:
                self._output_queue.get_nowait()
            except Empty:
                return
