# Real-time transcription and progressive translation design

Date: 2026-07-13
Status: Approved for implementation planning

## Summary

VRCNT-Next will treat speech recognition and cloud translation as separate pipeline stages. Whisper text will appear in the application as soon as transcription finishes. Google, Bing, or another translation provider will then update that same message in place when its response arrives.

This prevents a slow cloud translator from making transcription look frozen and makes the actual bottleneck visible to the user. The change also fixes the speaker-capture buffer defect, bounds real-time queues, reduces Whisper decoding cost through selectable profiles, prevents duplicate GPU model ownership, and replaces small pending indicators with a blocking operation overlay.

## Problem statement

Listening-only transcription can intermittently become slow or appear to stop while VRChat is using the same RTX 3070 Ti 8 GB GPU. Changing from Whisper Turbo to Small or Medium improves speed but loses too much accuracy.

The investigation found several independent causes:

1. Speaker loopback passes `get_sample_size(paInt16)` as `chunk_size`. That value is 2 bytes per sample, but the capture API expects frames per buffer and normally uses 1024. Listening mode therefore performs roughly 512 times more buffer reads than intended.
2. The transcription worker calls translation and all downstream output synchronously. Google or Bing can block the only worker that requests the next Whisper transcription.
3. Google and Bing are called without a timeout. The automatic CTranslate2 fallback contains an unbounded retry loop.
4. Audio and output queues are unbounded. When downstream work stalls, memory and queue age can grow until old speech is silently discarded by the existing six-second Whisper cap.
5. Whisper always uses beam size 5, even for short real-time chunks under GPU contention.
6. Microphone and speaker modes create independent Whisper model instances. Although this was not active in the reported listening-only test, it can duplicate VRAM usage when both modes are enabled.
7. Stall recovery can start a replacement while the old native inference still owns its model, temporarily increasing GPU pressure.
8. Current status UI does not distinguish capture, Whisper inference, translation queueing, provider latency, timeout, or overload.

## Goals

- Display recognized speech immediately after Whisper completes.
- Show translation as a visible asynchronous state on the same message.
- Ensure cloud translation never blocks audio capture or the next Whisper request.
- Preserve recent real-time work with bounded memory and explicit overload behavior.
- Retain Turbo model accuracy while allowing lower-cost decoding.
- Use at most one loaded Whisper model for a given active configuration.
- Report stage latency and provider state without logging transcript contents.
- Make application startup and slow main-function activation obvious and non-interactive.
- Keep the implementation compatible with the Windows PyInstaller sidecar.

## Non-goals

- Replacing faster-whisper or changing transcription providers.
- Running Google and Bing speculatively in parallel for every message.
- Separately estimating network upload and download bandwidth. The available translator API reliably exposes request round-trip time only.
- Upgrading faster-whisper, CTranslate2, CUDA, or the custom SpeechRecognition fork in the same change.
- Adding multiprocessing. The design stays thread-and-queue based for Windows frozen-build safety.

## Pipeline architecture

```text
Audio capture
    |
    v
Bounded per-source audio queue
    |
    v
Shared Whisper runtime / serialized GPU inference
    |                         \
    |                          \ stage metrics
    v                           v
Immediate original-text event   Pipeline status event
    |
    +--> message appears with translation placeholder
    |
    v
Bounded translation queue
    |
    v
Primary provider -> optional alternate provider
    |
    +--> in-place message update
    +--> final OSC / overlay / clipboard / websocket effects
    +--> stage metrics
```

Capture, transcription, translation, and output have separate ownership. A slow stage can create visible queueing, but it cannot execute inline inside an earlier stage.

## Message lifecycle and progressive rendering

Each completed transcription receives a stable `trace_id`. The original message is emitted before any translation request begins.

The frontend inserts one message with one translation entry per enabled target language. Each entry follows this state machine:

```text
queued -> sending -> success
                  -> fallback -> sending -> success
                  -> timeout
                  -> error
queued -> skipped_overload
```

Example user-visible progression:

```text
Original: 今日はどこへ行きますか？
English:  Waiting for Google · 1.4s

Original: 今日はどこへ行きますか？
English:  Google is slow · trying Bing

Original: 今日はどこへ行きますか？
English:  Where are you going today? · Bing · 620ms
```

