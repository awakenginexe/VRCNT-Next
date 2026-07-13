import test from "node:test";
import assert from "node:assert/strict";

import {
    BLOCKING_OPERATION_DELAY_MS,
    getBlockingOperationCandidate,
    getMainFunctionPendingCopyKey,
    readBooleanBackendResult,
    resolveFailedMainFunction,
} from "../blockingOperationState.js";

const createInput = (overrides = {}) => ({
    isBackendReady: true,
    initStatus: {
        phase: "ready",
        message: "Ready",
        detail: "",
        message_key: "",
        detail_key: "",
    },
    initProgress: 4,
    translationStatus: { state: "ok", data: false },
    transcriptionSendStatus: { state: "ok", data: false },
    transcriptionReceiveStatus: { state: "ok", data: false },
    ...overrides,
});

test("startup wins over simultaneous activations and blocks immediately", () => {
    const operation = getBlockingOperationCandidate(createInput({
        isBackendReady: false,
        initStatus: {
            phase: "local",
            message: "Checking",
            detail: "Preparing local services",
            message_key: "",
            detail_key: "",
        },
        initProgress: 1,
        translationStatus: { state: "pending", data: false },
        transcriptionSendStatus: { state: "pending", data: false },
        transcriptionReceiveStatus: { state: "pending", data: false },
    }));

    assert.equal(operation.id, "startup");
    assert.equal(operation.delayMs, 0);
    assert.equal(operation.titleKey, "blocking_operation.startup_operation");
    assert.equal(operation.phase, "Checking");
    assert.equal(operation.detail, "Preparing local services");
    assert.deepEqual(operation.progress, {
        kind: "determinate",
        value: 1,
        max: 4,
    });
});

test("startup progress is clamped at zero", () => {
    const operation = getBlockingOperationCandidate(createInput({
        isBackendReady: false,
        initProgress: -1,
    }));

    assert.deepEqual(operation.progress, {
        kind: "determinate",
        value: 0,
        max: 4,
    });
});

test("backend readiness removes startup immediately", () => {
    assert.equal(getBlockingOperationCandidate(createInput()), null);
});

test("a terminal startup error removes blocking even before backend readiness", () => {
    const operation = getBlockingOperationCandidate(createInput({
        isBackendReady: false,
        initStatus: {
            phase: "error",
            message: "",
            detail: "",
            message_key: "blocking_operation.startup_failed",
            detail_key: "blocking_operation.startup_failed_detail",
        },
        translationStatus: { state: "pending", data: false },
    }));

    assert.equal(operation, null);
});

test("only a pending activation whose old value is false is selected", () => {
    const operation = getBlockingOperationCandidate(createInput({
        translationStatus: { state: "pending", data: false },
    }));

    assert.equal(operation.id, "translation");
    assert.equal(operation.titleKey, "main_page.translation");
    assert.equal(operation.delayMs, 250);
    assert.deepEqual(operation.progress, { kind: "indeterminate" });
});

test("a pending deactivation whose old value is true is ignored", () => {
    const operation = getBlockingOperationCandidate(createInput({
        translationStatus: { state: "pending", data: true },
        transcriptionSendStatus: { state: "pending", data: true },
        transcriptionReceiveStatus: { state: "pending", data: true },
    }));

    assert.equal(operation, null);
});

test("foreground pending state is not a blocking-operation input", () => {
    const operation = getBlockingOperationCandidate({
        ...createInput(),
        foregroundStatus: { state: "pending", data: false },
    });

    assert.equal(operation, null);
});

test("activation priority is translation, then send, then receive", () => {
    const allPending = createInput({
        translationStatus: { state: "pending", data: false },
        transcriptionSendStatus: { state: "pending", data: false },
        transcriptionReceiveStatus: { state: "pending", data: false },
    });
    assert.equal(getBlockingOperationCandidate(allPending).id, "translation");

    assert.equal(getBlockingOperationCandidate({
        ...allPending,
        translationStatus: { state: "ok", data: false },
    }).id, "transcription_send");

    assert.equal(getBlockingOperationCandidate({
        ...allPending,
        translationStatus: { state: "ok", data: false },
        transcriptionSendStatus: { state: "ok", data: false },
    }).id, "transcription_receive");
});

