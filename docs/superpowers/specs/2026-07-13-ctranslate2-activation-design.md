# CTranslate2 Activation Design

## Goal

Make a selected CTranslate2 translation engine ready before it can receive a
translation job, while preserving the current real-time transcription and
translation hot paths.

The local CTranslate2 model must load only at a user-controlled activation
boundary. It must not add model checks, loading, or global pipeline
synchronization to each transcribed message. CTranslate2 inference may enter a
small local lifecycle guard solely so teardown cannot release a model that is
actively translating.

## Confirmed failure

The installed 418M CTranslate2 model and tokenizer load successfully on CPU and
translate English to Thai locally. The failure is in engine activation:
switching an already-enabled Translation function from Google or Bing to
CTranslate2 updates the selected provider without loading the local model.
The pipeline then schedules CTranslate2 correctly, but inference returns a
failure because the model is not ready.

## Required behavior

### Translation is off

- Selecting CTranslate2 records the selection without loading the model.
- Turning Translation on with CTranslate2 selected shows the existing blocking
  operation overlay immediately.
- The backend loads the selected CTranslate2 weight, tokenizer, device, and
  compute type while the activation request is pending.
- Translation becomes enabled only after loading succeeds.

### Translation is already on

- Switching from an online provider to CTranslate2 begins local-model
  preparation immediately; the user does not need to turn Translation off and
  on again.
- The existing blocking-operation overlay remains visible while preparation is
  pending and uses the existing localized Translation activation copy.
- The previous provider selection remains active until CTranslate2 loading
  succeeds.
- On success, the backend commits CTranslate2 as the selected provider and the
  overlay closes.
- On failure, the backend preserves the previous provider selection, settles
  the pending UI state, closes the overlay, and reports the existing localized
  translation activation error.

### Leaving CTranslate2

- Switching from a selection that contains CTranslate2 to a selection that does
  not contain it releases the local model and tokenizer before the provider
  selection request reports success to the UI.
- The new provider selection is committed internally first so newly captured
  messages cannot enter the CTranslate2 queue while teardown is waiting for an
  active local inference to finish.
- Turning Translation off also releases a loaded CTranslate2 model. Turning it
  on again follows the normal load-and-overlay activation path.
- A selection that still contains CTranslate2, including as a fallback, keeps
  the model loaded because the pipeline may still use it.

### Already loaded model

- If the requested CTranslate2 model is loaded and its model/device/compute
  parameters have not changed, activation completes without reloading.

## Architecture

### Backend control plane

Add controller helpers that reconcile CTranslate2 readiness with a proposed
engine selection. They will:

1. Normalize the current and proposed selections.
2. When the proposed enabled selection contains CTranslate2, return immediately
   if the local model is current; otherwise load it once and clear the
   changed-parameters marker only after success.
3. When the current enabled selection contains CTranslate2 and the proposed
   selection does not, commit the proposed selection to stop new local work,
   then release the local translator and tokenizer before returning success.
4. Return immediately for changes that involve online providers only.

`setEnableTranslation` calls the helper before its existing enabled-state early
return. `setSelectedTranslationEngines` calls it before committing a proposed
CTranslate2 selection when Translation is currently enabled. A controller lock
serializes these control-plane activation transactions so concurrent requests
cannot expose a half-loaded selection. `setDisableTranslation` and an enabled
switch away from CTranslate2 call the teardown helper.

The translation facade gains an explicit CTranslate2 unload operation. A small
CTranslate2-only lifecycle condition allows concurrent active local inference
to finish before teardown clears the translator and tokenizer references. New
stale CTranslate2 attempts cannot begin once teardown starts. This coordination
does not touch online providers or Whisper.

No model readiness call is added to `SourcePipeline._run_translation_job` or to
the per-message translator APIs.

### UI blocking state

The engine-selection request already has a pending state. Include the pending
selection in the derived blocking-operation inputs when:

- Translation is enabled; and
- either the current or proposed selection for the active preset contains
  CTranslate2.

Map this state to the existing `translation` blocking operation so the overlay
reuses all current localized title, progress, warm, long-running, failure, focus,
blur, and interaction-blocking behavior. Selecting CTranslate2 while
Translation is off remains nonblocking because loading is deferred until the
Translation toggle is enabled. Switching away stays blocked until model
teardown completes.

## Performance constraints

- No new work occurs in audio capture, Whisper inference, translation queue
  admission, provider attempts, or final output.
- CTranslate2 loading happens once per selected model/device/compute
  configuration, only at activation or an enabled provider switch.
- CTranslate2 teardown waits only for an already-running local inference; it
  does not drain, pause, or restart Whisper or the source pipelines.
- The existing configured translation device remains authoritative. The fix
  does not move CTranslate2 onto the Whisper GPU or create another Whisper
  runtime.
- Switching between online providers remains immediate. Switching away from
  CTranslate2 pays only the one-time teardown cost and releases its RAM or VRAM.

## Error handling

- A failed enabled-state switch must not commit CTranslate2.
- Once a switch away from CTranslate2 is committed internally, teardown is
  best-effort but always clears local references and marks the model unloaded;
  the UI cannot be left pointing at a partially unloaded CTranslate2 provider.
- Both success and failure must settle the engine-selection pending atom so the
  overlay cannot remain open indefinitely.
- Existing translation activation error codes and localized notification copy
  are reused; no exception details or model paths are displayed.
- A failed Translation toggle keeps Translation disabled, matching current
  activation behavior.

## Verification

Tests will prove:

- enabling Translation with CTranslate2 selected loads once and shows the
  existing activation pending state;
- an already-enabled Translation function loads CTranslate2 before committing
  the provider switch;
- a failed load preserves the previous provider and settles the UI operation;
- selecting CTranslate2 while Translation is off does not load it;
- switching between Google, Bing, or other online providers never loads,
  checks, or unloads CTranslate2;
- an already-current local model is not reloaded;
- switching to a selection without CTranslate2 waits for active local inference,
  unloads once, clears model/tokenizer references, and then settles the UI;
- keeping CTranslate2 as a fallback does not unload it;
- turning Translation off unloads CTranslate2 and the next enable loads it
  again;
- no model-readiness logic is added to the per-message pipeline;
- existing progressive translation, activation overlay, localization, Python,
  UI, production build, and CUDA frozen-sidecar verification remain green.

No test downloads a model or contacts a cloud provider.
