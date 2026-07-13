# CTranslate2 Activation Design

## Goal

Make a selected CTranslate2 translation engine ready before it can receive a
translation job, while preserving the current real-time transcription and
translation hot paths.

The local CTranslate2 model must load only at a user-controlled activation
boundary. It must not add model checks, loading, or synchronization to each
transcribed message.

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

### Already loaded model

- If the requested CTranslate2 model is loaded and its model/device/compute
  parameters have not changed, activation completes without reloading.
- A successfully loaded model stays warm when the user temporarily switches
  back to Google or Bing, making a later CTranslate2 selection immediate.

## Architecture

### Backend control plane

Add one controller helper that ensures CTranslate2 readiness for a proposed
engine selection. It will:

1. Normalize the proposed selection.
2. Return immediately when CTranslate2 is not selected.
3. Return immediately when the local model is already current.
4. Otherwise load the selected model once and clear the changed-parameters
   marker only after success.

`setEnableTranslation` calls the helper before its existing enabled-state early
return. `setSelectedTranslationEngines` calls it before committing a proposed
CTranslate2 selection when Translation is currently enabled. A controller lock
serializes these control-plane activation transactions so concurrent requests
cannot expose a half-loaded selection.

No model readiness call is added to `SourcePipeline._run_translation_job` or to
the per-message translator APIs.

### UI blocking state

The engine-selection request already has a pending state. Include the pending
selection in the derived blocking-operation inputs only when:

- Translation is enabled; and
- the proposed selection for the active preset contains CTranslate2.

Map this state to the existing `translation` blocking operation so the overlay
reuses all current localized title, progress, warm, long-running, failure, focus,
blur, and interaction-blocking behavior. Selecting CTranslate2 while
Translation is off remains nonblocking because loading is deferred until the
Translation toggle is enabled.

## Performance constraints

- No new work occurs in audio capture, Whisper inference, translation queue
  admission, provider attempts, or final output.
- CTranslate2 loading happens once per selected model/device/compute
  configuration, only at activation or an enabled provider switch.
- The existing configured translation device remains authoritative. The fix
  does not move CTranslate2 onto the Whisper GPU or create another Whisper
  runtime.
- Online-provider switching remains immediate and does not inspect or unload
  CTranslate2.

## Error handling

- A failed enabled-state switch must not commit CTranslate2.
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
- selecting Google or Bing never loads or checks CTranslate2;
- an already-current local model is not reloaded;
- no model-readiness logic is added to the per-message pipeline;
- existing progressive translation, activation overlay, localization, Python,
  UI, production build, and CUDA frozen-sidecar verification remain green.

No test downloads a model or contacts a cloud provider.