The update replaces the placeholder in the existing message. It must not append a duplicate message. If all attempts fail, the original text remains visible and the translation entry becomes `Translation unavailable` with the provider outcome.

For microphone/send mode, immediate display affects the local VRCNT-Next log only. OSC, clipboard, overlay, and websocket side effects continue to occur once the translation outcome is known so VRChat does not receive duplicate partial and final chatbox messages. Listening/receive mode follows the same message protocol.

## Backend design

### Speaker capture

- Remove the incorrect `chunk_size=get_sample_size(paInt16)` argument from both speaker recorder constructors.
- Allow the capture library's 1024-frame default or pass an explicit application constant of 1024 frames.
- Preserve selected sample rate, channel count, device index, and loopback mode.
- Add a capture heartbeat so recovery is based on recorder liveness rather than an empty audio queue during normal silence.

### Bounded audio queues

- Use a maximum of four captured chunks per source.
- Recorder callbacks use non-blocking enqueue.
- When full, remove the oldest queued chunk and enqueue the newest one. Real-time recency takes priority over processing stale audio already destined for the six-second Whisper tail cap.
- Increment a dropped-chunk counter and emit a queue-overload status event.
- Never block the capture callback waiting for the consumer.

### Shared Whisper runtime

- Add a `WhisperRuntimeManager` owned by the application model.
- Key the loaded runtime by weight, device, device index, and resolved compute type.
- Give microphone and speaker transcribers leases to the same runtime when their keys match.
- Serialize calls through one inference lane. `num_workers` remains 1 so CTranslate2 does not create additional model replicas.
- Do not load a replacement until the previous inference and model lease have ended.
- On coordinated stop, call the underlying CTranslate2 unload API after the active inference returns. Do not rely on `torch.cuda.empty_cache()` for CTranslate2 allocations.
- A setting change performs a coordinated restart after current inference completes; old sessions use a generation token and may not emit late results.

### Whisper decoding profiles

Add a persisted `WHISPER_DECODING_PROFILE` setting:

| Profile | Beam size | Intended use |
| --- | ---: | --- |
| Fast | 1 | Maximum responsiveness under heavy VR GPU load |
| Balanced | 2 | Default; Turbo accuracy with reduced decoder work |
| Accurate | 5 | Existing behavior for users with GPU headroom |

For CUDA Whisper, selecting compute type `auto` resolves to `int8_float16`. An explicitly selected supported compute type remains respected. Existing explicit CUDA `int8` continues to normalize to `int8_float16`.

The UI exposes all three profiles and identifies Balanced as recommended for VRChat. VAD remains user-controlled and off by default because capture already applies an energy gate and aggressive VAD can remove quiet speech.

### Immediate transcription event

When Whisper produces non-empty text:

1. Generate a `trace_id` that is unique for the process lifetime.
2. Emit the existing microphone or speaker message event immediately, extended with `trace_id` and translation entries in `queued` state.
3. Snapshot the ordered provider candidates once for the trace.
4. Enqueue one translation job per enabled target-language slot. Each job contains immutable text, source language, one target slot, the provider snapshot, the relevant configuration snapshot, and the trace ID.
5. Return to the transcription loop without waiting for translation or output rendering.

The frontend must continue accepting the legacy payload shape during migration. New fields are additive.

The extended initial payload on the existing source-specific endpoint has this shape:

```json
{
  "trace_id": "speaker-42",
  "original": {
    "message": "今日はどこへ行きますか？",
    "transliteration": []
  },
  "translations": [
    {
      "target_slot": "1",
      "message": null,
      "transliteration": [],
      "status": "queued",
      "engine": "Google",
      "duration_ms": null
    }
  ]
}
```

Translation changes use a new `/run/transcription_translation_update` endpoint:

```json
{
  "trace_id": "speaker-42",
  "target_slot": "1",
  "status": "success",
  "engine": "Bing",
  "message": "Where are you going today?",
  "transliteration": [],
  "duration_ms": 620,
  "queue_position": 0,
  "error_code": null
}
```

There is one update per target slot and state change. The frontend locates the existing message by `trace_id`, then the translation entry by `target_slot`.

