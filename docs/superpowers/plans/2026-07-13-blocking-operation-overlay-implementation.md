# Blocking Operation Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace easy-to-miss startup and activation indicators with a large blurred, non-dismissible overlay that blocks application interaction only while core startup or Translate/Speak/Listen activation is genuinely pending.

**Architecture:** The overlay is derived from existing backend-ready, initialization, and main-function atoms rather than stored independently. Core startup blocks immediately; activation blocks only after 250ms to avoid flicker. The page wrapper becomes inert while the overlay is open, the native title bar stays outside the blocked region, and every success/error path returns pending atoms to `ok` before notifications render.

**Tech Stack:** React 18, Jotai, Sass modules, i18next/YAML locales, native HTML `inert`, `node:test`, Python `unittest`, existing Python controller/main-loop transport.

## Global Constraints

- This plan implements the overlay portion of [2026-07-13-realtime-transcription-pipeline-design.md](../specs/2026-07-13-realtime-transcription-pipeline-design.md).
- Execute the real-time pipeline plan first when doing both plans because its runtime lifecycle changes the same transcription activation methods. If this plan is executed alone, Task 1 is still required so pending state reflects actual model readiness.
- Startup blocks from the first render until `/run/initialization_complete`, except that `InitStatus.phase === "error"` must release `inert` and show an error surface.
- Translate, Speak, and Listen activation blocks only for `{ state: "pending", data: false }` after 250ms. Deactivation keeps the old value `true` while pending and must not block.
- Foreground/always-on-top is never a blocking operation.
- Simultaneous activation priority is deterministic: translation, transcription send (Speaking), transcription receive (Listening).
- No independent “overlay open” atom is allowed. Derived state prevents success/error paths from leaving a stale modal flag.
- The overlay covers only the area below `WindowTitleBar`. It is a sibling of the inert page wrapper, never its descendant.
- The overlay has no close button, scrim click handler, Escape handler, or timeout dismissal. It releases only when the operation resolves or errors.
- Error handling clears pending state before showing a snackbar/notification. Unknown 400/500 errors may notify normally but cannot leave a recognized activation atom pending.
- Startup opens immediately. Activation elapsed time begins when pending begins, even though the overlay itself waits 250ms.
- Status text ticks at most once per second. Do not announce changing elapsed milliseconds through a live region.
- Reuse existing theme variables. Performance mode removes blur/glow/motion but retains a dark enough scrim to block visual interaction. Reduced-motion disables overlay animation.
- All new user-facing copy must exist in English, Thai, Japanese, Korean, Simplified Chinese, and Traditional Chinese.
- Python tests must insert `src-python` into `sys.path` and stub unavailable native/optional modules before importing controller/model code, following `src-python/tests/test_sensevoice_download.py`.
- Add no dependency or process model, do not upgrade backend/frontend packages, and preserve Windows PyInstaller sidecar compatibility.
- Make one focused commit after each task passes its focused tests. Preserve unrelated user changes.

---

## Task 1: Make activation responses represent actual readiness

**Files:**

- Modify: `src-python/controller.py`
- Modify: `src-python/model.py`
- Modify: `src-python/mainloop.py`
- Modify: `src-python/errors.py`
- Create: `src-python/tests/test_main_function_activation.py`

**Interfaces:**

- `model.startMicTranscript(callback) -> bool` and `model.startSpeakerTranscript(callback) -> bool` return `True` only after recorder, transcriber, queues, and runtime/session workers are ready.
- `Controller.startTranscriptionSendMessage() -> bool` and `startTranscriptionReceiveMessage() -> bool` return the corresponding model result while preserving `device_access_status=True` in `finally`.
- Missing devices raise `DeviceUnavailableError(ErrorCode.DEVICE_NO_MIC)` or `DeviceUnavailableError(ErrorCode.DEVICE_NO_SPEAKER)` before any worker starts.
- `/set/enable/{translation|transcription_send|transcription_receive}` returns `{"status": 200, "result": true}` only on readiness. Every failure, including status 500, returns `result: {"error_code": string, "message": string, "data": false}`.
- `runControllerInitialization(main_instance) -> bool` is a callable main-loop boundary: it returns `True` after core initialization and `False` after emitting a localized terminal initialization-error event.

- [ ] **Step 1: Write failing transcription activation timing tests**

  Patch `model.startMicTranscript()` and `model.startSpeakerTranscript()` with fakes blocked by an `Event`. Invoke `setEnableTranscriptionSend()` and `setEnableTranscriptionReceive()` on worker threads. Assert the controller methods have not returned status 200 until the corresponding fake start function completes.

  ```python
  thread = Thread(target=lambda: results.append(controller.setEnableTranscriptionReceive()))
  thread.start()
  start_entered.wait(1)
  self.assertEqual(results, [])
  release_start.set()
  thread.join(1)
  self.assertEqual(results[0], {"status": 200, "result": True})
  ```

  This test must fail while the setter starts a daemon thread and returns immediately.

- [ ] **Step 2: Write failing activation-error tests**

  Cover a generic model-start exception, a detected VRAM exception, no selected mic, and no selected speaker. Each direct set endpoint must return a structured non-200 response with a restored `False` result. It must stop any partially created source and never leave `ENABLE_TRANSCRIPTION_SEND` or `ENABLE_TRANSCRIPTION_RECEIVE` true. Cleanup is best-effort inside its own `try/except`, so a stop failure cannot replace or hide the original activation response.

  Existing unsolicited error events may remain for backwards compatibility, but the direct endpoint response is authoritative for the pending toggle.

