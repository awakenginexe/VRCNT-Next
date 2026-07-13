# CTranslate2 Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load CTranslate2 only when the user activates it as the primary translator, unload it when leaving or disabling Translation, and prevent online providers from ever falling back to it.

**Architecture:** A CTranslate2-only lifecycle condition protects active local inference while explicit load/unload operations run. A controller transaction lock owns activation and provider-selection changes, while bounded provider snapshots enforce the directional fallback rule. The frontend tracks a provider-selection transition separately from the committed selection and feeds CTranslate2 transitions into the existing blocking overlay.

**Tech Stack:** Python 3.12, CTranslate2 4.6, `threading.Condition`/`RLock`, React 18, Jotai, Node test runner, Python `unittest`, Vite, PyInstaller.

## Global Constraints

- The approved design is [2026-07-13-ctranslate2-activation-design.md](../specs/2026-07-13-ctranslate2-activation-design.md).
- CTranslate2 is local-primary opt-in only; Google, Bing, and other online primaries may fall back only to another online provider.
- Translation-off selection changes do not load CTranslate2.
- An enabled online-to-CTranslate2 change loads successfully before committing; failure preserves the online selection and keeps Translation enabled.
- Leaving CTranslate2 or disabling Translation unloads its native model and clears translator/tokenizer references.
- Do not add model readiness or load calls to per-message translation work.
- Do not clear shared Torch/CUDA caches because Whisper may be using the same GPU.
- Tests use fake models/providers only: no downloads, cloud calls, or GPU inference.
- Preserve unrelated changes and create one focused commit after each verified task.

---

### Task 1: Enforce directional provider fallback

**Files:**
- Modify: `src-python/model.py`
- Modify: `src-python/tests/test_translation_attempt.py`
- Modify: `src-python/tests/test_controller_progressive_pipeline.py`
- Modify: `src-ui/views/app/main_page/sidebar_section/language_settings/translator_selector_open_button/translator_selector/TranslatorSelector.jsx`
- Create: `src-ui/logics/common/__tests__/ctranslate2ProviderPolicy.test.js`

**Interfaces:**
- `boundedTranslationProviderSnapshot(selection) -> tuple[str, ...]` returns at most two distinct providers and removes CTranslate2 when it appears behind an online primary.
- `collapseTranslationProviderSnapshot(selection)` returns `""`, one provider string, or a two-provider list without inventing a default.
- CTranslate2-primary may retain one online secondary.

- [ ] **Step 1: Write the failing backend policy tests**

  Change/add exact assertions:

  ```python
  self.assertEqual(
      model_module.boundedTranslationProviderSnapshot(["Google", "CTranslate2"]),
      ("Google",),
  )
  self.assertEqual(
      model_module.boundedTranslationProviderSnapshot(["CTranslate2", "Bing"]),
      ("CTranslate2", "Bing"),
  )
  self.assertEqual(
      model_module.collapseTranslationProviderSnapshot(["Google", "CTranslate2"]),
      "Google",
  )
  self.assertEqual(model_module.collapseTranslationProviderSnapshot([]), "")
  ```

  Replace the legacy Google→CTranslate2 fallback expectation with `provider.providers == ["Google"]`, failed translation, and no local result. Add CTranslate2→Bing with fake responses `[False, "online translation"]` and assert both are attempted in that order.

- [ ] **Step 2: Run RED**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  ```

  Expected: the online-primary CTranslate2 filtering and collapse helper assertions fail.

- [ ] **Step 3: Implement bounded normalization**

  ```python
  def boundedTranslationProviderSnapshot(selection) -> tuple[str, ...]:
      values = [selection] if isinstance(selection, str) else selection if isinstance(selection, (list, tuple)) else []
      providers = []
      for value in values:
          if not isinstance(value, str):
              continue
          provider = value.strip()
          if provider and provider not in providers:
              providers.append(provider)
          if len(providers) == 2:
              break
      if providers and providers[0] != "CTranslate2":
          providers = [provider for provider in providers if provider != "CTranslate2"]
      return tuple(providers)

  def collapseTranslationProviderSnapshot(selection):
      providers = boundedTranslationProviderSnapshot(selection)
      if not providers:
          return ""
      return providers[0] if len(providers) == 1 else list(providers)
  ```

- [ ] **Step 4: Write RED UI policy test and implement selector filtering**

  The source test must assert that `isAvailableSecondary` rejects CTranslate2 when `primary_id !== "CTranslate2"`, and that `secondary_options` applies the same predicate. Implement one shared helper:

  ```js
  const canBeSecondary = (engine, primary_id) => (
      engine?.is_available === true
      && engine.id !== primary_id
      && (primary_id === "CTranslate2" || engine.id !== "CTranslate2")
  );
  ```

  Use it in `findFallbackSecondaryId` and `secondary_options` so the UI cannot save `[online, CTranslate2]`.

- [ ] **Step 5: Run GREEN and commit**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_controller_progressive_pipeline.py" -v
  node --test src-ui/logics/common/__tests__/ctranslate2ProviderPolicy.test.js
  git add src-python/model.py src-python/tests/test_translation_attempt.py src-python/tests/test_controller_progressive_pipeline.py src-ui/views/app/main_page/sidebar_section/language_settings/translator_selector_open_button/translator_selector/TranslatorSelector.jsx src-ui/logics/common/__tests__/ctranslate2ProviderPolicy.test.js
  git commit -m "fix: keep CTranslate2 opt-in only"
  ```