test("every activation uses the exact blocking delay", () => {
    assert.equal(BLOCKING_OPERATION_DELAY_MS, 250);

    const statusFields = [
        "translationStatus",
        "transcriptionSendStatus",
        "transcriptionReceiveStatus",
    ];
    for (const statusField of statusFields) {
        const operation = getBlockingOperationCandidate(createInput({
            [statusField]: { state: "pending", data: false },
        }));
        assert.equal(operation.delayMs, BLOCKING_OPERATION_DELAY_MS, statusField);
    }
});

test("pending copy changes at exactly five and thirty seconds", () => {
    const operationIds = [
        "translation",
        "transcription_send",
        "transcription_receive",
    ];

    for (const operationId of operationIds) {
        assert.equal(
            getMainFunctionPendingCopyKey(operationId, 4_999),
            `main_page.main_function_pending.${operationId}_start`,
        );
        assert.equal(
            getMainFunctionPendingCopyKey(operationId, 5_000),
            `main_page.main_function_pending.${operationId}_warm`,
        );
        assert.equal(
            getMainFunctionPendingCopyKey(operationId, 29_999),
            `main_page.main_function_pending.${operationId}_warm`,
        );
        assert.equal(
            getMainFunctionPendingCopyKey(operationId, 30_000),
            `main_page.main_function_pending.${operationId}_long`,
        );
    }
});

test("failed main-function endpoints resolve to their operation", () => {
    const endpointCases = {
        translation: [
            "/set/enable/translation",
            "/set/disable/translation",
            "/run/enable_translation",
        ],
        transcription_send: [
            "/set/enable/transcription_send",
            "/set/disable/transcription_send",
            "/run/enable_transcription_send",
        ],
        transcription_receive: [
            "/set/enable/transcription_receive",
            "/set/disable/transcription_receive",
            "/run/enable_transcription_receive",
        ],
    };

    for (const [operationId, endpoints] of Object.entries(endpointCases)) {
        for (const endpoint of endpoints) {
            assert.equal(
                resolveFailedMainFunction({ endpoint, errorCode: "UNKNOWN" }),
                operationId,
                endpoint,
            );
        }
    }
});

test("failed main-function error codes resolve to their operation", () => {
    const errorCodeCases = {
        translation: [
            "TRANSLATION_VRAM_ENABLE",
            "TRANSLATION_DISABLED_VRAM",
        ],
        transcription_send: [
            "DEVICE_NO_MIC",
            "TRANSCRIPTION_VRAM_MIC",
            "TRANSCRIPTION_SEND_DISABLED_VRAM",
        ],
        transcription_receive: [
            "DEVICE_NO_SPEAKER",
            "TRANSCRIPTION_VRAM_SPEAKER",
            "TRANSCRIPTION_RECEIVE_DISABLED_VRAM",
        ],
    };

    for (const [operationId, errorCodes] of Object.entries(errorCodeCases)) {
        for (const errorCode of errorCodes) {
            assert.equal(
                resolveFailedMainFunction({ endpoint: "/unknown", errorCode }),
                operationId,
                errorCode,
            );
        }
    }
});

test("unknown endpoint and error-code pairs do not resolve", () => {
    assert.equal(resolveFailedMainFunction({
        endpoint: "/set/enable/foreground",
        errorCode: "UNKNOWN",
    }), null);
    assert.equal(resolveFailedMainFunction({
        endpoint: "/set/enable/translation/extra",
        errorCode: "TRANSLATION_ENABLE_FAILED",
    }), null);
});

test("backend boolean results accept only booleans at the supported locations", () => {
    assert.equal(readBooleanBackendResult(true), true);
    assert.equal(readBooleanBackendResult(false), false);
    assert.equal(readBooleanBackendResult({ data: false }), false);
    assert.equal(readBooleanBackendResult({ data: true }), true);

    for (const value of [
        "true",
        "false",
        null,
        undefined,
        {},
        { data: "true" },
        { data: 0 },
    ]) {
        assert.equal(readBooleanBackendResult(value), undefined);
    }
});