- [ ] **Step 3: Run focused tests and confirm RED**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_main_function_activation.py" -v
  ```

  Expected: setters return before the fake model start is released, and error results do not consistently restore the flag.

- [ ] **Step 4: Make enable setters synchronously await model/session creation**

  Refactor the current background wrappers so direct enable endpoints call the activation method synchronously. Model/session creation may create its own long-lived worker threads, but the controller returns `200` only after the recorder, transcriber, translation pipeline, and runtime lease are ready.

  In `model.py`, replace the current no-device callback with an explicit readiness failure and return `True` at the end of successful startup:

  ```python
  if len(selected_mic_device) == 0 or mic_device_name == "NoDevice":
      raise DeviceUnavailableError(ErrorCode.DEVICE_NO_MIC)
  # construct recorder, transcriber, and workers
  return True
  ```

  Apply the corresponding `DEVICE_NO_SPEAKER` rule to speaker startup. Add the small typed exception to `errors.py`; it carries an `ErrorCode` and no transcript content.

  Change both controller wrappers to preserve the device lock and return readiness:

  ```python
  def startTranscriptionReceiveMessage(self) -> bool:
      while self.device_access_status is False:
          sleep(1)
      self.device_access_status = False
      try:
          return model.startSpeakerTranscript(self.speakerMessage)
      finally:
          self.device_access_status = True
  ```

  Use `return model.startMicTranscript(self.micMessage)` for send. Add a test that exercises setter → controller wrapper → model fake and receives true, so an implicit `None` cannot regress.

  ```python
  def setEnableTranscriptionReceive(self, *args, **kwargs) -> dict:
      if config.ENABLE_TRANSCRIPTION_RECEIVE is True:
          return {"status": 200, "result": True}
      config.ENABLE_TRANSCRIPTION_RECEIVE = True
      try:
          if self.startTranscriptionReceiveMessage() is not True:
              raise RuntimeError("transcription activation was cancelled")
          return {"status": 200, "result": True}
      except Exception as exc:
          config.ENABLE_TRANSCRIPTION_RECEIVE = False
          try:
              self.stopTranscriptionReceiveMessage()
          except Exception:
              errorLogging()
          return self._transcriptionActivationError("speaker", exc)
  ```

  Apply the same boundary to send/microphone. `_transcriptionActivationError()` maps `DeviceUnavailableError` to `DEVICE_NO_MIC`/`DEVICE_NO_SPEAKER` with `data=False`, detected VRAM failures to the existing source-specific VRAM code with `data=False`, and other exceptions to status 500 with `{"error_code":"TRANSCRIPTION_START_FAILED","message":"","data":False}`. Translation generic activation failure uses `TRANSLATION_ENABLE_FAILED` with the same structured shape. Add these stable codes to `ErrorCode`; the frontend uses its existing localized/generic error surface rather than displaying new Python English. Avoid catching and swallowing inside `startTranscription*Message`; let one controller boundary classify the exception and return it. Keep disable endpoints synchronous so pending deactivation reliably returns to `ok`, though deactivation does not open the overlay.

- [ ] **Step 5: Prove translation readiness semantics**

  Add tests that Google/Bing-only translation activation returns immediately, while selected CTranslate2 activation returns only after `changeTranslatorCTranslate2Model()` completes. Its generic/VRAM error response restores `ENABLE_TRANSLATION=False`.

- [ ] **Step 6: Add a terminal core-startup error event**

  Extend `Controller.initializationStatus()` with optional `message_key: str = ""` and `detail_key: str = ""`, including both fields in its payload. Extract `runControllerInitialization(main_instance)` from the code under `if __name__ == "__main__"`, then add a test that forces `main_instance.controller.init()` to fail before `/run/initialization_complete`. The callable boundary calls `initializationStatus("", "", visible=True, phase="error", message_key="blocking_operation.startup_failed", detail_key="blocking_operation.startup_failed_detail")` before logging the exception. Do not falsely emit initialization complete and do not mark the backend ready.

  Keep the receiver/process shutdown path usable after the failure so the UI can show the nonblocking error surface and the user can close/restart the application. The main block calls the extracted function and enters the existing main loop even when it returns `False`; do not claim an endpoint allowlist because the current live `_call_handler()` does not enforce mapping status. Backend-ready remains false, so normal UI controls stay disabled while shutdown/restart remains available.

- [ ] **Step 7: Run tests and commit**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_main_function_activation.py" -v
  ```

  Expected: all readiness, source cleanup, and error-state tests pass.

  Commit:

  ```powershell
  git add src-python/controller.py src-python/model.py src-python/mainloop.py src-python/errors.py src-python/tests/test_main_function_activation.py
  git commit -m "fix: resolve toggles only after activation is ready"
  ```

---

## Task 2: Define pure blocking-state and error-resolution rules

**Files:**

- Create: `src-ui/logics/common/blockingOperationState.js`
- Create: `src-ui/logics/common/__tests__/blockingOperationState.test.js`

**Interfaces:**

- `getBlockingOperationCandidate(input) -> null | BlockingOperationCandidate` consumes the six current atom values and applies startup/error/activation priority rules.
- `BlockingOperationCandidate.progress` is `{ kind: "determinate", value: number, max: number }` for startup or `{ kind: "indeterminate" }` for activation.
- `getMainFunctionPendingCopyKey(operationId, elapsedMs) -> string` returns an existing i18n key.
- `resolveFailedMainFunction({endpoint, errorCode}) -> null | "translation" | "transcription_send" | "transcription_receive"` is the single endpoint/error mapping.
- `readBooleanBackendResult(result) -> boolean | undefined` accepts only raw booleans or an object with a boolean `.data`.