---

### Task 2: Add safe CTranslate2 load and unload lifecycle

**Files:**
- Modify: `src-python/models/translation/translation_translator.py`
- Modify: `src-python/model.py`
- Modify: `src-python/tests/test_translation_attempt.py`

**Interfaces:**
- `Translator.unloadCTranslate2Model() -> None` waits for active local inference, calls native `unload_model(to_cpu=False)`, and always clears local references/state.
- `Model.unloadTranslatorCTranslate2Model() -> None` is the controller-facing facade.
- Online provider calls never enter this condition.

- [ ] **Step 1: Write lifecycle RED tests**

  Use an Event-blocked fake native translator and assert the exact teardown
  contract:

  ```python
  class BlockingNativeTranslator:
      def __init__(self):
          self.entered = threading.Event()
          self.release = threading.Event()
          self.unload_model = Mock()

      def translate_batch(self, *_args, **_kwargs):
          self.entered.set()
          self.release.wait(WAIT_SECONDS)
          return [SimpleNamespace(hypotheses=[["prefix", "translated"]])]

  def test_unload_waits_for_active_local_inference_and_clears_references(self):
      native = BlockingNativeTranslator()
      self.translator.ctranslate2_translator = native
      self.translator.ctranslate2_tokenizer = FakeTokenizer()
      self.translator.is_loaded_ctranslate2_model = True
      translation = threading.Thread(target=lambda: self.translator.translateCTranslate2(
          "message", "ja", "en", "m2m100_418M-ct2-int8"
      ))
      translation.start()
      self.assertTrue(native.entered.wait(WAIT_SECONDS))
      unloaded = threading.Event()
      teardown = threading.Thread(target=lambda: (self.translator.unloadCTranslate2Model(), unloaded.set()))
      teardown.start()
      self.assertFalse(unloaded.wait(0.05))
      native.release.set()
      translation.join(WAIT_SECONDS)
      teardown.join(WAIT_SECONDS)
      native.unload_model.assert_called_once_with(to_cpu=False)
      self.assertIsNone(self.translator.ctranslate2_translator)
      self.assertIsNone(self.translator.ctranslate2_tokenizer)
      self.assertFalse(self.translator.isLoadedCTranslate2Model())
  ```

  Add a second test with `native.unload_model.side_effect = RuntimeError("unload")`
  and the same three cleared-state assertions. While teardown is waiting, call
  a second `translateCTranslate2` and assert it returns `False`. Patch the
  condition with a guard mock during a Google `_translate_once` call and assert
  no lifecycle method is entered.

- [ ] **Step 2: Run RED**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  ```

  Expected: `unloadCTranslate2Model` is missing and concurrent teardown assertions fail.

- [ ] **Step 3: Implement the local lifecycle condition**

  Initialize:

  ```python
  self._ctranslate2_condition = Condition(RLock())
  self._ctranslate2_active_calls = 0
  self._ctranslate2_transitioning = False
  ```

  `translateCTranslate2` must reject when transitioning/unloaded, increment active calls while holding the condition, snapshot translator/tokenizer, execute outside the lock, then decrement and notify in `finally`. `changeCTranslate2Model` must set transitioning, wait for active calls, build new objects, assign only after both load, and clear/notify on every exit.

  Implement unload:

  ```python
  def unloadCTranslate2Model(self) -> None:
      with self._ctranslate2_condition:
          self._ctranslate2_transitioning = True
          while self._ctranslate2_active_calls:
              self._ctranslate2_condition.wait()
          native = self.ctranslate2_translator
          self.ctranslate2_translator = None
          self.ctranslate2_tokenizer = None
          self.is_loaded_ctranslate2_model = False
      try:
          if native is not None:
              native.unload_model(to_cpu=False)
      except Exception:
          errorLogging()
      finally:
          with self._ctranslate2_condition:
              self._ctranslate2_transitioning = False
              self._ctranslate2_condition.notify_all()
  ```

  Add the model facade calling `self.translator.unloadCTranslate2Model()`.

- [ ] **Step 4: Run GREEN and commit**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  git add src-python/models/translation/translation_translator.py src-python/model.py src-python/tests/test_translation_attempt.py
  git commit -m "fix: manage CTranslate2 translation lifetime"
  ```

