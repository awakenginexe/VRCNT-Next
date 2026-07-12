# Real-time Transcription Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Whisper transcription remain responsive beside VRChat, display recognized text immediately, update cloud translations in place, and expose the stage that is slow.

**Architecture:** Audio capture, Whisper inference, translation, and final side effects become separately owned stages. Each source uses a bounded newest-first audio queue, both sources lease one serialized Whisper runtime when their configuration matches, and each source owns an ordered translation worker plus a bounded output worker. The frontend inserts the original transcript on the existing source endpoint, patches translation slots by `trace_id`, and renders event-driven latency status.

**Tech Stack:** Python 3, `threading`, `queue`/`collections.deque`, faster-whisper/CTranslate2, SpeechRecognition/PyAudioWPatch, React 18, Jotai, Sass modules, `node:test`, Python `unittest`, Vite, PyInstaller.

## Global Constraints

- The approved design is [2026-07-13-realtime-transcription-pipeline-design.md](../specs/2026-07-13-realtime-transcription-pipeline-design.md). Do not change its user-visible behavior without a new design decision.
- Preserve the existing microphone and speaker message endpoints. Progressive fields are additive, and legacy message payloads must still render.
- Use `source: "mic" | "speaker"` everywhere. Do not introduce `"microphone"` as a second spelling.
- Generate trace IDs with `uuid.uuid4()` and prefix them with the source. They must remain unique if the Python sidecar restarts while the UI stays alive.
- Never perform translation, OSC, overlay, clipboard, websocket, history, or logging work on the transcription worker.
- Google and Bing receive their native `timeout=5.0` per HTTP request. Do not wrap an attempt in an abandoned future or add a retry inside the provider.
- A job snapshots at most two configured providers: primary plus one alternate. CTranslate2 is attempted only when explicitly present in that snapshot.
- If a primary attempt fails and an alternate exists, emit the primary failure to `/run/pipeline_status`, emit `fallback` to the translation-update route, and reserve terminal `timeout`/`error` translation updates for the last attempt.
- Queue bounds are audio `4`, translation jobs `8`, and final output `4`, per source. Audio and translation queues replace the oldest unstarted item. A coalescing `Event` wakes finalization and carries no queued data. Only the translation worker may wait for output capacity; `submit_trace()` never touches the output queue, so output backpressure cannot block capture or Whisper.
- Per-source aggregation records are capped at `16`. If a non-cooperative output sink exhausts that cap, the newest transcript is still emitted immediately but its translation entries and output metric become `skipped_overload`; it is not admitted for final side effects. Every admitted final task is executed at most once, and overload is never silent.
- The shared Whisper inference lock stays held until the lazy faster-whisper segment iterator has been fully materialized.
- A runtime configuration transition permits only one active Whisper runtime key globally. The previous inference and every lease must end before replacement model loading starts.
- `stage: "output"` terminal status uses `duration_ms` for total trace duration. Other stages use their own operation duration. All durations are calculated from `time.perf_counter()`; `observed_at_ms` uses wall-clock milliseconds only for event ordering.
- End-to-end time begins at the first `AudioChunk.captured_at_monotonic` retained for the phrase, before queueing and Whisper. `AudioTranscriber` propagates that value as `started_at_monotonic`; trace creation must not reset the total-time clock after Whisper finishes.
- Pipeline status events must never contain original or translated text.
- Use `threading.Event` barriers and fake providers/models in unit tests. Do not use real cloud requests, model downloads, GPU inference, or timing sleeps to prove concurrency.
- Each Python test file must insert `src-python` into `sys.path` and stub unavailable native/optional modules before importing production code, following `src-python/tests/test_sensevoice_download.py`.
- Preserve existing typed-message behavior and all final OSC, overlay, clipboard, websocket, logger, telemetry, and history behavior.
- Add no Python or JavaScript dependency, do not upgrade faster-whisper/CTranslate2/CUDA, do not add multiprocessing, and do not require model redownloads.
- Every backend change must remain safe for the existing Windows PyInstaller CPU/CUDA sidecars and thread-based process model.
- Make one focused commit after each task passes its focused tests. Do not include unrelated user changes.

### Pipeline status emission matrix

| Stage | Required outcomes | Required fields |
| --- | --- | --- |
| `capture` | `running`, `error`, `recovered` | source, duration when complete, drop count |
| `queue` | `waiting`, `success`, `skipped_overload` | source, queue age/depth, drop count; `success` is emitted when a queued item is dequeued |
| `transcription` | `running`, `success`, `error`, `recovered` | source, engine, queue age, duration |
| `translation` | `sending`, `fallback`, `success`, `timeout`, `error`, `skipped_overload` | trace, target slot, engine, queue age/depth, duration/error |
| `output` | `running`, `success`, `error`, `skipped_overload` | trace, source, total duration on terminal event or explicit admission failure |

An output exception is caught per task, emits `stage:"output", outcome:"error"`, and the worker continues with the next task. A successful finalizer emits exactly one output success event. Active frontend health becomes `slow` after 2,000ms; the backend does not need a timer thread solely to emit `slow`.

---

## Task 1: Add typed pipeline primitives and bounded newest-first queues

**Files:**

- Create: `src-python/models/pipeline/__init__.py`
- Create: `src-python/models/pipeline/pipeline_types.py`
- Create: `src-python/models/pipeline/latest_queue.py`
- Create: `src-python/tests/test_latest_queue.py`
- Create: `src-python/tests/test_pipeline_metrics.py`

**Interfaces:**

- `LatestQueue[T](maxsize)` exposes `offer`, `get`, `get_nowait`, `qsize`, `empty`, `drain`, and `close`; only `offer` replaces the oldest item and never blocks.
- `AudioChunk` carries raw bytes, speech wall time, and capture monotonic time. Until Task 5 it remains two-value iterable compatibility for the existing transcriber.
- `TranslationUpdate.to_payload()` is the only progressive-update serializer; internal target/config fields never leak accidentally.
- `PipelineStatusEvent.to_payload()` is the only metrics serializer and cannot contain transcript text.
- `TranscriptionTrace` and `FinalOutputTask` carry the same capture-derived `started_at_monotonic` and stable source generation.
- `OutputConfigSnapshot` deep-copies all source/destination language slots, including disabled slots and countries. Finalizers read only service liveness (websocket/overlay still alive) and generation from live state; output flags, formats, and payload metadata come from the snapshot.

- [ ] **Step 1: Write failing queue-policy tests**

  Add tests that prove a four-item queue accepts without blocking, replaces the oldest item on the fifth offer, reports the dropped item and depth, supports `get_nowait()`, and wakes a blocked getter when closed.

  ```python
  class LatestQueueTests(unittest.TestCase):
      def test_offer_replaces_oldest_without_exceeding_capacity(self):
          queue = LatestQueue[int](maxsize=4)
          for value in range(4):
              self.assertIsNone(queue.offer(value).dropped)

          result = queue.offer(4)

          self.assertEqual(result.dropped, 0)
          self.assertEqual(result.depth, 4)
          self.assertEqual(queue.drain(), [1, 2, 3, 4])

      def test_close_wakes_waiting_consumer(self):
          queue = LatestQueue[int](maxsize=1)
          observed = []
          def consume():
              try:
                  queue.get()
              except QueueClosed as exc:
                  observed.append(exc)
          worker = Thread(target=consume)
          worker.start()
          queue.close()
          worker.join(timeout=1)
          self.assertFalse(worker.is_alive())
          self.assertIsInstance(observed[0], QueueClosed)
  ```

- [ ] **Step 2: Run the focused queue test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_latest_queue.py" -v
  ```

  Expected: import failure for `models.pipeline.latest_queue`.

- [ ] **Step 3: Implement `LatestQueue` with atomic replace-oldest behavior**

  Use one `Condition` around a `deque`; do not compose `queue.full()`, `get_nowait()`, and `put_nowait()` because that sequence is racy across producers.

  ```python
  T = TypeVar("T")

  class QueueClosed(Exception):
      """Raised when a consumer reads after pipeline queue shutdown."""

  @dataclass(frozen=True)
  class OfferResult(Generic[T]):
      accepted: bool
      dropped: Optional[T]
      depth: int

  class LatestQueue(Generic[T]):
      def offer(self, item: T) -> OfferResult[T]:
          with self._condition:
              if self._closed:
                  return OfferResult(False, None, len(self._items))
              dropped = self._items.popleft() if len(self._items) == self._maxsize else None
              self._items.append(item)
              self._condition.notify()
              return OfferResult(True, dropped, len(self._items))
  ```

  Implement `get(timeout=None)`, `get_nowait()`, `qsize()`, `empty()`, `drain()`, and `close()`. `get()` raises `QueueClosed` after close and `queue.Empty` on timeout.

- [ ] **Step 4: Write failing serialization and secrecy tests**

  Define and test these contracts in `pipeline_types.py`:

  ```python
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
  ```

  Assert `to_payload()` converts enum values to strings, emits every schema field, and has no keys named `message`, `original`, `translation`, or `text`.

- [ ] **Step 5: Run the metrics/type test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_metrics.py" -v
  ```

  Expected: missing pipeline enums/dataclasses/serializers fail before their implementation.

- [ ] **Step 6: Add the remaining immutable job/result dataclasses**

  Add the exact contracts below. Keep provider snapshots as tuples and make `target_slot` a string.

  ```python
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
  ```

- [ ] **Step 7: Run focused tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_latest_queue.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_metrics.py" -v
  ```

  Expected: all tests pass.

  Commit:

  ```powershell
  git add src-python/models/pipeline src-python/tests/test_latest_queue.py src-python/tests/test_pipeline_metrics.py
  git commit -m "feat: add bounded transcription pipeline primitives"
  ```

---

## Task 2: Correct speaker capture and make recorder callbacks non-blocking

**Files:**

- Modify: `src-python/models/transcription/transcription_recorder.py`
- Create: `src-python/tests/test_transcription_recorder_pipeline.py`

**Interfaces:**

- `recordIntoQueue(audio_queue, energy_queue=None, *, on_drop=None, on_heartbeat=None)` offers `AudioChunk` without waiting.
- `on_drop(displaced_chunk)` is called once per replace-oldest event; `on_heartbeat(captured_at_monotonic)` is called for audio and silent energy callbacks.
- The Task-1 `AudioChunk.__iter__` compatibility keeps current `audio, time_spoken = audio_queue.get()` working until Task 5 switches to named fields.

- [ ] **Step 1: Write a failing constructor regression test**

  Stub `Microphone` and capture the successful constructor arguments for both `SelectedSpeakerRecorder` and `SelectedSpeakerEnergyAndAudioRecorder`.

  ```python
  self.assertTrue(kwargs["speaker"])
  self.assertEqual(kwargs["device_index"], 7)
  self.assertEqual(kwargs["sample_rate"], 48_000)
  self.assertEqual(kwargs["channels"], 2)
  self.assertNotIn("chunk_size", kwargs)
  ```

  The test must fail against the current two-frame `chunk_size=get_sample_size(paInt16)` code.

- [ ] **Step 2: Run the constructor test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_recorder_pipeline.py" -v
  ```

  Expected: two assertions report that `chunk_size` is present and equals `2`.