- [ ] **Step 1: Write failing operation-selection tests**

  Cover:

  - startup wins over simultaneous activations and uses `delayMs: 0`;
  - backend ready removes startup immediately;
  - startup phase `error` removes the blocking candidate even if backend ready is false;
  - only pending activation with old value false is selected;
  - pending deactivation with old value true is ignored;
  - Foreground is not accepted as input;
  - activation priority is translation, send, receive;
  - activation delay is exactly 250ms.

  ```js
  const operation = getBlockingOperationCandidate({
      isBackendReady: false,
      initStatus: { phase: "local", message: "Checking", detail: "" },
      initProgress: 1,
      translationStatus: { state: "pending", data: false },
      transcriptionSendStatus: { state: "ok", data: false },
      transcriptionReceiveStatus: { state: "ok", data: false },
  });
  assert.equal(operation.id, "startup");
  assert.equal(operation.delayMs, 0);
  ```

- [ ] **Step 2: Write failing copy-threshold and error-resolution tests**

  Assert 4,999ms uses `start`, 5,000–29,999ms uses `warm`, and 30,000ms uses `long` for each operation.

  `resolveFailedMainFunction({ endpoint, errorCode })` must resolve:

  - `/set/enable|disable/translation` and `/run/enable_translation` to `translation`;
  - `/set/enable|disable/transcription_send` and `/run/enable_transcription_send` to `transcription_send`;
  - `/set/enable|disable/transcription_receive` and `/run/enable_transcription_receive` to `transcription_receive`;
  - `TRANSLATION_VRAM_ENABLE` and `TRANSLATION_DISABLED_VRAM` to translation;
  - `DEVICE_NO_MIC`, `TRANSCRIPTION_VRAM_MIC`, and `TRANSCRIPTION_SEND_DISABLED_VRAM` to send;
  - `DEVICE_NO_SPEAKER`, `TRANSCRIPTION_VRAM_SPEAKER`, and `TRANSCRIPTION_RECEIVE_DISABLED_VRAM` to receive;
  - unknown endpoint/code pairs to `null`.

  Test `readBooleanBackendResult()` with `true`, `false`, `{ data: false }`, `{ data: true }`, and arbitrary strings/objects. Only actual booleans are accepted.

- [ ] **Step 3: Run focused tests and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationState.test.js
  ```

  Expected: module-not-found failure.

- [ ] **Step 4: Implement the pure state contract**

  ```js
  export const BLOCKING_OPERATION_DELAY_MS = 250;
  export const WARM_OPERATION_MS = 5_000;
  export const LONG_OPERATION_MS = 30_000;

  export const getMainFunctionPendingCopyKey = (operationId, elapsedMs) => {
      const phase = elapsedMs >= LONG_OPERATION_MS
          ? "long"
          : elapsedMs >= WARM_OPERATION_MS ? "warm" : "start";
      return `main_page.main_function_pending.${operationId}_${phase}`;
  };

  export const readBooleanBackendResult = (result) => {
      if (typeof result === "boolean") return result;
      if (result && typeof result === "object" && typeof result.data === "boolean") {
          return result.data;
      }
      return undefined;
  };
  ```

  Candidate progress is a discriminated object:

  ```js
  // startup
  { kind: "determinate", value: Math.max(0, initProgress), max: 4 }
  // activation
  { kind: "indeterminate" }
  ```

  Use existing localized pending key paths for activation titles/copy. Use new `blocking_operation.*` keys only for overlay chrome and startup fallback text.

- [ ] **Step 5: Run tests and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationState.test.js
  ```

  Expected: all selection, delay, threshold, endpoint, error-code, and boolean tests pass.

  Commit:

  ```powershell
  git add src-ui/logics/common/blockingOperationState.js src-ui/logics/common/__tests__/blockingOperationState.test.js
  git commit -m "feat: define blocking operation state rules"
  ```

---

## Task 3: Derive overlay timing and clear failed pending toggles

**Files:**

- Create: `src-ui/logics/common/useBlockingOperation.js`
- Modify: `src-ui/logics/common/index.js`
- Modify: `src-ui/logics/common/useStdoutToPython.js`
- Modify: `src-ui/logics/main/useMainFunction.js`
- Modify: `src-ui/logics/useReceiveRoutes.js`
- Modify: `src-ui/views/app/_app_controllers/StartPythonController.jsx`
- Modify: `src-ui/views/app/main_page/sidebar_section/main_function_switch/MainFunctionSwitch.jsx`
- Modify: `src-ui/views/app/main_page/sidebar_section/main_function_switch/MainFunctionSwitch.module.scss`
- Create: `src-ui/logics/common/__tests__/blockingOperationIntegration.test.js`

**Interfaces:**

- `useBlockingOperation() -> { isBlocking, operation }` derives display state and elapsed time; it owns no Jotai atom.
- `asyncStdoutToPython(endpoint, value) -> Promise<{ok: true} | {ok: false, error: Error}>` preserves compatibility for callers that ignore its result and lets toggles recover local write failures.
- `clearPendingMainFunctionError({endpoint, errorCode, result}) -> boolean` restores the affected atom through a functional update.
- `markBackendStartupError()` writes `InitStatus` with `phase: "error"` and localized key fields on spawn/error/early-close before readiness.
- `SwitchContainer` is a native `<button type="button" role="switch">` with `aria-checked`, `aria-busy`, native `disabled` for backend-disabled controls, and `aria-disabled` plus an event guard while pending so the initiating element retains focus identity.