---

### Task 3: Make controller activation and provider switching transactional

**Files:**
- Modify: `src-python/controller.py`
- Modify: `src-python/tests/test_main_function_activation.py`

**Interfaces:**
- `_translation_activation_lock: RLock` serializes enable, disable, and selection transitions.
- `_ensureCTranslate2Ready(selection) -> None` acts only when CTranslate2 is primary.
- `_releaseCTranslate2() -> None` calls the model facade only when loaded.
- `setSelectedTranslationEngines(data)` normalizes every preset defensively.

- [ ] **Step 1: Write controller RED tests**

  Cover these exact outcomes with mocks:

  ```python
  # Translation off: save CT, do not load.
  self.assertEqual(controller.setSelectedTranslationEngines({"1": "CTranslate2"})["status"], 200)
  change_model.assert_not_called()

  # Translation on: cloud -> CT blocks in change_model and commits after release.
  self.assertEqual(config.SELECTED_TRANSLATION_ENGINES["1"], "Google")

  # Load failure: cloud selection and ENABLE_TRANSLATION=True survive.
  self.assertEqual(config.SELECTED_TRANSLATION_ENGINES["1"], "Google")
  self.assertIs(config.ENABLE_TRANSLATION, True)

  # CT -> Google commits Google, unloads once, then returns.
  unload_model.assert_called_once_with()

  # Disable unloads; next enable loads again.
  # ["Google", "CTranslate2"] normalizes to "Google" and never loads CT.
  # Two concurrent CT selections serialize and load only once.
  ```

- [ ] **Step 2: Run RED**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_main_function_activation.py" -v
  ```

  Expected: enabled provider switching does not load, and disable/switch-away do not unload.

- [ ] **Step 3: Implement transaction helpers and endpoint changes**

  Add `_translation_activation_lock = RLock()` in `Controller.__init__`. Make disable/selection instance methods. Under the lock:

  ```python
  def _ensureCTranslate2Ready(self, selection) -> None:
      providers = boundedTranslationProviderSnapshot(selection)
      if not providers or providers[0] != "CTranslate2":
          return
      if not model.isLoadedCTranslate2Model() or model.isChangedTranslatorParameters():
          model.changeTranslatorCTranslate2Model()
          model.setChangedTranslatorParameters(False)

  def _releaseCTranslate2(self) -> None:
      if model.isLoadedCTranslate2Model():
          model.unloadTranslatorCTranslate2Model()
  ```

  `setEnableTranslation` checks readiness before the existing enabled early return. `setDisableTranslation` sets the flag false and releases. `setSelectedTranslationEngines` normalizes with `collapseTranslationProviderSnapshot`; when enabled, load-before-commit for CT primary, but commit-before-release when leaving CT so new traces stop selecting it. The provider-switch error path must reuse activation errors without changing `ENABLE_TRANSLATION` or the previous provider.

- [ ] **Step 4: Run GREEN and commit**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_main_function_activation.py" -v
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_translation_attempt.py" -v
  git add src-python/controller.py src-python/tests/test_main_function_activation.py
  git commit -m "fix: activate CTranslate2 on provider changes"
  ```

---

### Task 4: Show provider-transition blocking overlay and settle failures

**Files:**
- Modify: `src-ui/logics/store.js`
- Modify: `src-ui/logics/main/useLanguageSettings.js`
- Modify: `src-ui/logics/common/blockingOperationState.js`
- Modify: `src-ui/logics/common/useBlockingOperation.js`
- Modify: `src-ui/logics/useReceiveRoutes.js`
- Modify: `src-ui/views/app/_app_controllers/StartPythonController.jsx`
- Modify: `src-ui/logics/common/__tests__/blockingOperationState.test.js`
- Modify: `src-ui/logics/common/__tests__/blockingOperationIntegration.test.js`
- Create: `src-ui/logics/common/__tests__/ctranslate2SelectionTransition.test.js`

**Interfaces:**
- `Atom_TranslationEngineSelectionTransition` stores `{preset_key,current,proposed}` or `null` separately from committed selection.
- `settleSelectedTranslationEngineSelection()` restores the selected atom to `ok` without changing committed data and clears the transition.
- `translationSelectionUsesCTranslate2(transition) -> boolean` checks only primary providers.