### Translation scheduler

- Use one ordered translation worker per source and a bounded queue of eight target-slot jobs.
- Each job tries the selected primary provider and at most one selected alternate provider.
- Google and Bing receive a five-second request timeout per attempt.
- Remove the unconditional automatic switch to CTranslate2 and the unbounded fallback loop. CTranslate2 runs only when it is selected as a candidate, and it receives one attempt.
- Do not add automatic retry inside a provider attempt. Provider libraries must receive their native timeout and bounded retry configuration.
- Emit `sending`, `success`, `timeout`, and `error` events for every actual attempt.
- Multiple target languages remain distinct jobs and entries under the same trace ID. All jobs from one trace use the same provider-candidate order.

If the queue is full, remove the oldest target-slot job that has not started, mark that translation entry `skipped_overload`, and enqueue the newest job. The original transcript has already been displayed, so overload never hides recognized speech.

### Final side effects

After every target slot for a trace reaches a terminal state, an aggregator submits exactly one final output task. The output worker then performs the existing formatting, OSC, overlay, clipboard, websocket, logger, telemetry, and history work. These operations remain outside the transcription worker.

Terminal translation states are `success`, `timeout`, `error`, and `skipped_overload`. Missing translations are omitted from final message formatting. If no target slot succeeds, existing configuration decides whether to send/display the original text; the pipeline never sends a duplicate partial and final OSC message.

Each source owns one output worker so ordering is stable within microphone and speaker streams. Workers and queued jobs carry a session generation token. Stopped or replaced sessions cannot emit stale output.

### Metrics protocol

Add an unsolicited `/run/pipeline_status` event. Transport status remains HTTP-style `200`; operational success or failure is represented in the payload so the current frontend router dispatches every update.

```json
{
  "schema_version": 1,
  "trace_id": "speaker-42",
  "source": "speaker",
  "stage": "translation",
  "engine": "Google",
  "target_slot": "1",
  "outcome": "sending",
  "queue_age_ms": 84,
  "duration_ms": null,
  "queue_depth": 1,
  "dropped_count": 0,
  "observed_at_ms": 1783900000000,
  "error_code": null
}
```

Allowed stages are `capture`, `queue`, `transcription`, `translation`, and `output`. Allowed outcomes include `waiting`, `running`, `success`, `slow`, `fallback`, `timeout`, `error`, `skipped_overload`, and `recovered`.

Use `time.monotonic()` or `perf_counter()` for durations. The event contains no transcript or translated text.

## Frontend design

### Message updates

- Extend message-log entries with `trace_id` and per-target translation state.
- Add an update-by-trace-ID route and store reducer.
- Render the original text immediately.
- Render provider name, live elapsed time, queue position when greater than zero, fallback state, and explicit timeout/error copy in the translation area.
- Update the existing entry in place on provider completion.
- A provider-status change must not change scroll position when the user has scrolled away from the newest message.

### Pipeline status strip

Add a compact `PipelineStatus` strip below the existing resource monitor and above the message log. It is event-driven and does not poll.

The default one-row presentation contains:

- Source: `Listening` or `Speaking`
- Transcription: engine and last duration
- Cloud: provider and live/current round-trip duration
- Queue: depth and health state
- Total: end-to-end time when available

Healthy, slow, and error states use text and icons in addition to color. Values use tabular numerals. Narrow layouts wrap secondary details instead of shrinking the text below readable size.

Only timeout, overload, error, and recovery changes use a debounced polite live-region announcement. Routine millisecond updates are not announced.

### Blocking operation overlay

Create a reusable, non-dismissible `BlockingOperationOverlay` used for:

1. Core application startup until `/run/initialization_complete` marks the backend usable.
2. Translate, Listen, or Speak activation that remains pending for more than 250ms.

The overlay:

- Covers the application below the native title bar with a dark translucent scrim and strong backdrop blur.
- Uses a card approximately 42rem wide, responsive to viewport and UI scaling.
- Shows the operation, phase, progress, elapsed time, and the existing localized warm/long-running status copy.
- Uses `role="dialog"`, `aria-modal="true"`, and an internal polite status region.
- Makes the page wrapper inert while active so pointer and keyboard interaction are blocked.
- Cannot be dismissed while the backend operation is legitimately pending.
- Always releases interaction on success or error. A backend failure cannot leave an indefinite blocking overlay.
- Respects reduced-motion preferences and performance mode.