- [ ] **Step 1: Write failing source-structure integration tests**

  The repository has no DOM hook-test dependency, so use `node:test` source-structure assertions plus the pure tests from Task 2. Assert the hook reads all six existing atoms/hooks, uses 1,000ms elapsed ticks, cancels both delay/tick timers, tracks three per-operation start timestamps, and does not create/import a new atom.

  Assert the receive router calls `clearPendingMainFunctionError()` before `_useBackendErrorHandling` for status 400 and before `showNotification_Error()` for status 500. Assert a missing subprocess/write failure restores the initiating toggle, and sidecar spawn/error/early-close changes InitStatus to terminal error.

- [ ] **Step 2: Run the integration test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  ```

  Expected: missing hook, transport result, error-clearing, sidecar failure, and semantic-switch assertions fail.

- [ ] **Step 3: Implement `useBlockingOperation`**

  ```js
  export const useBlockingOperation = () => {
      const { currentIsBackendReady } = useIsBackendReady();
      const { currentInitStatus } = useInitStatus();
      const { currentInitProgress } = useInitProgress();
      const {
          currentTranslationStatus,
          currentTranscriptionSendStatus,
          currentTranscriptionReceiveStatus,
      } = useMainFunction();
      const startedAtByOperationRef = useRef({});
      const [nowMs, setNowMs] = useState(() => Date.now());
      const activeById = {
          startup: currentIsBackendReady.data !== true
              && currentInitStatus.data.phase !== "error",
          translation: currentTranslationStatus.state === "pending"
              && currentTranslationStatus.data === false,
          transcription_send: currentTranscriptionSendStatus.state === "pending"
              && currentTranscriptionSendStatus.data === false,
          transcription_receive: currentTranscriptionReceiveStatus.state === "pending"
              && currentTranscriptionReceiveStatus.data === false,
      };

      useEffect(() => {
          const observedAt = Date.now();
          Object.entries(activeById).forEach(([id, active]) => {
              if (active && startedAtByOperationRef.current[id] === undefined) {
                  startedAtByOperationRef.current[id] = observedAt;
              } else if (!active) {
                  delete startedAtByOperationRef.current[id];
              }
          });
          setNowMs(observedAt);
      }, [
          activeById.startup,
          activeById.translation,
          activeById.transcription_send,
          activeById.transcription_receive,
      ]);

      const candidate = getBlockingOperationCandidate({
          isBackendReady: currentIsBackendReady.data,
          initStatus: currentInitStatus.data,
          initProgress: currentInitProgress.data,
          translationStatus: currentTranslationStatus,
          transcriptionSendStatus: currentTranscriptionSendStatus,
          transcriptionReceiveStatus: currentTranscriptionReceiveStatus,
      });
      const startedAt = candidate
          ? startedAtByOperationRef.current[candidate.id] ?? nowMs
          : nowMs;
      const elapsedMs = Math.max(0, nowMs - startedAt);

      useEffect(() => {
          if (!candidate || elapsedMs >= candidate.delayMs) return undefined;
          const timer = setTimeout(
              () => setNowMs(Date.now()),
              candidate.delayMs - elapsedMs,
          );
          return () => clearTimeout(timer);
      }, [candidate?.id, candidate?.delayMs, elapsedMs]);

      useEffect(() => {
          if (!Object.values(activeById).some(Boolean)) return undefined;
          const timer = setInterval(() => setNowMs(Date.now()), 1_000);
          return () => clearInterval(timer);
      }, [
          activeById.startup,
          activeById.translation,
          activeById.transcription_send,
          activeById.transcription_receive,
      ]);

      return {
          isBlocking: Boolean(candidate && elapsedMs >= candidate.delayMs),
          operation: candidate ? { ...candidate, elapsedMs } : null,
      };
  };
  ```

  Read `useIsBackendReady`, `useInitStatus`, `useInitProgress`, and the three statuses from `useMainFunction`. Keep `startedAtByOperationRef` entries for startup and all three activation IDs, updating each entry from its own atom even when a higher-priority candidate is displayed. Delete an entry only when that operation leaves pending; this prevents priority changes from resetting its delay/elapsed time. Startup phase/message updates do not reset startup elapsed time. Open startup at once; for activation, set `isBlocking` after the selected candidate's remaining 250ms delay. Clear timers immediately when the candidate disappears, errors, or the component unmounts.

- [ ] **Step 4: Add pending error recovery to `useMainFunction`**

  Return:

  ```js
  const clearPendingMainFunctionError = ({ endpoint, errorCode, result }) => {
      const operation = resolveFailedMainFunction({ endpoint, errorCode });
      if (!operation) return false;
      const backendValue = readBooleanBackendResult(result);
      updateStatusFor(operation)((current) => backendValue ?? current.data);
      return true;
  };
  ```

  Calling the matching `update*Status()` is required because it restores atom state to `"ok"`; do not invent an `"error"` atom state, which would prevent `createTogglePair` from retrying.

- [ ] **Step 5: Clear before both error surfaces**

  Reuse the already-instantiated `useMainFunction` result in `hook_results`. For status 400, pass `endpoint`, `result.error_code`, and `result` to the clear method before existing backend error handling. For status 500, do the same before the generic notification. Preserve current notification copy and categories.

  When an initialization status event has `phase: "error"`, the derived overlay closes. Keep the startup status banner visible as the nonblocking error surface; do not set `IsBackendReady=true` merely to dismiss the overlay.

- [ ] **Step 6: Recover local transport and sidecar lifecycle failures**

  Change `asyncStdoutToPython()` to return `{ok:true}` on a completed write and `{ok:false,error}` for a missing subprocess or rejected write; it must no longer swallow the failure as an indistinguishable success. In `createTogglePair`, await that result. On failure, restore the same toggle with a functional `update*Status(current => current.data)` and show the localized backend-unavailable notification.

  In `StartPythonController`, mirror `IsBackendReady` into `backendReadyRef` on every render so long-lived command callbacks do not capture the initial false value. Handle spawn rejection, the command `error` event, and a process `close` event that occurs while `backendReadyRef.current !== true`. Each calls `markBackendStartupError()` with `message_key`/`detail_key` and shows one deduplicated notification. After readiness, a close event reports a backend-disconnected notification but does not reopen the startup overlay.

- [ ] **Step 7: Share threshold logic and make switches keyboard-operable**

  Replace the inline pending-key table and 5/30-second threshold logic in `MainFunctionSwitch.jsx` with `getMainFunctionPendingCopyKey()`. Convert the clickable `.switch_container` from `<div>` to `<button type="button" role="switch">`, set `aria-checked={currentState.data === true}`, `aria-busy={currentState.state === "pending"}`, native `disabled={isDisabled}`, and `aria-disabled={currentState.state === "pending"}` with the existing click guard. Add button-reset styles without removing the visible focus ring. Keeping the pending button focusable gives the overlay a stable element to restore after completion. The compact indicator may be visible during the first 250ms; once the overlay opens it sits inside the inert/covered page. Foreground retains its existing separate indicator and behavior.

- [ ] **Step 8: Run tests and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationState.test.js
  node --test src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  ```

  Expected: derived state, timer cleanup structure, shared copy rules, and error-clearing order pass.

  Commit:

  ```powershell
  git add src-ui/logics/common/useBlockingOperation.js src-ui/logics/common/index.js src-ui/logics/common/useStdoutToPython.js src-ui/logics/main/useMainFunction.js src-ui/logics/useReceiveRoutes.js src-ui/views/app/_app_controllers/StartPythonController.jsx src-ui/views/app/main_page/sidebar_section/main_function_switch/MainFunctionSwitch.jsx src-ui/views/app/main_page/sidebar_section/main_function_switch/MainFunctionSwitch.module.scss src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  git commit -m "fix: release pending operations on every outcome"
  ```