- [ ] **Step 1: Write RED state/integration tests**

  Add pure-state cases using the existing `state()` fixture:

  ```js
  assert.equal(getBlockingOperationCandidate({
      ...state(),
      translationSelectionPending: true,
  })?.id, "translation");
  assert.equal(getBlockingOperationCandidate({
      ...state(),
      translationSelectionPending: false,
  }), null);

  assert.equal(translationSelectionUsesCTranslate2({
      current: "Google",
      proposed: "CTranslate2",
  }), true);
  assert.equal(translationSelectionUsesCTranslate2({
      current: ["Google", "CTranslate2"],
      proposed: ["Google", "Bing"],
  }), false);
  ```

  In the structure/integration test, assert `setSelectedTranslationEngines`
  marks the committed atom pending without replacing its data, records
  `{preset_key,current,proposed}`, awaits the transport result, and settles on
  `ok:false`. Assert receive-route 400/500/503 settlement occurs before error
  notification, 200 uses the wrapped commit updater, and the post-ready sidecar
  close calls `settleSelectedTranslationEngineSelection()` exactly once.

- [ ] **Step 2: Run RED**

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationState.test.js src-ui/logics/common/__tests__/blockingOperationIntegration.test.js src-ui/logics/common/__tests__/ctranslate2SelectionTransition.test.js
  ```

  Expected: transition atom/helper and selection-driven overlay state are missing.

- [ ] **Step 3: Implement transition tracking and overlay derivation**

  Add the atom with initial `null`. In `useLanguageSettings`, wrap the raw selected-engine updater so backend success commits and clears transition. Before sending, keep the committed map unchanged, mark it pending, and save:

  ```js
  {
      preset_key: presetKey,
      current: currentSelectedTranslationEngines.data?.[presetKey] ?? "",
      proposed: selected_translator,
  }
  ```

  Await `asyncStdoutToPython`; on `ok:false`, call the settlement function. In receive-route 400/500/503 handling, settle when endpoint is `/set/data/selected_translation_engines` before notifying. Call the same settlement function from the post-ready sidecar close handler.

  Extend `getBlockingOperationCandidate` with `translationSelectionPending`. In `useBlockingOperation`, set it only when Translation is enabled, selected-engine state is pending, and the current/proposed primary is CTranslate2. Reuse the existing `translation` title, 250ms delay, elapsed copy, blur, and inert page boundary.

- [ ] **Step 4: Run GREEN and commit**

  ```powershell
  node --test src-ui/logics/common/__tests__/blockingOperationState.test.js src-ui/logics/common/__tests__/blockingOperationIntegration.test.js src-ui/logics/common/__tests__/ctranslate2SelectionTransition.test.js src-ui/logics/common/__tests__/ctranslate2ProviderPolicy.test.js
  npm run test:ui
  git add src-ui/logics/store.js src-ui/logics/main/useLanguageSettings.js src-ui/logics/common/blockingOperationState.js src-ui/logics/common/useBlockingOperation.js src-ui/logics/useReceiveRoutes.js src-ui/views/app/_app_controllers/StartPythonController.jsx src-ui/logics/common/__tests__
  git commit -m "feat: show CTranslate2 provider transitions"
  ```

---

### Task 5: Final verification

**Files:**
- Modify only if a scoped Critical or Important defect is found.

- [ ] **Step 1: Run all Python and UI tests**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
  npm run test:ui
  ```

- [ ] **Step 2: Compile and build production UI**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m compileall -q src-python
  npm run vite-build
  ```

- [ ] **Step 3: Verify CPU environment only if present**

  ```powershell
  if (Test-Path '.venv\Scripts\python.exe') {
      .\.venv\Scripts\python.exe -m unittest discover -s src-python/tests -p "test_*.py" -v
      .\.venv\Scripts\python.exe -m compileall -q src-python
  } else {
      Write-Output 'CPU virtual environment unavailable'
  }
  ```

- [ ] **Step 4: Build the CUDA frozen sidecar**

  ```powershell
  .\.venv_cuda\Scripts\python.exe -m PyInstaller spec/backend_cuda.spec --distpath src-tauri/bin --clean --noconfirm --log-level ERROR
  ```

- [ ] **Step 5: Run local no-network smoke and review**

  With the already installed local model only, load once, translate one fixed English sentence to Thai, unload, and assert unloaded state/references without downloading. Then review the branch diff for lifecycle races, online-path regression, pending UI state, and shared-GPU cache interference. Fix and re-run only Critical or Important findings.
