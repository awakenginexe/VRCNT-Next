import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

const hookPath = "src-ui/logics/common/useBlockingOperation.js";
const mainFunctionPath = "src-ui/logics/main/useMainFunction.js";
const receiveRoutesPath = "src-ui/logics/useReceiveRoutes.js";
const startPythonPath = (
    "src-ui/views/app/_app_controllers/StartPythonController.jsx"
);
const switchPath = (
    "src-ui/views/app/main_page/sidebar_section/main_function_switch/"
    + "MainFunctionSwitch.jsx"
);
const switchStylesPath = (
    "src-ui/views/app/main_page/sidebar_section/main_function_switch/"
    + "MainFunctionSwitch.module.scss"
);

test("the blocking hook derives timing from all existing operation state", () => {
    assert.equal(
        fs.existsSync(path.join(repoRoot, hookPath)),
        true,
        "useBlockingOperation.js must derive the overlay state",
    );

    const source = readSource(hookPath);
    for (const stateName of [
        "currentIsBackendReady",
        "currentInitStatus",
        "currentInitProgress",
        "currentTranslationStatus",
        "currentTranscriptionSendStatus",
        "currentTranscriptionReceiveStatus",
    ]) {
        assert.match(source, new RegExp(`\\b${stateName}\\b`), stateName);
    }

    for (const operationId of [
        "startup",
        "translation",
        "transcription_send",
        "transcription_receive",
    ]) {
        assert.match(source, new RegExp(`\\b${operationId}\\b`), operationId);
    }

    const activeByIdSource = source.match(
        /const activeById = \{([\s\S]*?)\n    \};/,
    )?.[1] ?? "";
    assert.match(
        activeByIdSource,
        /startup:[\s\S]*?currentIsBackendReady\.data\s*!==\s*true[\s\S]*?currentInitStatus\.data\.phase\s*!==\s*["']error["']/,
    );
    for (const [operationId, statusName] of [
        ["translation", "currentTranslationStatus"],
        ["transcription_send", "currentTranscriptionSendStatus"],
        ["transcription_receive", "currentTranscriptionReceiveStatus"],
    ]) {
        assert.match(
            activeByIdSource,
            new RegExp(
                `${operationId}:\\s*${statusName}\\.state\\s*===\\s*["']pending["']`
                + `[\\s\\S]*?${statusName}\\.data\\s*===\\s*false`,
            ),
            operationId,
        );
    }

    assert.match(source, /startedAtByOperationRef\s*=\s*useRef\(\{\}\)/);
    const timestampTrackingSource = source.match(
        /Object\.entries\(activeById\)\.forEach\(\(\[id, active\]\)\s*=>\s*\{([\s\S]*?)\n        \}\);/,
    )?.[1] ?? "";
    assert.match(
        timestampTrackingSource,
        /active\s*&&\s*startedAtByOperationRef\.current\[id\]\s*===\s*undefined/,
    );
    assert.match(
        timestampTrackingSource,
        /startedAtByOperationRef\.current\[id\]\s*=\s*observedAt/,
    );
    assert.match(
        timestampTrackingSource,
        /else if\s*\(\s*!active\s*\)[\s\S]*?delete\s+startedAtByOperationRef\.current\[id\]/,
    );
    assert.doesNotMatch(timestampTrackingSource, /candidate/);
    assert.ok(
        source.indexOf("Object.entries(activeById)")
            < source.indexOf("const candidate = getBlockingOperationCandidate"),
        "every active operation must be timestamped before display priority is selected",
    );
    assert.match(source, /getBlockingOperationCandidate\(\{/);
    assert.equal((source.match(/setTimeout\(/g) ?? []).length, 1);
    assert.equal((source.match(/clearTimeout\(timer\)/g) ?? []).length, 1);
    assert.equal((source.match(/setInterval\(/g) ?? []).length, 1);
    assert.equal((source.match(/clearInterval\(timer\)/g) ?? []).length, 1);
    assert.match(source, /return\s*\(\)\s*=>\s*clearTimeout\(timer\)/);
    assert.match(source, /setInterval\([\s\S]*?,\s*1_000\)/);
    assert.match(source, /return\s*\(\)\s*=>\s*clearInterval\(timer\)/);
    assert.match(source, /elapsedMs\s*>?=\s*candidate\.delayMs/);
    assert.doesNotMatch(
        source,
        /from\s+["']jotai["']|\b(?:atom|useAtom|useAtomValue|useSetAtom)\s*\(/,
    );
});

test("the common barrel exports the derived blocking hook", () => {
    const source = readSource("src-ui/logics/common/index.js");
    assert.match(
        source,
        /export\s+\{\s*useBlockingOperation\s*\}\s+from\s+["']\.\/useBlockingOperation["'];/,
    );
});

test("stdout transport distinguishes successful, missing, and rejected writes", () => {
    const source = readSource("src-ui/logics/common/useStdoutToPython.js");

    const missingStart = source.indexOf("if (!backend_subprocess)");
    const writeTryStart = source.indexOf("try {", missingStart);
    const rejectedStart = source.indexOf("} catch (cause) {", writeTryStart);
    assert.ok(missingStart >= 0 && writeTryStart > missingStart);
    assert.ok(rejectedStart > writeTryStart);

    const missingBranch = source.slice(missingStart, writeTryStart);
    assert.match(
        missingBranch,
        /const\s+error\s*=\s*new\s+Error\(["']Backend subprocess is not found\.["']\)/,
    );
    assert.match(missingBranch, /return\s+\{\s*ok:\s*false,\s*error\s*\}/);

    const successfulWriteBranch = source.slice(writeTryStart, rejectedStart);
    const writeIndex = successfulWriteBranch.indexOf("await backend_subprocess.write");
    const successIndex = successfulWriteBranch.indexOf("return { ok: true }");
    assert.ok(
        writeIndex >= 0 && writeIndex < successIndex,
        "a successful result must follow the completed write",
    );

    const rejectedWriteBranch = source.slice(rejectedStart);
    assert.match(
        rejectedWriteBranch,
        /const\s+error\s*=\s*cause\s+instanceof\s+Error\s*\?\s*cause\s*:\s*new\s+Error\(String\(cause\)\)/,
    );
    assert.match(
        rejectedWriteBranch,
        /return\s+\{\s*ok:\s*false,\s*error\s*\}/,
    );
});

test("main-function failures restore the initiating atom with functional updates", () => {
    const source = readSource(mainFunctionPath);

    const transportCall = source.indexOf("const transportResult = await asyncStdoutToPython");
    const localFailureStart = source.indexOf("if (!transportResult.ok)", transportCall);
    const localFailureEnd = source.indexOf("\n            }\n        };", localFailureStart);
    assert.ok(transportCall >= 0 && localFailureStart > transportCall);
    assert.ok(localFailureEnd > localFailureStart);
    const localFailureBranch = source.slice(localFailureStart, localFailureEnd);
    const restoreIndex = localFailureBranch.indexOf(
        "updateStatus((current) => current.data)",
    );
    const notifyIndex = localFailureBranch.indexOf("showNotification_Error");
    assert.ok(
        restoreIndex >= 0 && restoreIndex < notifyIndex,
        "local transport failure must restore the initiating status before notifying",
    );
    assert.match(
        localFailureBranch,
        /t\(["']blocking_operation\.backend_unavailable["']\)/,
    );

    for (const [operationId, currentStatus, pendingStatus, updateStatus] of [
        [
            "translation",
            "currentTranslationStatus",
            "pendingTranslationStatus",
            "updateTranslationStatus",
        ],
        [
            "transcription_send",
            "currentTranscriptionSendStatus",
            "pendingTranscriptionSendStatus",
            "updateTranscriptionSendStatus",
        ],
        [
            "transcription_receive",
            "currentTranscriptionReceiveStatus",
            "pendingTranscriptionReceiveStatus",
            "updateTranscriptionReceiveStatus",
        ],
    ]) {
        assert.match(
            source,
            new RegExp(
                `createTogglePair\\(\\s*${currentStatus},\\s*${pendingStatus},`
                + `\\s*${updateStatus},\\s*["']${operationId}["'],?\\s*\\)`,
            ),
            `${operationId} must restore its own updater`,
        );
    }

    assert.match(
        source,
        /const\s+clearPendingMainFunctionError\s*=\s*\(\{\s*endpoint,\s*errorCode,\s*result\s*\}\)\s*=>/,
    );
    assert.match(source, /resolveFailedMainFunction\(\{\s*endpoint,\s*errorCode\s*\}\)/);
    assert.match(source, /readBooleanBackendResult\(result\)/);
    assert.match(source, /if\s*\(\s*!operation\s*\)\s*return false/);
    assert.match(
        source,
        /updateStatusFor\(operation\)\(\(current\)\s*=>\s*backendValue\s*\?\?\s*current\.data\)/,
    );
    assert.match(source, /updateStatusFor\(operation\)[\s\S]*?return true/);
    assert.match(source, /clearPendingMainFunctionError,/);
});

test("receive errors clear main-function pending state before notifying", () => {
    const source = readSource(receiveRoutesPath);
    const status400 = source.match(/case\s+400:([\s\S]*?)break;/)?.[1] ?? "";
    const status500 = source.match(/case\s+500:([\s\S]*?)break;/)?.[1] ?? "";
    const clearPayloadPattern = (
        /hook_results\.useMainFunction\?\.clearPendingMainFunctionError\?\.\(\{\s*endpoint,\s*errorCode:\s*result\?\.error_code,\s*result,?\s*\}\)/
    );

    const clear400 = status400.indexOf("clearPendingMainFunctionError");
    const handle400 = status400.indexOf("errorHandling_Backend");
    assert.ok(clear400 >= 0, "400 responses must settle the initiating toggle");
    assert.match(
        status400,
        clearPayloadPattern,
        "400 settlement must receive endpoint, backend error code, and result",
    );
    assert.ok(
        clear400 < handle400,
        "400 settlement must happen before backend error handling",
    );

    const clear500 = status500.indexOf("clearPendingMainFunctionError");
    const notify500 = status500.indexOf("showNotification_Error");
    assert.ok(clear500 >= 0, "500 responses must settle the initiating toggle");
    assert.match(
        status500,
        clearPayloadPattern,
        "500 settlement must receive endpoint, backend error code, and result",
    );
    assert.ok(
        clear500 < notify500,
        "500 settlement must happen before the generic notification",
    );
});

test("sidecar startup failures produce terminal InitStatus and deduplicated copy", () => {
    const source = readSource(startPythonPath);

    const hookStart = source.indexOf("const useStartPython = () => {");
    const markerStart = source.indexOf("const markBackendStartupError", hookStart);
    const renderScopeSetup = source.slice(hookStart, markerStart);
    assert.match(renderScopeSetup, /backendReadyRef\s*=\s*useRef\(/);
    assert.match(
        renderScopeSetup,
        /backendReadyRef\.current\s*=\s*currentIsBackendReady\.data/,
        "the long-lived event callbacks must read a ref refreshed at render scope",
    );
    assert.match(source, /const\s+markBackendStartupError\s*=/);
    assert.match(source, /updateInitStatus\(\{[\s\S]*?phase:\s*["']error["']/);
    assert.match(source, /message_key:\s*["']blocking_operation\.startup_failed["']/);
    assert.match(
        source,
        /detail_key:\s*["']blocking_operation\.startup_failed_detail["']/,
    );
    const startFunctionStart = source.indexOf("const asyncStartPython", markerStart);
    const markerSource = source.slice(markerStart, startFunctionStart);
    assert.match(markerSource, /if\s*\(\s*!startupErrorNotifiedRef\.current\s*\)/);
    assert.equal((markerSource.match(/showNotification_Error\(/g) ?? []).length, 1);
    assert.match(markerSource, /category_id:\s*["']backend_startup_failed["']/);

    const errorHandlerStart = source.indexOf('command.on("error"');
    const closeHandlerStart = source.indexOf('command.on("close"');
    const stdoutHandlerStart = source.indexOf('command.stdout.on("data"');
    assert.ok(
        errorHandlerStart >= 0
            && closeHandlerStart > errorHandlerStart
            && stdoutHandlerStart > closeHandlerStart,
    );

    const errorHandlerSource = source.slice(errorHandlerStart, closeHandlerStart);
    assert.match(
        errorHandlerSource,
        /command\.on\(["']error["'],\s*\(error\)\s*=>\s*\{[\s\S]*?markBackendStartupError\(error\)/,
        "the command error event must terminate startup",
    );

    const closeHandlerSource = source.slice(closeHandlerStart, stdoutHandlerStart);
    const preReadyCheck = closeHandlerSource.indexOf(
        "if (backendReadyRef.current !== true)",
    );
    const earlyCloseMark = closeHandlerSource.indexOf(
        "markBackendStartupError(termination)",
    );
    const earlyCloseReturn = closeHandlerSource.indexOf("return;", earlyCloseMark);
    const disconnectedNotice = closeHandlerSource.indexOf(
        't("blocking_operation.backend_disconnected")',
    );
    assert.ok(
        preReadyCheck >= 0
            && preReadyCheck < earlyCloseMark
            && earlyCloseMark < earlyCloseReturn
            && earlyCloseReturn < disconnectedNotice,
        "only pre-readiness close marks startup error; post-readiness close notifies",
    );
    assert.equal(
        (closeHandlerSource.match(/markBackendStartupError\(/g) ?? []).length,
        1,
    );

    const spawnCall = source.indexOf("const backend_subprocess = await command.spawn()");
    const spawnTryStart = source.lastIndexOf("try {", spawnCall);
    const spawnSource = source.slice(spawnTryStart, source.indexOf("\n    };", spawnCall));
    assert.ok(spawnTryStart >= 0 && spawnCall > spawnTryStart);
    assert.match(
        spawnSource,
        /catch\s*\(error\)\s*\{\s*markBackendStartupError\(error\);\s*throw error;/,
        "spawn rejection must terminate startup before propagating",
    );

    assert.doesNotMatch(source, /updateIsBackendReady\(false\)/);
});

test("main-function switches keep native semantics and pending focus identity", () => {
    const source = readSource(switchPath);
    const styles = readSource(switchStylesPath);

    const switchItemsSource = source.match(
        /const switch_items = \[([\s\S]*?)\n    \];/,
    )?.[1] ?? "";
    for (const operationId of [
        "translation",
        "transcription_send",
        "transcription_receive",
    ]) {
        const itemSource = switchItemsSource.match(
            new RegExp(
                `\\{\\s*switch_id:\\s*["']${operationId}["']([\\s\\S]*?)\\n        \\},`,
            ),
        )?.[1] ?? "";
        assert.match(
            itemSource,
            /isDisabled:\s*currentIsBackendReady\.data\s*!==\s*true/,
            `${operationId} native disabled state must depend only on backend readiness`,
        );
        assert.doesNotMatch(itemSource, /pending/);
    }
    const foregroundItemSource = switchItemsSource.match(
        /\{\s*switch_id:\s*["']foreground["']([\s\S]*?)\n        \},/,
    )?.[1] ?? "";
    assert.match(foregroundItemSource, /isDisabled:\s*false/);

    assert.match(source, /getMainFunctionPendingCopyKey\(/);
    assert.doesNotMatch(source, /pending_messages|translation_start|foreground_long/);
    assert.equal((source.match(/<button\b/g) ?? []).length, 1);
    assert.equal((source.match(/<\/button>/g) ?? []).length, 1);
    const buttonOpeningTag = source.match(/<button\b[\s\S]*?>/)?.[0] ?? "";
    const buttonContent = source.slice(
        source.indexOf("<button"),
        source.indexOf("</button>") + "</button>".length,
    );
    assert.doesNotMatch(
        buttonContent,
        /<(?:div|p)\b/,
        "native button descendants must remain valid phrasing content",
    );
    assert.match(buttonOpeningTag, /type=["']button["']/);
    assert.match(buttonOpeningTag, /role=["']switch["']/);
    assert.match(
        buttonOpeningTag,
        /aria-checked=\{currentState\.data\s*===\s*true\}/,
    );
    assert.match(
        buttonOpeningTag,
        /aria-busy=\{currentState\.state\s*===\s*["']pending["']\}/,
    );
    assert.match(buttonOpeningTag, /\sdisabled=\{isDisabled\}/);
    assert.match(
        buttonOpeningTag,
        /aria-disabled=\{currentState\.state\s*===\s*["']pending["']\}/,
    );
    assert.match(
        source,
        /if\s*\(isDisabled\s*\|\|\s*currentState\.state\s*===\s*["']pending["']\)\s*return/,
    );
    assert.doesNotMatch(buttonOpeningTag, /\sdisabled=\{[^}\n]*pending/);
    assert.doesNotMatch(buttonOpeningTag, /tabIndex=\{?-1\}?/);
    assert.doesNotMatch(source, /\.blur\(\)/);

    assert.match(styles, /appearance:\s*none/);
    assert.match(styles, /font:\s*inherit/);
    assert.match(styles, /text-align:\s*left/);
    assert.match(styles, /:focus-visible/);
    assert.doesNotMatch(styles, /pointer-events:\s*none/);
});