---

## Task 4: Build the accessible non-dismissible overlay component

**Files:**

- Create: `src-ui/views/app/others/blocking_operation_overlay/BlockingOperationOverlay.jsx`
- Create: `src-ui/views/app/others/blocking_operation_overlay/BlockingOperationOverlay.module.scss`
- Modify: `src-ui/views/app/others/index.js`
- Create: `src-ui/logics/common/__tests__/blockingOperationOverlayStructure.test.js`

**Interfaces:**

- `BlockingOperationOverlay` consumes `{open, operationId, title, phase, detail, progress, progressLabel, progressText, elapsedText}`; App creates progress/elapsed text through localized interpolation.
- `open === false` returns `null`, leaving no dialog node in the accessibility tree.
- `progress.kind === "determinate"` requires numeric `value`/`max`; `"indeterminate"` omits `aria-valuenow`.
- Opening moves focus to the card; closing/unmount restores the previously focused connected element.

- [ ] **Step 1: Write a failing semantic structure test**

  Read the JSX source and assert it includes:

  - `role="dialog"` and `aria-modal="true"`;
  - `aria-labelledby` and `aria-describedby` IDs tied to content;
  - a focusable card with `tabIndex={-1}`;
  - an internal polite, atomic status region containing phase/detail only;
  - a determinate progressbar with min/max/current;
  - an indeterminate progressbar without `aria-valuenow`;
  - no close button, scrim click callback, or Escape key dismissal.

- [ ] **Step 2: Run the structure test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationOverlayStructure.test.js
  ```

  Expected: component-not-found and missing semantic/style assertions fail.

- [ ] **Step 3: Implement the component contract**

  ```jsx
  export const BlockingOperationOverlay = ({
      open, operationId, title, phase, detail, progress,
      progressLabel, progressText, elapsedText,
  }) => {
      const cardRef = useRef(null);
      const previousFocusRef = useRef(null);

      useEffect(() => {
          if (!open) return undefined;
          previousFocusRef.current = document.activeElement;
          cardRef.current?.focus();
          return () => {
              const previous = previousFocusRef.current;
              if (previous?.isConnected) previous.focus();
          };
      }, [open]);

      if (!open) return null;
      const titleId = `blocking-operation-${operationId}-title`;
      const descriptionId = `blocking-operation-${operationId}-description`;
      const determinate = progress.kind === "determinate";
      const progressPercent = determinate
          ? Math.min(100, Math.max(0, progress.max > 0
              ? (progress.value / progress.max) * 100
              : 0))
          : 0;
      const progressAria = determinate
          ? {
              "aria-valuemin": 0,
              "aria-valuemax": progress.max,
              "aria-valuenow": progress.value,
          }
          : { "aria-valuetext": progressText };

      return (
          <div
              className={styles.overlay}
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              aria-describedby={descriptionId}
          >
              <section className={styles.card} ref={cardRef} tabIndex={-1}>
                  <h2 id={titleId}>{title}</h2>
                  <div
                      id={descriptionId}
                      role="status"
                      aria-live="polite"
                      aria-atomic="true"
                  >
                      <p>{phase}</p>
                      {detail ? <p>{detail}</p> : null}
                  </div>
                  <div
                      className={styles.progress}
                      role="progressbar"
                      aria-label={progressLabel}
                      {...progressAria}
                  >
                      <span
                          className={styles.progress_fill}
                          style={determinate
                              ? { "--progress-percent": `${progressPercent}%` }
                              : undefined}
                      />
                  </div>
                  <p className={styles.progress_text}>{progressText}</p>
                  <p className={styles.elapsed}>{elapsedText}</p>
              </section>
          </div>
      );
  };
  ```

  On open, remember `document.activeElement`, focus the card, and on close/unmount restore focus only if that node is still connected. Keep elapsed time outside the live region to avoid a screen-reader announcement each second. Phase/detail may update politely.

  For determinate startup progress, set `aria-valuemin={0}`, `aria-valuemax={progress.max}`, and `aria-valuenow={progress.value}`. For activation, render the same visible track with `aria-valuetext` but omit `aria-valuenow`.

- [ ] **Step 4: Implement large blurred responsive styling**

  Required properties:

  ```scss
  .overlay {
      position: absolute;
      inset: 0;
      z-index: 100;
      display: grid;
      place-items: center;
      background: rgb(4 8 16 / 72%);
      backdrop-filter: blur(18px) saturate(0.8);
  }

  .card {
      width: min(42rem, calc(100% - 3.2rem));
      max-height: calc(100% - 3.2rem);
      overflow: auto;
      font-variant-numeric: tabular-nums;
  }

  .progress_fill {
      width: var(--progress-percent, 100%);
  }
  ```

  Use responsive padding/type, clear progress text, and a visible indeterminate pattern that remains understandable with animation disabled. Add `@media (prefers-reduced-motion: reduce)` to remove spinner/progress/card animations. Under `:global(.performance_mode)`, remove blur, glow, and animation while increasing scrim opacity enough to retain the blocked state.

- [ ] **Step 5: Run tests and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationOverlayStructure.test.js
  ```

  Expected: dialog, focus, live-region, progress, no-dismiss, and reduced-motion structure tests pass.

  Commit:

  ```powershell
  git add src-ui/views/app/others/blocking_operation_overlay src-ui/views/app/others/index.js src-ui/logics/common/__tests__/blockingOperationOverlayStructure.test.js
  git commit -m "feat: add blocking operation overlay"
  ```