- [ ] **Step 3: Remove only the incorrect speaker `chunk_size` arguments**

  Let the pinned SpeechRecognition/PyAudioWPatch source use its 1024-frame default. Keep `speaker`, device index, sample rate, and channels unchanged. Remove the now-unused `get_sample_size` and `paInt16` import from this file if no remaining code needs it.

- [ ] **Step 4: Write failing callback and heartbeat tests**

  Cover both recorder bases. The callback must call `LatestQueue.offer(AudioChunk(data=raw, spoken_at=now, captured_at_monotonic=captured))`, return promptly when capacity is full, report a displaced chunk through `on_drop`, and call `on_heartbeat(perf_counter_value)` during silent energy callbacks even when no UI energy queue exists.

  ```python
  recorder.recordIntoQueue(
      audio_queue,
      energy_queue=None,
      on_drop=dropped.append,
      on_heartbeat=heartbeats.append,
  )
  captured_audio_callback(None, FakeAudio(b"new"))
  captured_energy_callback(0)

  self.assertEqual(audio_queue.qsize(), 4)
  self.assertEqual(dropped[0].data, b"oldest")
  self.assertEqual(len(heartbeats), 2)
  ```

  Add a compatibility assertion that unpacking `AudioChunk(data=b"audio", spoken_at=now, captured_at_monotonic=10.0)` yields `b"audio"` and `now`, preventing this task's commit from breaking the not-yet-migrated `AudioTranscriber`.

- [ ] **Step 5: Run callback/heartbeat tests and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_recorder_pipeline.py" -v
  ```

  Expected: constructor regression now passes, while bounded offers/drop callbacks/silent heartbeat assertions fail.

- [ ] **Step 6: Implement a shared offer helper and liveness callbacks**

  Add a small internal helper that supports `LatestQueue` and keeps compatibility with a conventional queue in older call sites during the migration.

  ```python
  def _offer_audio(audio_queue, chunk: AudioChunk, on_drop=None) -> None:
      if hasattr(audio_queue, "offer"):
          result = audio_queue.offer(chunk)
          if result.dropped is not None and on_drop is not None:
              on_drop(result.dropped)
          return
      try:
          audio_queue.put_nowait(chunk)
      except Full:
          displaced = audio_queue.get_nowait()
          audio_queue.put_nowait(chunk)
          if on_drop is not None:
              on_drop(displaced)
  ```

  Extend `recordIntoQueue` with keyword-only `on_drop=None` and `on_heartbeat=None`. Register the energy callback whenever either the UI energy queue or heartbeat callback is supplied. Heartbeat uses `time.perf_counter()` and never enqueues transcript content.

- [ ] **Step 7: Run focused tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_recorder_pipeline.py" -v
  ```

  Expected: all recorder tests pass without initializing a real audio device.

  Commit:

  ```powershell
  git add src-python/models/transcription/transcription_recorder.py src-python/tests/test_transcription_recorder_pipeline.py
  git commit -m "fix: bound audio capture and correct speaker buffers"
  ```

---

## Task 3: Add Whisper decoding profiles and deterministic compute resolution

**Files:**

- Modify: `src-python/config.py`
- Modify: `src-python/models/transcription/transcription_whisper.py`
- Modify: `src-python/controller.py`
- Modify: `src-python/mainloop.py`
- Create: `src-python/tests/test_whisper_decoding_profile.py`

**Interfaces:**

- `getWhisperBeamSize("fast"|"balanced"|"accurate") -> 1|2|5`; invalid/missing persisted data resolves to Balanced.
- `resolveWhisperComputeType(device, device_index, requested) -> str` is called before runtime-key construction.
- `/get/data/whisper_decoding_profile` and `/set/data/whisper_decoding_profile` exchange one lowercase profile string.
- `Controller._requestCoordinatedTranscriptionRestart()` exists in this task as a runnable delegation to `_restartActiveTranscription()` and is strengthened in Task 9 without changing callers.

- [ ] **Step 1: Write failing profile and compute-resolution tests**

  ```python
  class WhisperProfileTests(unittest.TestCase):
      def test_profile_beam_sizes(self):
          self.assertEqual(getWhisperBeamSize("fast"), 1)
          self.assertEqual(getWhisperBeamSize("balanced"), 2)
          self.assertEqual(getWhisperBeamSize("accurate"), 5)

      def test_cuda_auto_and_int8_resolve_to_int8_float16(self):
          self.assertEqual(resolveWhisperComputeType("cuda", 0, "auto"), "int8_float16")
          self.assertEqual(resolveWhisperComputeType("cuda", 0, "int8"), "int8_float16")

      def test_explicit_supported_cuda_type_is_preserved(self):
          self.assertEqual(resolveWhisperComputeType("cuda", 0, "float16"), "float16")
  ```

  Also instantiate a fresh config object and assert a missing saved key retains `"balanced"`; invalid values must be rejected or normalized to `"balanced"` through the existing `ValidatedProperty` convention.

- [ ] **Step 2: Run the focused test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_decoding_profile.py" -v
  ```

  Expected: imports or attributes for the new profile helpers fail.

- [ ] **Step 3: Implement the configuration and helper functions**

  Add a validated `WHISPER_DECODING_PROFILE` beside the transcription compute settings and initialize it to `"balanced"`.

  ```python
  WHISPER_BEAM_SIZES = {"fast": 1, "balanced": 2, "accurate": 5}

  def getWhisperBeamSize(profile: str) -> int:
      return WHISPER_BEAM_SIZES.get(str(profile).lower(), WHISPER_BEAM_SIZES["balanced"])

  def resolveWhisperComputeType(device: str, device_index: int, compute_type: str) -> str:
      requested = str(compute_type).lower()
      if str(device).lower() == "cuda" and requested in {"auto", "int8"}:
          return "int8_float16"
      if requested != "auto":
          return requested
      return getBestComputeType(device=device, device_index=device_index)
  ```

  Ensure model construction receives the resolved value, so the same value is later used as part of the runtime key.

- [ ] **Step 4: Add controller and main-loop get/set routes**

  Add:

  ```python
  def getWhisperDecodingProfile(*args, **kwargs) -> dict:
      return {"status": 200, "result": config.WHISPER_DECODING_PROFILE}

  def setWhisperDecodingProfile(self, data, *args, **kwargs) -> dict:
      config.WHISPER_DECODING_PROFILE = str(data).lower()
      self._requestCoordinatedTranscriptionRestart()
      return {"status": 200, "result": config.WHISPER_DECODING_PROFILE}
  ```

  Map `/get/data/whisper_decoding_profile` and `/set/data/whisper_decoding_profile`. Add `_requestCoordinatedTranscriptionRestart()` now as a thin call to the existing `_restartActiveTranscription()` stop-both/start-active sequence so this commit is runnable; Task 9 replaces its body with the runtime-aware serialized lifecycle.

- [ ] **Step 5: Run tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_decoding_profile.py" -v
  ```

  Expected: all profile, migration, and route tests pass.

  Commit:

  ```powershell
  git add src-python/config.py src-python/models/transcription/transcription_whisper.py src-python/controller.py src-python/mainloop.py src-python/tests/test_whisper_decoding_profile.py
  git commit -m "feat: add Whisper decoding profiles"
  ```

---

## Task 4: Create the single-owner Whisper runtime manager

**Files:**

- Create: `src-python/models/transcription/whisper_runtime.py`
- Modify: `src-python/models/transcription/transcription_whisper.py`
- Create: `src-python/tests/test_whisper_runtime.py`

**Interfaces:**

- `WhisperRuntimeManager.acquire(root, key) -> WhisperRuntimeLease` shares a matching key and raises `WhisperRuntimeBusy` for a different key while leases/inference remain.
- `WhisperRuntimeLease.transcribe(audio, **options) -> WhisperInferenceResult` serializes native inference and materializes all segments under the lock.
- `WhisperRuntimeLease.close()` is idempotent; final close waits on the manager condition while inference is active, then unloads exactly once. It can never unload an in-use model.
- `WhisperRuntimeManager.shutdown()` rejects new work, invalidates leases, waits for current inference, and explicitly unloads once.

- [ ] **Step 1: Write failing lease-sharing and serialized-inference tests**

  Inject a fake model factory and fake unload callback. Assert two matching acquisitions call the factory once, return leases with the same key, and do not unload until both leases close.

  Use an iterator that blocks on an `Event` after `transcribe()` returns. Start a second lease inference and assert it cannot enter the fake model until the first iterator is released. This proves the lock covers lazy segment materialization.

  ```python
  first = manager.acquire(root, key)
  second = manager.acquire(root, key)
  self.assertEqual(factory.call_count, 1)

  thread_a = Thread(target=lambda: first.transcribe(audio, beam_size=2))
  thread_b = Thread(target=lambda: second.transcribe(audio, beam_size=2))
  thread_a.start()
  iterator_entered.wait(1)
  thread_b.start()
  self.assertFalse(second_model_entry.is_set())
  release_iterator.set()
  ```

- [ ] **Step 2: Write failing replacement and stale-lease tests**

  While a lease for key A is open, `acquire(root, key_b)` must raise `WhisperRuntimeBusy`. Block inference A on an Event, call the final lease's `close()` on another thread, and assert close remains waiting, unload has not run, and key B cannot load. Release inference; assert close returns, unload runs exactly once, then B can load. `shutdown()` follows the same condition and makes old leases reject future `transcribe()` calls.

