import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const read = (relativePath) => fs.readFileSync(path.join(repoRoot, relativePath), "utf8");

const storeSource = read("src-ui/logics/store.js");
const languageSource = read("src-ui/logics/main/useLanguageSettings.js");
const blockingSource = read("src-ui/logics/common/useBlockingOperation.js");
const receiveSource = read("src-ui/logics/useReceiveRoutes.js");
const startSource = read("src-ui/views/app/_app_controllers/StartPythonController.jsx");

test("provider selection keeps committed and proposed values separate", () => {
    assert.match(storeSource, /Atom_TranslationEngineSelectionTransition/);
    assert.match(storeSource, /useStore_TranslationEngineSelectionTransition/);
    assert.match(languageSource, /currentTranslationEngineSelectionTransition/);
    assert.match(languageSource, /preset_key:\s*presetKey/);
    assert.match(languageSource, /current:\s*currentSelection/);
    assert.match(languageSource, /proposed:\s*selected_translator/);
    assert.match(languageSource, /pendingSelectedTranslationEngines\(\)/);
    assert.match(languageSource, /await\s+asyncStdoutToPython\(\s*["']\/set\/data\/selected_translation_engines["']/);
    assert.match(languageSource, /if\s*\(\s*!transportResult\.ok\s*\)/);
});

test("provider transition settlement covers backend results and disconnect", () => {
    assert.match(languageSource, /const\s+settleSelectedTranslationEngineSelection\s*=/);
    assert.match(languageSource, /updateTranslationEngineSelectionTransition\(null\)/);
    assert.match(
        receiveSource,
        /endpoint\s*===\s*["']\/set\/data\/selected_translation_engines["'][\s\S]*?settleSelectedTranslationEngineSelection/,
    );
    assert.match(startSource, /settleSelectedTranslationEngineSelection/);
    const closeStart = startSource.indexOf('command.on("close"');
    const stdoutStart = startSource.indexOf('command.stdout.on("data"', closeStart);
    const closeSource = startSource.slice(closeStart, stdoutStart);
    assert.match(closeSource, /clearPendingMainFunctionStatuses\(\)/);
    assert.match(closeSource, /settleSelectedTranslationEngineSelection\(\)/);
});

test("blocking hook derives CTranslate2 selection transitions", () => {
    assert.match(blockingSource, /useLanguageSettings\(\)/);
    assert.match(blockingSource, /translationSelectionUsesCTranslate2\(/);
    assert.match(blockingSource, /currentTranslationStatus\.data\s*===\s*true/);
    assert.match(blockingSource, /currentSelectedTranslationEngines\.state\s*===\s*["']pending["']/);
    assert.match(blockingSource, /translationSelectionPending/);
});