---

## Task 5: Integrate inert page boundaries and retain a nonblocking startup banner

**Files:**

- Modify: `src-ui/views/app/App.jsx`
- Modify: `src-ui/views/app/App.module.scss`
- Modify: `src-ui/views/app/others/startup_status_banner/StartupStatusBanner.jsx`
- Modify: `src-ui/views/app/others/startup_status_banner/StartupStatusBanner.module.scss`
- Modify: `src-ui/logics/common/__tests__/blockingOperationIntegration.test.js`

**Interfaces:**

- `Contents` keeps `WindowTitleBar` outside `.app_body`, derives overlay props, and applies `inert` to exactly one wrapper containing both normal and updating branches.
- `StartupStatusBanner` renders optional post-ready service status for 2.2 seconds, but renders `phase:"error"` persistently until status changes or the app restarts.
- Layer order is Config/Main (`<=20`), persistent banner (`50`), blocking overlay (`100`), with the native title bar outside the app-body layer.

- [ ] **Step 1: Extend the failing integration test**

  Assert `WindowTitleBar` is before and outside `.app_body`; `.pages_wrapper` receives `inert={isBlocking ? "" : undefined}`; the overlay is a sibling after `.pages_wrapper`; and `ConfigPage`, `MainPage`, and `ModalController` remain inside the inert wrapper. Assert `SnackbarController` stays outside so a cleared error notification remains visible.

- [ ] **Step 2: Run the extended integration test and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  ```

  Expected: App lacks app-body/inert/overlay placement and banner persistence assertions.

- [ ] **Step 3: Derive and translate overlay props in `Contents`**

  Call `useBlockingOperation()` and `useI18n()`. Set `overlayProps` to `null` when `operation` is null. Otherwise resolve title/phase keys through `t()`, prefer backend `message_key`/`detail_key` over raw phase text, build determinate `progressText` with `blocking_operation.progress_steps` and `{current,max}`, build activation text with `blocking_operation.progress_indeterminate`, and build `elapsedText` with elapsed seconds. Render only through the guarded branch below:

  ```jsx
  <WindowTitleBar />
  <div className={styles.app_body}>
      <StartupStatusBanner />
      <UpdateNotificationController />
      <div className={styles.pages_wrapper} inert={isBlocking ? "" : undefined}>
          {currentIsSoftwareUpdating.data === false ? (
              <>
                  <ConfigPage />
                  <MainPage />
                  <ModalController />
              </>
          ) : <UpdatingComponent />}
      </div>
      {overlayProps ? (
          <BlockingOperationOverlay
              open={isBlocking}
              operationId={overlayProps.operationId}
              title={overlayProps.title}
              phase={overlayProps.phase}
              detail={overlayProps.detail}
              progress={overlayProps.progress}
              progressLabel={overlayProps.progressLabel}
              progressText={overlayProps.progressText}
              elapsedText={overlayProps.elapsedText}
          />
      ) : null}
  </div>
  ```

  Keep software-updating behavior inside the app-body decision. Updating UI must not accidentally render beneath a stale activation overlay.

- [ ] **Step 4: Add the app-body layout boundary**

  ```scss
  .app_body {
      position: relative;
      width: 100%;
      flex: 1;
      min-height: 0;
      overflow: hidden;
  }
  ```

  Preserve `.pages_wrapper` at full width/height. Overlay z-index must exceed the config page's current z-index 20. Do not place the native title bar under the scrim.

- [ ] **Step 5: Narrow `StartupStatusBanner` to nonblocking statuses**

  Render the banner only when either:

  - backend is ready and an optional background service status remains visible; or
  - initialization phase is `error`.

  Keep the existing 2.2-second dismissal only for post-ready optional status and retain `pointer-events: none`. A `phase:"error"` banner never auto-dismisses, uses the localized error keys instead of Python English, and has z-index 50 so it is above ConfigPage but below the overlay. Remove the hard-coded `Backend startup X / 4` English label in favor of a locale key. The banner never sets `inert` and never controls overlay visibility.

- [ ] **Step 6: Run tests and commit**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  node --test src-ui/logics/common/__tests__/blockingOperationOverlayStructure.test.js
  ```

  Expected: app hierarchy, inert boundary, error release, and banner scope assertions pass.

  Commit:

  ```powershell
  git add src-ui/views/app/App.jsx src-ui/views/app/App.module.scss src-ui/views/app/others/startup_status_banner src-ui/logics/common/__tests__/blockingOperationIntegration.test.js
  git commit -m "feat: block app interaction during startup and activation"
  ```