- [ ] **Step 3: Run the focused runtime test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_runtime.py" -v
  ```

  Expected: import failure for `whisper_runtime`.

- [ ] **Step 4: Implement manager, key, lease, and explicit unload**

  ```python
  @dataclass(frozen=True)
  class WhisperRuntimeKey:
      weight_type: str
      device: str
      device_index: int
      compute_type: str

  @dataclass(frozen=True)
  class WhisperInferenceResult:
      segments: tuple[Any, ...]
      info: Any
  ```

  Implement lease/manager methods with the signatures in the Interfaces block. Track active inference and final-close/unload state under one `Condition`; inference completion notifies final close/shutdown. Tests define lifecycle behavior, so no method may silently replace an active different key or unload before lazy iteration completes.

  `WhisperRuntimeLease.transcribe()` must execute both calls below inside the manager's inference lock:

  ```python
  segments, info = model.transcribe(audio, **options)
  materialized = tuple(segments)
  return WhisperInferenceResult(segments=materialized, info=info)
  ```

  Add `unloadWhisperModel(model)` in `transcription_whisper.py`. It calls `model.model.unload_model()` when available, then releases the wrapper reference. `gc.collect()` and `torch.cuda.empty_cache()` may remain cleanup aids but are never used as proof that CTranslate2 ownership ended.

- [ ] **Step 5: Run tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_runtime.py" -v
  ```

  Expected: one fake model for matching leases, no overlapping inference, and exactly one unload on final release/shutdown.

  Commit:

  ```powershell
  git add src-python/models/transcription/whisper_runtime.py src-python/models/transcription/transcription_whisper.py src-python/tests/test_whisper_runtime.py
  git commit -m "feat: share one serialized Whisper runtime"
  ```

---

## Task 5: Make `AudioTranscriber` consume typed chunks and a runtime lease

**Files:**

- Modify: `src-python/models/transcription/transcription_transcriber.py`
- Modify: `src-python/models/transcription/whisper_runtime.py`
- Modify: `src-python/model.py`
- Create: `src-python/tests/test_transcription_transcriber_pipeline.py`

**Interfaces:**

- `AudioTranscriber` keeps all existing positional/config arguments and adds optional `pipeline_context: TranscriberPipelineContext`.
- `TranscriberPipelineContext` contains source, runtime lease, decoding profile, generation, generation predicate, metric callback, and recovery callback.
- `request_recovery(source, generation, error_code, safe_to_restart)` only offers a control-plane request and returns; the transcriber sets the Event during final cleanup and never loads/unloads/stops a model or joins its own worker.
- `getTranscript()` adds `started_at_monotonic` to the existing confidence/text/language result.
- `QueueClosed` is a normal stop signal: `transcribeAudioQueue()` returns `False` without logging it as recognition failure.
- Model creates one `WhisperRuntimeManager`, acquires/passes leases before constructing Whisper transcribers, and closes each source lease during current stop methods. Task 9 adds generation/restart coordination without leaving this task's commit broken.

- [ ] **Step 1: Write failing transcriber-integration tests**

  Construct `AudioTranscriber` with a fake `WhisperRuntimeLease`, decoding profile, generation, and metric callback. Assert:

  - it does not call `getWhisperModel()` itself;
  - Balanced passes `beam_size=2` to the lease;
  - `AudioChunk` queue age is reported;
  - all queued chunks are drained through typed fields;
  - an inactive generation drops a completed result before `updateTranscript()`;
  - Whisper failure clears live audio but does not reload a model from inside the transcriber.
  - recovery callback returns and `transcribeAudioQueue()` exits before any restart callback begins.