Optional background service checks after the core interface is ready do not continue blocking the application.

The frontend's generic backend-error route must clear the pending state for the operation that failed before displaying the error notification. This prevents a 500 response from leaving the overlay open forever.

All new copy is added to English, Thai, Japanese, Korean, Simplified Chinese, and Traditional Chinese locale files.

## Error and recovery behavior

- Translation timeout: retain original, mark provider timeout, try one alternate if configured, then mark unavailable.
- Translation queue overload: retain original, mark the displaced job as skipped, update drop metrics.
- Whisper inference error: report the error and allow a coordinated runtime recovery only after the failing call returns. Never overlap replacement models.
- Capture heartbeat failure: restart only the recorder for that source, preserving the shared model.
- Output error: record the failed stage without killing the transcription or translation worker.
- Startup or activation error: release the blocking overlay and show the existing error surface with the operation name.

## Testing strategy

### Python unit tests

- Speaker recorder constructors use a frame-sized chunk and preserve device parameters.
- Audio producer never blocks and applies the documented oldest-chunk replacement policy.
- A blocked fake translation worker does not stop subsequent fake transcriptions.
- Translation queue is bounded and marks displaced jobs as `skipped_overload`.
- Google/Bing attempts receive a five-second timeout and never exceed primary plus one alternate.
- A translator returning `False` cannot enter an unbounded fallback loop.
- Original-text events are emitted before translation begins.
- Translation success, fallback, timeout, and error update the same trace ID.
- Shared runtime creates one Whisper model for matching microphone/speaker configuration.
- Runtime stop/restart cannot overlap models or emit from stale generations.
- Fast, Balanced, and Accurate profiles propagate beam sizes 1, 2, and 5.
- Pipeline metrics contain timing metadata but no message text.

### Frontend tests

- Initial transcription payload creates one message with visible original text and waiting translations.
- Translation updates replace placeholders without appending duplicate messages.
- Provider fallback, timeout, error, and overload copy are classified correctly.
- Pipeline status reducer ignores stale events and merges events by trace/stage.
- Blocking overlay activates after the delay, makes pages inert, and releases on success/error.
- Existing localization tests include every new key for all six locales.

### Verification

- Run all Python unit tests.
- Run `npm run test:ui`.
- Run the Vite production build.
- Run Python syntax/import checks in both CPU and CUDA environments.
- Build both PyInstaller sidecar specifications if the local toolchain permits.
- Smoke-test listen start/stop twice, 45 seconds of silence, a forced translation timeout, and clean shutdown.
- On the RTX 3070 Ti, compare Fast, Balanced, and Accurate with the same recorded speech while monitoring latency and VRAM.

## Acceptance criteria

1. In listening mode, recognized text appears before a delayed translation completes.
2. While a fake Google request is delayed for five seconds, later audio continues reaching Whisper.
3. The waiting placeholder visibly identifies Google or Bing and shows elapsed time.
4. A successful response updates the same message entry with provider and duration.
5. A timeout or overload leaves the original text visible and explains why translation is missing.
6. Speaker capture no longer uses a two-frame PortAudio buffer.
7. Balanced mode uses beam size 2 and is the default for new and migrated configurations.
8. Enabling both microphone and speaker modes loads one matching Whisper runtime, not two.
9. All queues have documented bounds and observable drop counters.
10. Startup and slow function activation show the large blurred blocking overlay and cannot trap the interface after an error.
11. Existing typed-message, OSC, overlay, clipboard, websocket, and logging behavior remains functional.

## Rollout and compatibility

- Additive message fields preserve legacy frontend parsing during the transition.
- Missing `WHISPER_DECODING_PROFILE` migrates to `balanced`.
- Explicit user compute types remain unchanged; only Whisper CUDA `auto` resolution changes.
- No model files need to be downloaded again.
- No new Python dependency is required.
- Operational logs and status events never include transcript text beyond the existing user-facing message events.