---

## Task 6: Localize every overlay state and verify the complete interaction

**Files:**

- Modify: `locales/en.yml`
- Modify: `locales/th.yml`
- Modify: `locales/ja.yml`
- Modify: `locales/ko.yml`
- Modify: `locales/zh-Hans.yml`
- Modify: `locales/zh-Hant.yml`
- Create: `src-ui/logics/common/__tests__/blockingOperationLocalization.test.js`
- Modify: `src-ui/logics/common/__tests__/mainPageLocalization.test.js`

**Interfaces:**

- `blocking_operation.*` is the shared locale namespace for overlay chrome, startup failure, sidecar transport failure, and the nonblocking startup banner.
- `main_page.main_function_pending.*` remains the source of Translate/Speak/Listen start/warm/long operation copy.
- Locale parity tests treat English as the key schema and require non-empty values in all five translated files.

- [ ] **Step 1: Write failing six-locale parity tests**

  Add a `blocking_operation` namespace and assert every leaf is a non-empty string in all six locales:

  ```yaml
  blocking_operation:
      dialog_label:
      startup_operation:
      phase_label:
      progress_label:
      progress_steps:
      progress_indeterminate:
      elapsed:
      startup_failed:
      startup_failed_detail:
      backend_startup_progress:
      backend_unavailable:
      backend_disconnected:
  ```

  Also assert every existing `main_page.main_function_pending` start/warm/long key used by translation, send, and receive exists in all six locales. Add overlay, banner, `StartPythonController.jsx`, and `useMainFunction.js` to localization/source tests and reject newly hard-coded English UI labels.

- [ ] **Step 2: Run localization tests and confirm RED**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationLocalization.test.js
  node --test src-ui/logics/common/__tests__/mainPageLocalization.test.js
  ```

  Expected: missing `blocking_operation` keys and new-source coverage fail.

- [ ] **Step 3: Add localized copy**

  Use these exact values, preserving i18next interpolation tokens:

  ```yaml
  # locales/en.yml
  blocking_operation:
      dialog_label: "Operation in progress"
      startup_operation: "Starting VRCNT-Next"
      phase_label: "Current step"
      progress_label: "Startup progress"
      progress_steps: "{{current}} of {{total}}"
      progress_indeterminate: "Working…"
      elapsed: "Elapsed {{seconds}}s"
      startup_failed: "Startup could not finish"
      startup_failed_detail: "Restart VRCNT-Next. If this continues, check the application log."
      backend_startup_progress: "Backend startup {{current}} / {{total}}"
      backend_unavailable: "The backend is unavailable. Your change was not applied."
      backend_disconnected: "The backend stopped. Restart VRCNT-Next to continue."
  ```

  ```yaml
  # locales/th.yml
  blocking_operation:
      dialog_label: "กำลังดำเนินการ"
      startup_operation: "กำลังเริ่ม VRCNT-Next"
      phase_label: "ขั้นตอนปัจจุบัน"
      progress_label: "ความคืบหน้าในการเริ่มต้น"
      progress_steps: "{{current}} จาก {{total}}"
      progress_indeterminate: "กำลังดำเนินการ…"
      elapsed: "ผ่านไป {{seconds}} วินาที"
      startup_failed: "ไม่สามารถเริ่มต้นให้เสร็จสมบูรณ์"
      startup_failed_detail: "โปรดเริ่ม VRCNT-Next ใหม่ หากยังเกิดปัญหา ให้ตรวจสอบบันทึกของแอป"
      backend_startup_progress: "เริ่มระบบเบื้องหลัง {{current}} / {{total}}"
      backend_unavailable: "ระบบเบื้องหลังไม่พร้อมใช้งาน การเปลี่ยนแปลงของคุณยังไม่ถูกนำไปใช้"
      backend_disconnected: "ระบบเบื้องหลังหยุดทำงาน โปรดเริ่ม VRCNT-Next ใหม่เพื่อดำเนินการต่อ"
  ```

  ```yaml
  # locales/ja.yml
  blocking_operation:
      dialog_label: "処理中"
      startup_operation: "VRCNT-Next を起動しています"
      phase_label: "現在のステップ"
      progress_label: "起動の進行状況"
      progress_steps: "{{current}} / {{total}}"
      progress_indeterminate: "処理しています…"
      elapsed: "経過 {{seconds}} 秒"
      startup_failed: "起動を完了できませんでした"
      startup_failed_detail: "VRCNT-Next を再起動してください。続く場合はアプリのログを確認してください。"
      backend_startup_progress: "バックエンド起動 {{current}} / {{total}}"
      backend_unavailable: "バックエンドを利用できないため、変更は適用されませんでした。"
      backend_disconnected: "バックエンドが停止しました。VRCNT-Next を再起動してください。"
  ```

  ```yaml
  # locales/ko.yml
  blocking_operation:
      dialog_label: "작업 진행 중"
      startup_operation: "VRCNT-Next 시작 중"
      phase_label: "현재 단계"
      progress_label: "시작 진행률"
      progress_steps: "{{current}} / {{total}}"
      progress_indeterminate: "처리 중…"
      elapsed: "경과 {{seconds}}초"
      startup_failed: "시작을 완료하지 못했습니다"
      startup_failed_detail: "VRCNT-Next를 다시 시작하세요. 문제가 계속되면 앱 로그를 확인하세요."
      backend_startup_progress: "백엔드 시작 {{current}} / {{total}}"
      backend_unavailable: "백엔드를 사용할 수 없어 변경 사항이 적용되지 않았습니다."
      backend_disconnected: "백엔드가 중지되었습니다. 계속하려면 VRCNT-Next를 다시 시작하세요."
  ```

  ```yaml
  # locales/zh-Hans.yml
  blocking_operation:
      dialog_label: "操作进行中"
      startup_operation: "正在启动 VRCNT-Next"
      phase_label: "当前步骤"
      progress_label: "启动进度"
      progress_steps: "{{current}} / {{total}}"
      progress_indeterminate: "正在处理…"
      elapsed: "已用 {{seconds}} 秒"
      startup_failed: "无法完成启动"
      startup_failed_detail: "请重新启动 VRCNT-Next。如果问题持续，请检查应用日志。"
      backend_startup_progress: "后端启动 {{current}} / {{total}}"
      backend_unavailable: "后端不可用，您的更改尚未应用。"
      backend_disconnected: "后端已停止。请重新启动 VRCNT-Next 后继续。"
  ```

  ```yaml
  # locales/zh-Hant.yml
  blocking_operation:
      dialog_label: "操作進行中"
      startup_operation: "正在啟動 VRCNT-Next"
      phase_label: "目前步驟"
      progress_label: "啟動進度"
      progress_steps: "{{current}} / {{total}}"
      progress_indeterminate: "正在處理…"
      elapsed: "已用 {{seconds}} 秒"
      startup_failed: "無法完成啟動"
      startup_failed_detail: "請重新啟動 VRCNT-Next。如果問題持續，請檢查應用程式記錄。"
      backend_startup_progress: "後端啟動 {{current}} / {{total}}"
      backend_unavailable: "後端無法使用，您的變更尚未套用。"
      backend_disconnected: "後端已停止。請重新啟動 VRCNT-Next 後繼續。"
  ```

  Reuse the existing localized operation names and warm/long messages instead of duplicating their text. Existing normal startup phase text may continue to come from Python; the new terminal core/sidecar failure uses `message_key` and `detail_key`, so no newly introduced Python English is displayed.

- [ ] **Step 4: Run all UI tests**

  Run:

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationLocalization.test.js
  npm run test:ui
  ```

  Expected: all locale parity, source structure, existing main-function, and common UI tests pass.