- [ ] **Step 2: Run the focused test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_transcriber_pipeline.py" -v
  ```

  Expected: constructor/signature assertions fail because the transcriber still owns `whisper_model` and hard-codes beam 5.

- [ ] **Step 3: Add lease and generation dependencies**

  Add this exact dependency object so non-Whisper engines and legacy construction remain compatible:

  ```python
  @dataclass(frozen=True)
  class TranscriberPipelineContext:
      source: PipelineSource
      whisper_runtime_lease: Optional[WhisperRuntimeLease]
      whisper_decoding_profile: str
      generation: int
      is_generation_current: Callable[[int], bool]
      emit_metric: Callable[[PipelineStatusEvent], None]
      request_recovery: Callable[[PipelineSource, int, str, Event], None]
  ```

  Add `pipeline_context: Optional[TranscriberPipelineContext] = None` as the final constructor parameter. Remove `_reloadWhisperModel()` and direct Whisper ownership. On recognition failure, create `safe_to_restart = Event()`, call `request_recovery(source, generation, "whisper_inference_failed", safe_to_restart)`, and set that Event in the outer `finally` after inference/result cleanup. The coordinator may not stop/join this worker until the Event is set; the transcriber cannot load a replacement itself.

  In `model.py`, create the manager during model initialization. Resolve a `WhisperRuntimeKey`, acquire a lease before each Whisper `AudioTranscriber`, pass it through `TranscriberPipelineContext`, and close it in `stopMicTranscript`/`stopSpeakerTranscript`. Final close may wait beyond the current two-second transcription join timeout until native inference returns; this is required to prevent overlapping/unloading an active model. Matching mic/speaker keys therefore share immediately. Use a temporary non-blocking recovery recorder callback in this task; Task 9 connects it to the coordinator. Non-Whisper engines receive `whisper_runtime_lease=None` and keep their current construction.

- [ ] **Step 4: Convert queue reads and Whisper inference**

  Read `AudioChunk.data`, `.spoken_at`, and `.captured_at_monotonic`. Immediately emit `queue/success` with final queue age/depth when the chunk is dequeued, then emit `transcription/running`. Track the first retained chunk in `audio_sources["phrase_started_at_monotonic"]`; reset it only when a new phrase starts, and copy it into the transcript result. Measure queue age and inference duration with `perf_counter()`. Use the current options with only the beam source changed:

  ```python
  result = self.whisper_runtime_lease.transcribe(
      audio_data,
      beam_size=getWhisperBeamSize(self.whisper_decoding_profile),
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
  text = "".join(
      segment.text for segment in result.segments
      if segment.avg_logprob >= avg_logprob
      and segment.no_speech_prob <= no_speech_prob
  )
  ```

  Catch `QueueClosed` around `get()` and drain calls as normal shutdown. Check `is_generation_current(generation)` immediately before updating transcript data and before emitting success. Status payloads contain only timing, stage, queue depth, source, and error codes.

- [ ] **Step 5: Run tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_transcriber_pipeline.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_runtime.py" -v
  ```

  Expected: all integration and runtime tests pass.

  Commit:

  ```powershell
  git add src-python/models/transcription/transcription_transcriber.py src-python/models/transcription/whisper_runtime.py src-python/model.py src-python/tests/test_transcription_transcriber_pipeline.py
  git commit -m "refactor: lease Whisper from the transcription worker"
  ```

---

## Task 6: Add finite structured translation attempts

**Files:**

- Modify: `src-python/models/translation/translation_translator.py`
- Modify: `src-python/model.py`
- Modify: `src-python/controller.py`
- Create: `src-python/tests/test_translation_attempt.py`

**Interfaces:**

- `_translate_once(name, weight, source, target, country, message, context, timeout_seconds)` dispatches one provider call and lets exceptions propagate.
- `PROVIDER_TIMEOUT_EXCEPTIONS` is exactly `(TimeoutError, requests.exceptions.Timeout)`; provider-specific wrappers must chain one of these for timeout classification.
- `translateAttempt(name, weight, source, target, country, message, context, timeout) -> TranslationAttempt` never rotates providers or retries.
- `boundedTranslationProviderSnapshot(selection) -> tuple of provider names` preserves configured order, removes duplicates/blank values, returns at most two names, and never injects CTranslate2.
- Legacy `translate()` adapts one structured attempt to `str | False`; legacy `model.getInputTranslate/getOutputTranslate` use the bounded snapshot and contain no unbounded provider loop.
- Typed-chat failure reports its existing error but never calls `changeToCTranslate2Process()` or mutates `SELECTED_TRANSLATION_ENGINES`; CTranslate2 runs only when present in the original snapshot.

- [ ] **Step 1: Write failing Google/Bing timeout-classification tests**

  Patch the provider library call and assert both Google and Bing receive `timeout=5.0`. Raise the library's timeout exception and expect a `TranslationAttempt` with `status=TIMEOUT`, provider engine, no message, monotonic duration, and a stable `error_code` such as `provider_timeout`.

  ```python
  attempt = translator.translateAttempt(
      "Google", "", "Japanese", "English", "United States", "hello",
      timeout_seconds=5.0,
  )
  provider.assert_called_once_with(
      query_text="hello",
      translator="google",
      from_language="ja",
      to_language="en",
      timeout=5.0,
  )
  self.assertEqual(attempt.status, TranslationStatus.TIMEOUT)
  ```

  Add success, non-timeout error, and empty/False provider-result cases. Add same-language coverage: Japanese→Japanese returns a successful attempt containing the original message and the provider fake is never called. No test may make a network request.

- [ ] **Step 2: Run the focused test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  ```

  Expected: `translateAttempt` is missing and provider timeout kwargs are absent.

- [ ] **Step 3: Implement the structured single-attempt API**

  ```python
  def translateAttempt(
      self,
      translator_name: str,
      weight_type: str,
      source_language: str,
      target_language: str,
      target_country: str,
      message: str,
      context_history: Optional[list[dict]] = None,
      timeout_seconds: Optional[float] = None,
  ) -> TranslationAttempt:
      started = time.perf_counter()
      if source_language == target_language:
          return TranslationAttempt(
              status=TranslationStatus.SUCCESS,
              engine=translator_name,
              message=message,
              duration_ms=0,
              error_code=None,
          )
      try:
          translated = self._translate_once(
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
              duration_ms=round((time.perf_counter() - started) * 1000),
              error_code="provider_timeout",
          )
      except Exception:
          errorLogging()
          return TranslationAttempt(
              status=TranslationStatus.ERROR,
              engine=translator_name,
              message=None,
              duration_ms=round((time.perf_counter() - started) * 1000),
              error_code="provider_error",
          )
      return TranslationAttempt(
          status=TranslationStatus.SUCCESS if translated else TranslationStatus.ERROR,
          engine=translator_name,
          message=str(translated) if translated else None,
          duration_ms=round((time.perf_counter() - started) * 1000),
          error_code=None if translated else "empty_provider_result",
      )
  ```

  Extract the current provider `match` body into `_translate_once()`. Pass `timeout_seconds` only to Google/Bing's `_web_translator`; other providers keep their current native call signature. Keep the current `translate()` API for typed chat by adapting its structured result back to the legacy return value. Do not put provider rotation, fallback, or retry in `translateAttempt()`.

- [ ] **Step 4: Write failing legacy-loop and provider-mutation regressions**

  Add a test that legacy `translate()` still returns the translated string on success and `False` on terminal failure. Add a regression test around `model.getInputTranslate/getOutputTranslate` proving `False` cannot enter the current `while True`, no more than two configured providers are called, empty selection terminates with failure, and CTranslate2 is not auto-injected. Use a fake that raises `AssertionError` on a third call so the RED test fails quickly instead of hanging.

  Exercise `controller.chatMessage()` with two failed providers and assert `changeToCTranslate2Process()` is not called and `SELECTED_TRANSLATION_ENGINES` is unchanged.

- [ ] **Step 5: Run the legacy regressions and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  ```

  Expected: the third-call sentinel or automatic CTranslate2 mutation assertion fails quickly against current behavior.

- [ ] **Step 6: Replace unbounded fallback and automatic mutation**

  Replace the loop with finite `for provider in boundedTranslationProviderSnapshot(selection)`. Remove the automatic CTranslate2 mutation from typed chat; the explicit method may remain for any separately authorized UI action.

- [ ] **Step 7: Run all translation-attempt tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  ```

  Expected: all attempt, same-language, timeout, legacy-wrapper, finite-provider, and no-mutation tests pass.

  Commit:

  ```powershell
  git add src-python/models/translation/translation_translator.py src-python/model.py src-python/controller.py src-python/tests/test_translation_attempt.py
  git commit -m "feat: bound and classify translation attempts"
  ```

---

## Task 7: Build the per-source progressive translation scheduler

**Files:**

- Create: `src-python/models/pipeline/source_pipeline.py`
- Create: `src-python/tests/test_translation_scheduler.py`
- Create: `src-python/tests/test_progressive_transcription_pipeline.py`
- Create: `src-python/tests/test_pipeline_output_worker.py`

**Interfaces:**

- `SourcePipeline(source, translator, transliterate, emit_initial, emit_update, emit_metric, emit_final, is_generation_current)` owns exactly one translation thread and one output thread.
- `transliterate(message, language, output_config) -> tuple of ruby/romaji token dictionaries` is Model-owned; it returns an empty tuple when conversion is disabled/non-Japanese and logs then returns empty on conversion failure.
- `submit_trace(trace) -> None` emits the original payload synchronously, offers only translation jobs or sets the coalescing ready Event, and never calls a provider, output queue `put`, or final side effect.
- A coalescing `ready_for_output_event` wakes the translation thread for zero-target, no-provider, and overload-ready traces; it contains no tasks and cannot overflow or displace translation work.
- `_flush_ready_traces_to_output()` runs only on the translation thread and uses cancellable 100ms output-queue puts.
- The output worker wraps each `emit_final(task)` call with output `running` then `success|error` metrics and continues after an exception.

- [ ] **Step 1: Write a failing immediate-emission concurrency test**

  Use a fake translator blocked by an `Event`. Submit trace A, wait until translation starts, submit trace B, and assert both initial callbacks were emitted before releasing translation A. The initial payload must include original text and queued entries, while no final output exists yet.

  ```python
  pipeline.submit_trace(trace_a)
  provider_started.wait(1)
  pipeline.submit_trace(trace_b)

  self.assertEqual([p["trace_id"] for p in initial_payloads], [trace_a.trace_id, trace_b.trace_id])
  self.assertEqual(initial_payloads[0]["translations"][0]["status"], "queued")
  self.assertEqual(final_tasks, [])
  ```

- [ ] **Step 2: Write failing state-machine and fallback tests**

  Assert the per-slot update order:

  - primary success: `queued` initial payload, then `sending`, `success`;
  - primary timeout plus alternate success: `sending`, `fallback`, `sending`, `success`;
  - final timeout/error: one terminal update after the last attempt;
  - no more than two calls regardless of the global configured-provider list;
  - CTranslate2 is never injected unless selected in `trace.providers`.

  Provider failure on the first attempt must also emit an attempt failure metric before the `fallback` update.

  Add an empty-provider test: initial original text is emitted, every target gets terminal `error` with `error_code:"no_provider_configured"`, no provider is called, and the original-only final task is produced once. CTranslate2 cannot appear in either providers or updates unless selected in the trace snapshot.

  Add a Japanese-success test proving the Model-owned transliteration callback receives translated text plus the target language and its tokens are present in both the success update and final task. Non-Japanese/disabled conversion returns an empty tuple without a callback call.

- [ ] **Step 3: Write failing overload and aggregation tests**

  Fill an eight-item translation queue with the worker blocked. The ninth job displaces the oldest unstarted job, emits `skipped_overload` for that exact trace/slot, increments `dropped_count`, and keeps the original trace visible. Multiple target slots aggregate independently.

  Assert exactly one `FinalOutputTask` enters the four-item output queue after every slot is terminal. Fill the output queue, submit a zero-target trace and a trace whose displaced job becomes terminal, and assert both `submit_trace()` calls return before output capacity is released. A fake slow output worker may backpressure only the translation worker; a separate fake transcription submission must still emit its original payload.

  In `test_pipeline_output_worker.py`, make the first finalizer raise and the second succeed. Assert `output/running,error` for the first, `output/running,success` for the second, total duration is measured from `started_at_monotonic`, and the worker remains alive between them.

  Fill all 16 aggregation records while a fake output sink is blocked, submit one more trace, and assert its original event is emitted with terminal `skipped_overload`, no provider/finalizer is called for it, and an output-overload metric is emitted. This is the explicit bounded-memory admission policy.

- [ ] **Step 4: Run scheduler tests and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_scheduler.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_progressive_transcription_pipeline.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_output_worker.py" -v
  ```

  Expected: import failure for `models.pipeline.source_pipeline`.

- [ ] **Step 5: Implement `SourcePipeline` ownership and non-blocking submission**

  Use the constructor/method contracts in the Interfaces block. `start(generation)` creates daemon translation/output threads and `stop(generation, discard_pending=True)` closes work intake, invalidates the generation, wakes both workers, and joins them after in-flight fake/native work returns.

  `submit_trace()` performs only immutable aggregation setup, synchronous initial-event emission, and non-blocking translation offers. Initial `queue_position` is the current translation depth plus the slot's submission order; `sending` resets it to zero, while authoritative queue depth remains in pipeline metrics. It never invokes a provider or output queue. Before allocating a record, enforce `MAX_ACTIVE_TRACES_PER_SOURCE = 16`; an over-cap trace follows the explicit `skipped_overload` admission behavior in Global Constraints.

  Construct each `TranslationJob` from `trace.ctranslate2_weight_type` and the deep-copied `trace.context_history`; no field comes from mutable config after submission. On provider success, call the injected transliterator before constructing `TranslationUpdate`.

  When translation is disabled or there are no targets, mark the aggregation record ready and set `ready_for_output_event`. When targets exist but provider snapshot is empty, emit terminal `no_provider_configured` updates, mark slots terminal, and set the same event. When an offer displaces a job, mark its slot `skipped_overload` and set the event if its trace becomes ready; do not enqueue a final task from the caller.

  At the start/end of each translation work cycle, and after a 100ms queue wait timeout, the translation thread clears `ready_for_output_event`, scans aggregation records marked ready, and moves them to `Queue(maxsize=4)` with a `put(timeout=0.1)` loop that exits if the pipeline stop event is set or generation becomes stale. Mark `final_submitted=True` before the put loop so a retry cannot duplicate it. This preserves bounded queue memory and exactly-once processing during a live generation without coupling Whisper to final side effects.

- [ ] **Step 6: Implement per-trace aggregation under a lock**

  Maintain an internal record keyed by trace ID. Update one target slot atomically. When all slots are terminal, mark the record `ready_for_output=True`; only the translation thread converts it to one immutable `FinalOutputTask`. Preserve `targets` and match successful `TranslationUpdate` values by `target_slot`, so partial success for slot 2 can never be mislabeled with slot 1's language. A trace with zero enabled targets becomes ready immediately, wakes the translation thread, and produces one original-only final task.

  The output worker checks generation, emits `output/running`, invokes `emit_final` inside `try/except`, then emits `output/success` or `output/error` with end-to-end duration. It removes the aggregation record in `finally` and continues after failures.

- [ ] **Step 7: Run tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_scheduler.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_progressive_transcription_pipeline.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_metrics.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_output_worker.py" -v
  ```

  Expected: state order, overload displacement, provider bound, generation filtering, and one-final-task assertions all pass.

  Commit:

  ```powershell
  git add src-python/models/pipeline/source_pipeline.py src-python/tests/test_translation_scheduler.py src-python/tests/test_progressive_transcription_pipeline.py src-python/tests/test_pipeline_output_worker.py
  git commit -m "feat: schedule progressive translations per source"
  ```

---

## Task 8: Split controller callbacks into immediate traces and final side effects

**Files:**

- Modify: `src-python/controller.py`
- Modify: `src-python/model.py`
- Modify: `src-python/mainloop.py`
- Create: `src-python/tests/test_controller_progressive_pipeline.py`

**Interfaces:**

- `_beginTranscriptionTrace(source, result) -> None` validates/filter-checks the result, snapshots targets/providers/config, generates a UUID trace, and submits to the matching `SourcePipeline`.
- `_emitTranslationUpdate(update) -> None` sends `update.to_payload()` to `/run/transcription_translation_update` with transport status 200.
- `_emitPipelineStatus(event) -> None` sends `event.to_payload()` to `/run/pipeline_status` with transport status 200.
- `_finalizeMicOutput(task)` and `_finalizeSpeakerOutput(task)` consume only successful updates, join them to `task.targets` by `target_slot`, rebuild the existing complete websocket/overlay language maps from `LanguageSlotSnapshot` (including disabled slots), and perform current side effects once after a generation check.
- Provider snapshots come from `boundedTranslationProviderSnapshot()` and contain zero, one, or two explicitly configured names.
- `Model.ensureSourcePipeline(source, callbacks, generation) -> SourcePipeline` constructs/starts the source session before a transcription callback can submit; current source stop methods stop it. Task 9 adds coordinated generations/restarts without leaving this commit with a missing session.

- [ ] **Step 1: Characterize current microphone and speaker side effects**

  Before refactoring, write tests with fakes for `controller.run`, OSC, overlay, clipboard, websocket, logger/history, and translation. Capture which operations happen for microphone/send and speaker/listen success, translation failure, and original-only configuration. These tests are the regression boundary for existing user behavior.

- [ ] **Step 2: Write a failing progressive-order test**

  Feed a transcription result into each callback. Assert the existing source-specific event is the first externally visible call and carries:

  ```json
  {
    "trace_id": "speaker-550e8400-e29b-41d4-a716-446655440000",
    "original": {"message": "recognized", "transliteration": []},
    "translations": [{
      "target_slot": "1",
      "message": null,
      "transliteration": [],
      "status": "queued",
      "engine": "Google",
      "duration_ms": null
    }]
  }
  ```

  Translation and final side-effect fakes must still be untouched at that point.

- [ ] **Step 3: Run the progressive controller test and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_controller_progressive_pipeline.py" -v
  ```

  Expected: current callbacks invoke translation before emitting the source message and lack trace/queued fields.

- [ ] **Step 4: Extract exact controller boundaries**

  Add the five methods specified in Interfaces. `_beginTranscriptionTrace()` creates `f"{source.value}-{uuid.uuid4()}"`, computes optional original-language transliteration through the Model-owned callback, snapshots every enabled target slot and calls `boundedTranslationProviderSnapshot(config.SELECTED_TRANSLATION_ENGINES[config.SELECTED_TAB_NO])`, copies `CTRANSLATE2_WEIGHT_TYPE` plus context history into the trace, builds the exact `OutputConfigSnapshot` from Task 1 with all enabled/disabled language slots and countries, carries `result["started_at_monotonic"]`, and calls the appropriate source pipeline. Speaker mode now iterates all enabled “your translation” target slots, matching the approved design.

  Before changing mic/speaker callbacks to `_beginTranscriptionTrace`, wire `model.ensureSourcePipeline()` into each source start with controller emit/finalizer callbacks and the Model-owned transliterator. Stop it in the matching source stop method. Keep a test assertion that the pipeline exists and is started before the first callback; do not add a fallback to synchronous legacy translation.

  Move the current formatting, OSC, overlay, clipboard, websocket, logger, telemetry, and history statements into the two finalizers. Build `successful_pairs = [(target_by_slot[u.target_slot], u) for u in task.translations if u.status is SUCCESS]` and derive both translated strings and target-language metadata from that same list. This prevents partial slot success from being assigned to another target in overlays/websocket payloads. Preserve current original-only rules. Check generation immediately before each externally visible effect.

- [ ] **Step 5: Add unsolicited run mappings**

  Map:

  ```python
  "transcription_translation_update": "/run/transcription_translation_update",
  "pipeline_status": "/run/pipeline_status",
  ```

  `_emitTranslationUpdate()` serializes the dataclass without changing transport status from `200`. `_emitPipelineStatus()` calls `event.to_payload()` and never includes transcript fields.

- [ ] **Step 6: Run focused tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_controller_progressive_pipeline.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_progressive_transcription_pipeline.py" -v
  ```

  Expected: initial events precede provider work; final side effects match characterization and run exactly once.

  Commit:

  ```powershell
  git add src-python/controller.py src-python/model.py src-python/mainloop.py src-python/tests/test_controller_progressive_pipeline.py
  git commit -m "refactor: separate transcript display from final output"
  ```

---

## Task 9: Own sessions, shared runtime, recovery, and shutdown in the model

**Files:**

- Modify: `src-python/model.py`
- Modify: `src-python/controller.py`
- Modify: `src-python/mainloop.py`
- Create: `src-python/tests/test_pipeline_lifecycle.py`
- Create: `src-python/tests/test_pipeline_end_to_end.py`
- Modify: `src-python/tests/test_pipeline_metrics.py`

**Interfaces:**

- `Controller` owns `_transcription_restart_lock` and the only `_requestCoordinatedTranscriptionRestart(reason)` method because configuration flags/setters live there.
- `Model` owns `WhisperRuntimeManager`, per-source generation/session state, and `setTranscriptionRecoveryCallback(callback)`.
- The registered recovery callback signature is `(source: PipelineSource, generation: int, error_code: str, safe_to_restart: Event) -> None`; it only performs a non-blocking offer to Controller's four-item newest-first recovery queue.
- A dedicated Controller coordinator thread consumes recovery requests, ignores stale generations, waits for `safe_to_restart`, and only then invokes coordinated restart. A transcription worker never stops/joins itself.
- Capture-heartbeat recovery calls `Model.restartRecorder(source, generation)` and never replaces the shared runtime.
- `Model.shutdownTranscriptionPipelines()` stops both sources and the runtime; `Controller.shutdown()` calls it before telemetry/process shutdown.

- [ ] **Step 1: Write failing matching-source runtime tests**

  Start microphone and speaker with identical Whisper configuration. Assert one factory load and two leases. Stop one source and assert the runtime stays loaded. Stop the second and assert one explicit unload.

  Extend metrics tests with the full matrix: successful recorder start emits `capture/running`; recorder construction failure emits `capture/error`; a normal audio offer emits `queue/waiting`; dequeue emits `queue/success`; replace-oldest emits `queue/skipped_overload`; recorder-only heartbeat recovery emits `capture/recovered`; successful inference-recovery restart emits `transcription/recovered`. Assert every event has the correct source and null-trace source events contain no message text.

- [ ] **Step 2: Write failing coordinated restart tests**

  Block fake inference, request a weight/device/compute/profile change, and assert:

  - both old source generations stop accepting output;
  - no replacement factory call occurs while old inference is active;
  - releasing inference closes leases and unloads the old runtime;
  - exactly one replacement runtime loads;
  - active microphone/speaker selections restart once;
  - late results from the old generation emit nothing.

  Cover the existing stall watchdog: capture heartbeat failure restarts only the recorder, while inference failure requests the same coordinated runtime restart and never overlaps a model.

  Trigger inference failure on the fake transcription worker. The fake recovery request contains `safe_to_restart`; assert the coordinator remains before stop/restart until the transcription worker sets it in `finally`, then joins from the coordinator thread. Assert replacement model loading cannot begin until the old worker has actually returned.

- [ ] **Step 3: Write failing deterministic shutdown tests**

  Assert shutdown order for each source: stop recorder callback, close audio queue, join transcription worker, stop translation pipeline, drain/cancel output for the old generation, close runtime lease, then manager shutdown. Release all fake provider/finalizer Events and assert the translation and output worker objects are actually not alive after `controller.shutdown()` and the main-loop stop route; checking daemon flags alone is insufficient.

  Document the non-cooperative edge: Python cannot cancel a third-party provider that ignores its timeout. Pipeline threads are daemon threads so process exit remains possible, but normal Google/Bing paths are bounded by native five-second timeouts and every test-controlled in-flight call must terminate before a successful shutdown claim.

  In `test_pipeline_end_to_end.py`, feed trace A from a known `AudioChunk.captured_at_monotonic` through fake transcription and controller boundaries. Block fake Google after it records `timeout_seconds`, feed trace B audio, and assert B's original event arrives before Google releases. After release, assert A's same trace/slot updates, final output occurs once, and output `duration_ms` covers capture → queue → Whisper → translation → output rather than starting at trace creation. Use a fake monotonic clock and Events, not a five-second sleep.

- [ ] **Step 4: Run lifecycle tests and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_lifecycle.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_end_to_end.py" -v
  ```

  Expected: matching sources load two models, restart overlaps a blocked inference, shutdown leaves workers alive, or the end-to-end total starts after capture.

- [ ] **Step 5: Add application-owned managers and generation state**

  Reuse the single `WhisperRuntimeManager` established in Task 5; do not instantiate or replace it here. Add per-source generation counters, recorder heartbeat timestamps, and two optional `SourcePipeline` sessions to Model ownership. In controller initialization, create `_transcription_restart_lock`, `LatestQueue(maxsize=4)` for recovery requests, a stop event, and a dedicated daemon recovery-coordinator thread. Register a model recovery callback that only offers `{source,generation,error_code,safe_to_restart}`. The coordinator checks generation, waits on `safe_to_restart` in a stop-aware 100ms loop, then calls `_requestCoordinatedTranscriptionRestart(error_code)`.

  Replace unbounded `Queue()` audio creation with `LatestQueue(maxsize=4)`. Wire recorder `on_drop` to a capture overload metric and `on_heartbeat` to liveness state. Acquire a runtime lease only for Whisper; preserve existing engines.

- [ ] **Step 6: Serialize configuration changes**

  Implement:

  ```python
  def _requestCoordinatedTranscriptionRestart(self, reason: str = "configuration_changed") -> None:
      with self._transcription_restart_lock:
          active = self._snapshot_active_sources()
          self._stop_active_transcription_sources()
          self._start_sources(active)
  ```

  Route engine, weight, compute device, compute type, decoding profile, and coordinator-consumed inference recovery through this one controller method. Model owns no second restart lock/method. Avoid nested restart calls when setting a device also normalizes compute type. Capture heartbeat invokes recorder-only replacement through Model and leaves leases/pipelines intact. Controller shutdown closes the recovery queue, sets its stop event, and joins the coordinator after any released fake request completes.

- [ ] **Step 7: Run lifecycle tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_lifecycle.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_whisper_runtime.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_transcription_recorder_pipeline.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_metrics.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_end_to_end.py" -v
  ```

  Expected: one runtime, no overlapping replacement, recorder-only heartbeat recovery, stale-generation suppression, and clean thread shutdown.

  Commit:

  ```powershell
  git add src-python/model.py src-python/controller.py src-python/mainloop.py src-python/tests/test_pipeline_lifecycle.py src-python/tests/test_pipeline_end_to_end.py src-python/tests/test_pipeline_metrics.py
  git commit -m "fix: coordinate transcription runtime lifecycle"
  ```

---

## Task 10: Normalize and patch progressive messages in the frontend

**Files:**

- Create: `src-ui/logics/common/messageLogUtils.js`
- Create: `src-ui/logics/common/__tests__/messageLogUtils.test.js`
- Modify: `src-ui/logics/common/useMessage.js`
- Modify: `src-ui/logics/useReceiveRoutes.js`

**Interfaces:**

- `createMessageLogEntry(payload, category, options) -> MessageLogEntry` accepts legacy and progressive payloads.
- `mergeTranslationUpdateByTrace(logs, payload, nowMs) -> logs` matches `trace_id` then stringified `target_slot`, returns the original array on no match, and never appends.
- `isTranslationTransitionAllowed(currentStatus, nextStatus) -> boolean` prevents terminal-to-active regression and permits missed-intermediate active-to-terminal transitions.
- `formatDurationMs(durationMs) -> string` returns milliseconds below 1,000 and one-decimal seconds at/above 1,000.
- `getTranslationPresentation(entry, nowMs) -> {tone,textKey,textValues,elapsedMs,showQueuePosition}` maps every active/terminal/error-code state to localized rendering.
- Translation entries contain `{target_slot,message,transliteration,status,engine,previous_engine,duration_ms,queue_position,error_code,status_changed_at_ms}`.
- Terminal status cannot regress; provider changes preserve `previous_engine`; explicit null fields are applied.
- `/run/transcription_translation_update` maps to `useMessage().updateTranscriptionTranslation`.

- [ ] **Step 1: Write failing normalization tests**

  Test a legacy payload and a progressive payload. The progressive entry must preserve `trace_id`, original text, and one queued translation slot. A legacy payload with two translations and no slots must receive stable string slots `"1"` and `"2"`, never `"undefined"` or duplicate React keys.

  ```js
  const entry = createMessageLogEntry(payload, "received", {
      id: "local-id",
      createdAt: "10:30",
      nowMs: 1_000,
  });
  assert.equal(entry.trace_id, "speaker-test");
  assert.equal(entry.messages.original.message, "recognized");
  assert.equal(entry.messages.translations[0].status, "queued");
  ```

- [ ] **Step 2: Write failing immutable patch tests**

  Test that a success patch finds the top-level trace and stringified target slot, replaces the slot without appending a log entry, preserves local `id`/timestamp/original/category, and returns the same array reference for unknown traces or slots.

  Also cover independent slots, explicit `null` fields, fallback provider preservation, same-status duration changes, and terminal-state non-regression.

- [ ] **Step 3: Run the focused test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/messageLogUtils.test.js
  ```

  Expected: module-not-found failure.

- [ ] **Step 4: Implement pure message helpers**

  Export the two status sets plus the five functions defined in Interfaces. `createMessageLogEntry` maps each payload translation with `(entry, index)`; its normalized shape is:

  ```js
  export const TRANSLATION_ACTIVE_STATUSES = new Set(["queued", "sending", "fallback"]);
  export const TRANSLATION_TERMINAL_STATUSES = new Set([
      "success", "timeout", "error", "skipped_overload",
  ]);
  const normalizedTranslation = {
      target_slot: String(entry.target_slot ?? index + 1),
      message: entry.message ?? null,
      transliteration: entry.transliteration ?? [],
      status: entry.status ?? (entry.message ? "success" : null),
      engine: entry.engine ?? null,
      previous_engine: null,
      duration_ms: entry.duration_ms ?? null,
      queue_position: entry.queue_position ?? 0,
      error_code: entry.error_code ?? null,
      status_changed_at_ms: nowMs,
  };
  ```

  Use property-presence checks (`Object.hasOwn`) so an explicit `message: null` clears a value. Store `previous_engine` when `engine` changes. Set `status_changed_at_ms` only when status or engine changes; the UI's live elapsed counter begins at frontend receipt, while the backend's terminal `duration_ms` remains authoritative.

- [ ] **Step 5: Wire the hook and receive route**

  Replace `generateMessageObject()` usage with `createMessageLogEntry()`. Add:

  ```js
  const updateTranscriptionTranslation = (payload) => {
      updateMessageLogs((current) =>
          mergeTranslationUpdateByTrace(current.data, payload, Date.now())
      );
  };
  ```

  Return the method from `useMessage()` and map `/run/transcription_translation_update` to it. Do not mutate existing entries in `updateItemById`; convert that helper to an immutable copy as part of the same change.

- [ ] **Step 6: Run tests and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/messageLogUtils.test.js
  ```

  Expected: all legacy, progressive, fallback, terminal, and identity tests pass.

  Commit:

  ```powershell
  git add src-ui/logics/common/messageLogUtils.js src-ui/logics/common/__tests__/messageLogUtils.test.js src-ui/logics/common/useMessage.js src-ui/logics/useReceiveRoutes.js
  git commit -m "feat: patch transcript translations in place"
  ```

---

## Task 11: Render visible waiting, fallback, success, and failure states

**Files:**

- Create: `src-ui/views/app/main_page/main_section/message_container/log_box/message_container/MessageText.jsx`
- Create: `src-ui/views/app/main_page/main_section/message_container/log_box/message_container/translation_entry/TranslationEntry.jsx`
- Create: `src-ui/views/app/main_page/main_section/message_container/log_box/message_container/translation_entry/TranslationEntry.module.scss`
- Modify: `src-ui/views/app/main_page/main_section/message_container/log_box/message_container/MessageContainer.jsx`
- Modify: `src-ui/views/app/main_page/main_section/message_container/log_box/message_container/MessageContainer.module.scss`
- Modify: `src-ui/logics/common/__tests__/mainPageLocalization.test.js`
- Create: `src-ui/logics/common/__tests__/progressiveMessageStructure.test.js`
- Modify: `locales/en.yml`
- Modify: `locales/th.yml`
- Modify: `locales/ja.yml`
- Modify: `locales/ko.yml`
- Modify: `locales/zh-Hans.yml`
- Modify: `locales/zh-Hant.yml`

**Interfaces:**

- `MessageText({item})` renders `item.message` plus optional transliteration and accepts missing/null fields safely.
- `TranslationEntry({entry})` renders one slot state; it owns a 250ms local clock only for queued/sending/fallback and never mutates Jotai.
- `MessageContainer` always renders original text and keys translation children by stable `target_slot`.
- `main_page.message_log.translation_status.*` is identical across all six locale schemas.

- [ ] **Step 1: Write failing presentation/localization structure tests**

  Add locale keys under `main_page.message_log.translation_status` for queued, sending, fallback, success provider/duration, timeout, error, overload, unavailable, and queue position. Assert all six locale files contain every key and no new visible English literal appears in `TranslationEntry.jsx`.

  Add a source-structure test proving translation entries use `target_slot` as the React key and that pending states render even when `message` is `null`.

- [ ] **Step 2: Run the new tests and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/progressiveMessageStructure.test.js
  node --test src-ui/logics/common/__tests__/mainPageLocalization.test.js
  ```

  Expected: missing component/locale assertions fail before rendering code is changed.

- [ ] **Step 3: Extract defensive text rendering**

  Move the existing transliteration logic to `MessageText.jsx`. Normalize with:

  ```js
  const transliteration = item?.transliteration ?? [];
  const message = item?.message ?? "";
  ```

  Preserve current ruby/hepburn behavior for completed original and translated text.

- [ ] **Step 4: Implement `TranslationEntry` without mutating global state**

  Accept `entry` only. While status is queued, sending, or fallback, use a local 250ms interval to update `nowMs`; clear it on terminal state and unmount. Render:

  - `Waiting for Google · 1.4s` for queued;
  - `Translating with Google · 1.4s` for sending;
  - `Google is slow · trying Bing` for fallback;
  - translated text plus `Bing · 620ms` for success;
  - explicit unavailable copy for timeout/error/overload.

  The interval must never update Jotai or call `updateMessageLogs()`, preventing scroll jumps and whole-log rerenders.

- [ ] **Step 5: Integrate into the existing message**

  Always render original text. Render each translation with:

  ```jsx
  {messages.translations.map((entry) => (
      <TranslationEntry key={entry.target_slot} entry={entry} />
  ))}
  ```

  Preserve the existing `useMessageLogScroll` guard: provider patches may scroll only when the user was already following the newest message.

- [ ] **Step 6: Add the exact six-locale status copy**

  Add the following English source values and their specified translations; preserve interpolation names exactly:

  | Key | English | Thai | Japanese |
  | --- | --- | --- | --- |
  | `queued` | `Waiting for {{engine}} · {{elapsed}}` | `กำลังรอ {{engine}} · {{elapsed}}` | `{{engine}} を待機中 · {{elapsed}}` |
  | `sending` | `Translating with {{engine}} · {{elapsed}}` | `กำลังแปลด้วย {{engine}} · {{elapsed}}` | `{{engine}} で翻訳中 · {{elapsed}}` |
  | `fallback` | `{{previousEngine}} is slow · trying {{engine}}` | `{{previousEngine}} ช้า · กำลังลอง {{engine}}` | `{{previousEngine}} が遅延 · {{engine}} を試行中` |
  | `success_meta` | `{{engine}} · {{duration}}` | `{{engine}} · {{duration}}` | `{{engine}} · {{duration}}` |
  | `timeout` | `Translation unavailable · {{engine}} timed out` | `ไม่มีคำแปล · {{engine}} หมดเวลา` | `翻訳を利用できません · {{engine}} がタイムアウトしました` |
  | `error` | `Translation unavailable · {{engine}} failed` | `ไม่มีคำแปล · {{engine}} ล้มเหลว` | `翻訳を利用できません · {{engine}} が失敗しました` |
  | `skipped_overload` | `Translation skipped · queue overloaded` | `ข้ามการแปล · คิวทำงานหนักเกินไป` | `翻訳をスキップしました · キューが過負荷です` |
  | `no_provider` | `Translation unavailable · no provider selected` | `ไม่มีคำแปล · ยังไม่ได้เลือกผู้ให้บริการ` | `翻訳を利用できません · プロバイダーが未選択です` |
  | `unavailable` | `Translation unavailable` | `ไม่มีคำแปล` | `翻訳を利用できません` |
  | `queue_position` | `Queue {{position}}` | `คิว {{position}}` | `キュー {{position}}` |

  | Key | Korean | Simplified Chinese | Traditional Chinese |
  | --- | --- | --- | --- |
  | `queued` | `{{engine}} 대기 중 · {{elapsed}}` | `正在等待 {{engine}} · {{elapsed}}` | `正在等待 {{engine}} · {{elapsed}}` |
  | `sending` | `{{engine}}로 번역 중 · {{elapsed}}` | `正在使用 {{engine}} 翻译 · {{elapsed}}` | `正在使用 {{engine}} 翻譯 · {{elapsed}}` |
  | `fallback` | `{{previousEngine}} 지연 · {{engine}} 시도 중` | `{{previousEngine}} 响应缓慢 · 正在尝试 {{engine}}` | `{{previousEngine}} 回應緩慢 · 正在嘗試 {{engine}}` |
  | `success_meta` | `{{engine}} · {{duration}}` | `{{engine}} · {{duration}}` | `{{engine}} · {{duration}}` |
  | `timeout` | `번역을 사용할 수 없음 · {{engine}} 시간 초과` | `翻译不可用 · {{engine}} 请求超时` | `翻譯無法使用 · {{engine}} 請求逾時` |
  | `error` | `번역을 사용할 수 없음 · {{engine}} 실패` | `翻译不可用 · {{engine}} 失败` | `翻譯無法使用 · {{engine}} 失敗` |
  | `skipped_overload` | `번역 건너뜀 · 대기열 과부하` | `已跳过翻译 · 队列过载` | `已略過翻譯 · 佇列過載` |
  | `no_provider` | `번역을 사용할 수 없음 · 제공자 미선택` | `翻译不可用 · 未选择服务商` | `翻譯無法使用 · 未選擇服務商` |
  | `unavailable` | `번역을 사용할 수 없음` | `翻译不可用` | `翻譯無法使用` |
  | `queue_position` | `대기열 {{position}}` | `队列 {{position}}` | `佇列 {{position}}` |

- [ ] **Step 7: Run focused and full UI tests, then commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/progressiveMessageStructure.test.js
  npm run test:ui
  ```

  Expected: all progressive structure and locale parity tests pass; existing UI logic tests remain green.

  Commit:

  ```powershell
  git add src-ui/views/app/main_page/main_section/message_container/log_box/message_container src-ui/logics/common/__tests__ locales
  git commit -m "feat: show translation progress on transcript messages"
  ```

---

## Task 12: Add the event-driven pipeline latency strip

**Files:**

- Create: `src-ui/logics/common/pipelineStatusUtils.js`
- Create: `src-ui/logics/common/usePipelineStatus.js`
- Create: `src-ui/logics/common/__tests__/pipelineStatusUtils.test.js`
- Create: `src-ui/logics/common/__tests__/pipelineStatusStructure.test.js`
- Create: `src-ui/logics/common/__tests__/pipelineStatusLocalization.test.js`
- Modify: `src-ui/logics/common/index.js`
- Modify: `src-ui/logics/store.js`
- Modify: `src-ui/logics/useReceiveRoutes.js`
- Create: `src-ui/views/app/main_page/main_section/pipeline_status/PipelineStatus.jsx`
- Create: `src-ui/views/app/main_page/main_section/pipeline_status/PipelineStatus.module.scss`
- Modify: `src-ui/views/app/main_page/main_section/MainSection.jsx`
- Modify: `src-ui/views/app/main_page/main_section/MainSection.module.scss`
- Modify: `locales/en.yml`
- Modify: `locales/th.yml`
- Modify: `locales/ja.yml`
- Modify: `locales/ko.yml`
- Modify: `locales/zh-Hans.yml`
- Modify: `locales/zh-Hant.yml`

**Interfaces:**

- `mergePipelineStatusEvent(state, event, {maxTraces:32})` merges schema-v1 events by trace and `stage:target_slot`, preserving equal-millisecond arrival order.
- `selectPipelineStatusSummary(state, nowMs)` returns `{source,transcription,translation,queue,total_duration_ms,health}`.
- `PIPELINE_ACTIVE_OUTCOMES` is exactly `waiting`, `running`, `sending`, and `fallback`; their local elapsed display advances until a terminal event.
- `isLatencyActive(event)` returns false for `stage:"capture"` even when its liveness outcome is `running`; it returns true for queue `waiting` and non-capture waiting/running/sending/fallback. Queue `success` stops its timer.
- Health is `error` for terminal failure/overload, `slow` when an active or successful stage reaches 2,000ms, and `healthy` at 1,999ms or below.
- Events with `trace_id:null` update only `latest_by_source[source][stageKey]`; they are never inserted into the trace dictionary, so mic/speaker source-level metrics cannot collide.
- `usePipelineStatus()` exposes the atom value and immutable event updater; `/run/pipeline_status` is its only backend route.
- `PipelineStatus` owns local active elapsed time and a 450ms exceptional-announcement debounce; neither timer writes to Jotai.

- [ ] **Step 1: Write failing reducer tests**

  Export the empty-state and stage-key functions below plus the three reducer/selector functions specified in Interfaces:

  ```js
  export const createEmptyPipelineStatusState = () => ({
      traces: {},
      latest_by_source: { mic: {}, speaker: {} },
      latest_source: null,
      latest_observed_at_ms: 0,
      announcement_event: null,
  });
  export const getPipelineStageKey = (event) => `${event.stage}:${event.target_slot ?? "_"}`;
  ```

  Cover schema version 1, separate target slots, older-event rejection, distinct equal-millisecond updates, a 32-trace retention limit, latest source summary, and total duration from terminal `stage: "output"`. Verify null-trace mic/speaker events stay separate outside `traces`. Test all four active outcomes, queue waiting→success timer stop, permanent capture/running exclusion, and the 1,999ms healthy / 2,000ms slow boundary. Only timeout, error, skipped overload, and recovered events become live announcements.

  In `pipelineStatusStructure.test.js`, assert the component is placed between ResourceMonitor and MessageContainer, renders localized source/stage labels, imports healthy/warning/error icons, uses a local active-stage interval, contains tabular-number/responsive-wrap CSS, and has exactly one debounced polite live region fed only by exceptional/recovery events.

- [ ] **Step 2: Run the focused reducer test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/pipelineStatusUtils.test.js
  node --test src-ui/logics/common/__tests__/pipelineStatusStructure.test.js
  node --test src-ui/logics/common/__tests__/pipelineStatusLocalization.test.js
  ```

  Expected: module-not-found failure.

- [ ] **Step 3: Implement reducer, atom, hook, and route**

  Add `Atom_PipelineStatus` initialized from `createEmptyPipelineStatusState()`. `usePipelineStatus()` exposes `currentPipelineStatus` and an `updatePipelineStatus(payload)` callback that immutably merges `current.data`.

  Map `/run/pipeline_status` through the common namespace. Ignore unknown schema versions and source spellings instead of corrupting current status.

- [ ] **Step 4: Build the compact status strip**

  Insert `PipelineStatus` between `ResourceMonitor` and `MessageContainer`, and change the chat grid to:

  ```scss
  grid-template-rows: auto auto minmax(0, 1fr);
  ```

  Render source, transcription engine/duration, cloud provider/live or final duration, queue depth/health, and total output duration. Use the existing check, warning, and error icons; text labels must convey state without relying on color. Use tabular numerals and wrap secondary details on narrow widths.

  While `isLatencyActive(latestStage)` is true—including queue waiting—maintain a component-local 250ms clock so elapsed status advances without new backend events. Never write that clock into Jotai. Stop it on queue success or another terminal event/unmount and replace it with the backend's authoritative `duration_ms` when received. Never age capture/running into slow. Classify and label latency work as slow at 2,000ms; timeout/error/overload is error; all other completed stages are healthy.

  Routine timings are not live. Feed only exceptional/recovery events into one visually hidden `role="status" aria-live="polite" aria-atomic="true"` region after a 450ms debounce.

- [ ] **Step 5: Localize and verify**

  Add `main_page.pipeline_status` with the exact values below and extend locale parity tests to compare every locale against English for the namespace.

  | Key | English | Thai | Japanese |
  | --- | --- | --- | --- |
  | `source` | `Source` | `แหล่งเสียง` | `入力` |
  | `listening` | `Listening` | `กำลังฟัง` | `リスニング` |
  | `speaking` | `Speaking` | `กำลังพูด` | `スピーキング` |
  | `transcription` | `Transcription` | `ถอดเสียง` | `文字起こし` |
  | `cloud` | `Cloud` | `คลาวด์` | `クラウド` |
  | `queue` | `Queue` | `คิว` | `キュー` |
  | `total` | `Total` | `รวม` | `合計` |
  | `healthy` | `Healthy` | `ปกติ` | `正常` |
  | `slow` | `Slow` | `ช้า` | `低速` |
  | `error` | `Error` | `ข้อผิดพลาด` | `エラー` |
  | `waiting` | `Waiting` | `กำลังรอ` | `待機中` |
  | `unavailable` | `Unavailable` | `ไม่พร้อมใช้งาน` | `利用不可` |
  | `timeout_announcement` | `{{engine}} translation timed out` | `การแปลด้วย {{engine}} หมดเวลา` | `{{engine}} の翻訳がタイムアウトしました` |
  | `overload_announcement` | `Translation queue overloaded` | `คิวการแปลทำงานหนักเกินไป` | `翻訳キューが過負荷です` |
  | `error_announcement` | `{{stage}} failed` | `{{stage}} ล้มเหลว` | `{{stage}} が失敗しました` |
  | `recovered_announcement` | `{{stage}} recovered` | `{{stage}} กลับมาทำงานแล้ว` | `{{stage}} が復旧しました` |

  | Key | Korean | Simplified Chinese | Traditional Chinese |
  | --- | --- | --- | --- |
  | `source` | `소스` | `来源` | `來源` |
  | `listening` | `듣기` | `正在聆听` | `正在聆聽` |
  | `speaking` | `말하기` | `正在说话` | `正在說話` |
  | `transcription` | `음성 인식` | `转写` | `轉錄` |
  | `cloud` | `클라우드` | `云端` | `雲端` |
  | `queue` | `대기열` | `队列` | `佇列` |
  | `total` | `전체` | `总计` | `總計` |
  | `healthy` | `정상` | `正常` | `正常` |
  | `slow` | `느림` | `缓慢` | `緩慢` |
  | `error` | `오류` | `错误` | `錯誤` |
  | `waiting` | `대기 중` | `等待中` | `等待中` |
  | `unavailable` | `사용 불가` | `不可用` | `無法使用` |
  | `timeout_announcement` | `{{engine}} 번역 시간 초과` | `{{engine}} 翻译超时` | `{{engine}} 翻譯逾時` |
  | `overload_announcement` | `번역 대기열 과부하` | `翻译队列过载` | `翻譯佇列過載` |
  | `error_announcement` | `{{stage}} 실패` | `{{stage}} 失败` | `{{stage}} 失敗` |
  | `recovered_announcement` | `{{stage}} 복구됨` | `{{stage}} 已恢复` | `{{stage}} 已恢復` |

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/pipelineStatusUtils.test.js
  node --test src-ui/logics/common/__tests__/pipelineStatusStructure.test.js
  node --test src-ui/logics/common/__tests__/pipelineStatusLocalization.test.js
  npm run test:ui
  ```

  Expected: reducer, announcement, and all localization tests pass.

- [ ] **Step 6: Commit**

  ```powershell
  git add src-ui/logics/common/pipelineStatusUtils.js src-ui/logics/common/usePipelineStatus.js src-ui/logics/common/__tests__/pipelineStatusUtils.test.js src-ui/logics/common/__tests__/pipelineStatusStructure.test.js src-ui/logics/common/__tests__/pipelineStatusLocalization.test.js src-ui/logics/common/index.js src-ui/logics/store.js src-ui/logics/useReceiveRoutes.js src-ui/views/app/main_page/main_section locales
  git commit -m "feat: display transcription pipeline latency"
  ```

---

## Task 13: Expose the decoding profile in transcription settings

**Files:**

- Modify: `src-ui/logics/configs/config_page_setter/ui_config_setter.js`
- Modify: `src-ui/views/app/config_page/setting_section/setting_box/transcription/Transcription.jsx`
- Create: `src-ui/logics/common/__tests__/whisperDecodingProfileUI.test.js`
- Modify: `locales/en.yml`
- Modify: `locales/th.yml`
- Modify: `locales/ja.yml`
- Modify: `locales/ko.yml`
- Modify: `locales/zh-Hans.yml`
- Modify: `locales/zh-Hant.yml`

**Interfaces:**

- The dynamic config registry entry is `Category:"Transcription"`, `Base_Name:"WhisperDecodingProfile"`, base endpoint `whisper_decoding_profile`, default `"balanced"`.
- `useTranscription()` exposes `currentWhisperDecodingProfile` and `setWhisperDecodingProfile` through existing generated config logic.
- The selector sends exactly `fast`, `balanced`, or `accurate` and renders only for Whisper.

- [ ] **Step 1: Write a failing source-structure test**

  Assert the config setter declares `WhisperDecodingProfile` under the transcription category and uses `/get/data/whisper_decoding_profile` plus `/set/data/whisper_decoding_profile`. Assert `Transcription.jsx` renders values `fast`, `balanced`, and `accurate` only when the selected engine is Whisper.

- [ ] **Step 2: Run the focused test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/whisperDecodingProfileUI.test.js
  ```

  Expected: registry, options, and locale assertions fail before UI implementation.

- [ ] **Step 3: Wire the dynamic config hook**

  Follow the existing `SelectedTranscriptionComputeType` entry shape so `useTranscription()` exposes current value and setter. Do not create a separate store path.

- [ ] **Step 4: Render the profile selector**

  Place it near compute type, label Balanced as recommended for VRChat, and describe beam sizes without exposing unsupported knobs:

  - Fast — beam 1, lowest latency;
  - Balanced — beam 2, recommended;
  - Accurate — beam 5, highest decoder cost.

  Preserve VAD as a separate user-controlled setting and leave it off by default.

- [ ] **Step 5: Add exact localized labels and descriptions**

  Use `config_page.transcription.whisper_decoding_profile` and these values:

  | Copy | English | Thai | Japanese |
  | --- | --- | --- | --- |
  | Label | `Whisper decoding profile` | `โปรไฟล์การถอดรหัส Whisper` | `Whisper デコードプロファイル` |
  | Description | `Choose the balance between VR responsiveness and decoder accuracy.` | `เลือกระหว่างความลื่นไหลใน VR และความแม่นยำของตัวถอดรหัส` | `VR の応答性とデコード精度のバランスを選びます。` |
  | Fast | `Fast · beam 1 · lowest latency` | `เร็ว · beam 1 · หน่วงต่ำสุด` | `高速 · beam 1 · 最小遅延` |
  | Balanced | `Balanced · beam 2 · recommended for VRChat` | `สมดุล · beam 2 · แนะนำสำหรับ VRChat` | `バランス · beam 2 · VRChat 推奨` |
  | Accurate | `Accurate · beam 5 · highest decoder cost` | `แม่นยำ · beam 5 · ใช้การประมวลผลสูงสุด` | `高精度 · beam 5 · デコード負荷最大` |

  | Copy | Korean | Simplified Chinese | Traditional Chinese |
  | --- | --- | --- | --- |
  | Label | `Whisper 디코딩 프로필` | `Whisper 解码配置` | `Whisper 解碼設定` |
  | Description | `VR 반응성과 디코더 정확도의 균형을 선택합니다.` | `选择 VR 响应速度与解码准确度之间的平衡。` | `選擇 VR 回應速度與解碼準確度之間的平衡。` |
  | Fast | `빠름 · beam 1 · 최저 지연` | `快速 · beam 1 · 最低延迟` | `快速 · beam 1 · 最低延遲` |
  | Balanced | `균형 · beam 2 · VRChat 권장` | `均衡 · beam 2 · 推荐用于 VRChat` | `均衡 · beam 2 · 建議用於 VRChat` |
  | Accurate | `정확 · beam 5 · 최고 디코더 부하` | `准确 · beam 5 · 解码开销最高` | `精確 · beam 5 · 解碼負載最高` |

- [ ] **Step 6: Test and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/whisperDecodingProfileUI.test.js
  npm run test:ui
  ```

  Expected: profile route/option structure and six-locale parity pass.

  Commit:

  ```powershell
  git add src-ui/logics/configs/config_page_setter/ui_config_setter.js src-ui/views/app/config_page/setting_section/setting_box/transcription/Transcription.jsx src-ui/logics/common/__tests__/whisperDecodingProfileUI.test.js locales
  git commit -m "feat: expose Whisper latency profiles"
  ```

---

## Task 14: Run cross-stage verification and frozen-build checks

**Files:**

- Modify only if verification uncovers a scoped defect in files already listed above.

**Interfaces:**

- The Task-9 end-to-end fake test drives capture submission, fake Whisper completion, blocked translation, progressive UI payload collection, and final output through public pipeline/controller boundaries.
- It asserts configured `timeout_seconds == 5.0` and capture-based total duration; it uses Events instead of waiting five real seconds.
- CPU verification is required when `.venv` exists and must be reported as unavailable, not passed, when it does not.

- [ ] **Step 1: Run every Python unit test in the CUDA environment**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
  ```

  Expected: all tests pass; no test downloads a model or calls a cloud provider.

- [ ] **Step 2: Compile and import the Python pipeline in CUDA and CPU environments**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m compileall -q src-python
  .\.venv_cuda\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src-python'); from models.pipeline.source_pipeline import SourcePipeline; from models.transcription.whisper_runtime import WhisperRuntimeManager; import model, controller, mainloop; print('cuda imports ok')"
  ```

  If `.venv\Scripts\python.exe` exists, run:

  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
  .\.venv\Scripts\python.exe -m compileall -q src-python
  .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src-python'); from models.pipeline.source_pipeline import SourcePipeline; from models.transcription.whisper_runtime import WhisperRuntimeManager; import model, controller, mainloop; print('cpu imports ok')"
  ```

  Expected: exit code 0 with no syntax/import errors in every locally installed environment. Record the CPU environment as unavailable when `.venv` is absent.

- [ ] **Step 3: Run all UI tests and production build**

  Run:

  ```powershell
  npm run test:ui
  npm run vite-build
  ```

  Expected: all Node tests pass and Vite completes a production build without unresolved imports or React warnings.

- [ ] **Step 4: Build the CUDA sidecar**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m PyInstaller spec/backend_cuda.spec --distpath src-tauri/bin --clean --noconfirm --log-level ERROR
  ```

  Expected: PyInstaller exits 0 and emits the configured sidecar in `src-tauri/bin`. If the repository's optional CPU `.venv` exists, also run `bat\build.bat`; otherwise record that the CPU frozen build was not locally available rather than claiming it passed.

- [ ] **Step 5: Perform deterministic integration tests with fakes**

  Re-run the Task-9 test that creates trace A from a known captured audio timestamp, blocks fake Google after recording `timeout_seconds`, and feeds trace B before release. It must still prove same-slot update, one final output, `5.0` timeout configuration, and capture-based total duration; do not sleep five seconds.

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_pipeline_end_to_end.py" -v
  ```

  Expected: later fake audio reaches transcription while Google is blocked, original text precedes translation, and final output is exactly once.

- [ ] **Step 6: Perform the RTX 3070 Ti manual smoke matrix**

  With VRChat running, use the same recorded speech for each row and record transcription duration, cloud duration, total duration, queue depth, drops, and VRAM:

  | Mode | Model | Compute | Profile | Expected |
  | --- | --- | --- | --- | --- |
  | Listen only | Turbo | int8_float16 | Fast | lowest latency, same original text flow |
  | Listen only | Turbo | int8_float16 | Balanced | default/recommended behavior |
  | Listen only | Turbo | int8_float16 | Accurate | beam-5 comparison |
  | Listen + Speak | Turbo | int8_float16 | Balanced | one shared Whisper model |

  Also test: start/stop listening twice, 45 seconds of silence, forced Google timeout with Bing fallback, translation queue overload, configuration restart during blocked inference, and clean application shutdown.

- [ ] **Step 7: Review protocol and secrecy invariants**

  Search:

  ```powershell
  rg -n "pipeline_status|PipelineStatusEvent" src-python src-ui
  rg -n "beam_size=5|chunk_size=get_sample_size|while True" src-python/models/transcription src-python/model.py
  ```

  Expected: the status protocol is wired at both boundaries; `test_pipeline_metrics.py` proves status serialization has no transcript content; no two-frame speaker buffer remains; beam 5 exists only in the Accurate profile mapping/tests; every indefinite loop has a stop condition or is an application event loop.

- [ ] **Step 8: Commit scoped verification corrections, if any**

  If verification required corrections, stage only those files and commit:

  ```powershell
  git commit -m "test: verify real-time transcription pipeline"
  ```

  If no correction was needed, do not create an empty commit.