- [ ] **Step 5: Run the production UI build**

  Run:

  ```powershell
  npm run vite-build
  ```

  Expected: Vite exits 0 without unresolved aliases, Sass failures, or React attribute warnings. Confirm the rendered inert attribute uses `inert={isBlocking ? "" : undefined}`, not `inert={true}`.

- [ ] **Step 6: Re-run Python and frozen-sidecar verification after activation changes**

  Run:

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
  .\.venv_cuda\Scripts\python.exe -m compileall -q src-python
  .\.venv_cuda\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src-python'); import model, controller, mainloop; print('cuda activation imports ok')"
  .\.venv_cuda\Scripts\python.exe -m PyInstaller spec/backend_cuda.spec --distpath src-tauri/bin --clean --noconfirm --log-level ERROR
  ```

  If `.venv\Scripts\python.exe` exists, also run:

  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
  .\.venv\Scripts\python.exe -m compileall -q src-python
  .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src-python'); import model, controller, mainloop; print('cpu activation imports ok')"
  .\.venv\Scripts\python.exe -m PyInstaller spec/backend.spec --distpath src-tauri/bin --clean --noconfirm --log-level ERROR
  ```

  Expected: every Python test passes, compile/import checks exit 0, and every locally available CPU/CUDA sidecar rebuild succeeds. If `.venv` is absent, record CPU frozen verification as unavailable rather than passed.

- [ ] **Step 7: Perform the manual interaction matrix**

  Verify:

  - core startup blocks immediately below the title bar;
  - `/run/initialization_complete` releases interaction while optional service checks continue only in the banner;
  - an initialization `phase: "error"` releases interaction and leaves a visible nonblocking error banner;
  - Google/Bing-only Translate activation under 250ms never flashes the overlay;
  - slow CTranslate2, Speak, and Listen activation blocks after 250ms and shows start/warm/long copy at 0/5/30 seconds;
  - disable operations do not block;
  - 400 and 500 activation failures close the overlay before the notification appears;
  - page pointer events and Tab navigation cannot reach Config/Main/Modal content while inert;
  - title-bar controls remain pointer-reachable because they are outside the covered body; keyboard access depends on their existing title-bar semantics;
  - focus returns to the initiating switch after success/error when it still exists;
  - reduced-motion removes animation;
  - performance mode removes blur/glow while the opaque scrim remains visibly blocking;
  - UI scaling and narrow windows keep the 42rem card within the viewport.

- [ ] **Step 8: Commit localization and verification corrections**

  ```powershell
  git add locales src-ui/logics/common/__tests__/blockingOperationLocalization.test.js src-ui/logics/common/__tests__/mainPageLocalization.test.js
  git commit -m "feat: localize blocking operation status"
  ```

  If manual/build verification required a scoped correction, include only the already-listed overlay files in this commit. Do not create a separate empty verification commit.
