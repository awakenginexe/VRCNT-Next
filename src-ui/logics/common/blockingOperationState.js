export const BLOCKING_OPERATION_DELAY_MS = 250;
export const WARM_OPERATION_MS = 5_000;
export const LONG_OPERATION_MS = 30_000;

const primaryTranslationProvider = (selection) => (
    Array.isArray(selection) ? selection[0] : selection
);

export const translationSelectionUsesCTranslate2 = (transition) => (
    primaryTranslationProvider(transition?.current) === "CTranslate2"
    || primaryTranslationProvider(transition?.proposed) === "CTranslate2"
);

const ACTIVATION_OPERATIONS = [
    {
        id: "translation",
        statusField: "translationStatus",
        titleKey: "main_page.translation",
    },
    {
        id: "transcription_send",
        statusField: "transcriptionSendStatus",
        titleKey: "main_page.transcription_send",
    },
    {
        id: "transcription_receive",
        statusField: "transcriptionReceiveStatus",
        titleKey: "main_page.transcription_receive",
    },
];

const FAILED_MAIN_FUNCTION_BY_ENDPOINT = new Map([
    ["/set/enable/translation", "translation"],
    ["/set/disable/translation", "translation"],
    ["/run/enable_translation", "translation"],
    ["/set/enable/transcription_send", "transcription_send"],
    ["/set/disable/transcription_send", "transcription_send"],
    ["/run/enable_transcription_send", "transcription_send"],
    ["/set/enable/transcription_receive", "transcription_receive"],
    ["/set/disable/transcription_receive", "transcription_receive"],
    ["/run/enable_transcription_receive", "transcription_receive"],
]);

const FAILED_MAIN_FUNCTION_BY_ERROR_CODE = new Map([
    ["TRANSLATION_VRAM_ENABLE", "translation"],
    ["TRANSLATION_DISABLED_VRAM", "translation"],
    ["DEVICE_NO_MIC", "transcription_send"],
    ["TRANSCRIPTION_VRAM_MIC", "transcription_send"],
    ["TRANSCRIPTION_SEND_DISABLED_VRAM", "transcription_send"],
    ["DEVICE_NO_SPEAKER", "transcription_receive"],
    ["TRANSCRIPTION_VRAM_SPEAKER", "transcription_receive"],
    ["TRANSCRIPTION_RECEIVE_DISABLED_VRAM", "transcription_receive"],
]);

export const getBlockingOperationCandidate = ({
    isBackendReady,
    initStatus,
    initProgress,
    translationStatus,
    transcriptionSendStatus,
    transcriptionReceiveStatus,
    translationSelectionPending = false,
}) => {
    if (initStatus?.phase === "error") return null;

    if (isBackendReady !== true) {
        return {
            id: "startup",
            titleKey: "blocking_operation.startup_operation",
            phase: initStatus?.message ?? "",
            detail: initStatus?.detail ?? "",
            phaseKey: initStatus?.message_key ?? "",
            detailKey: initStatus?.detail_key ?? "",
            delayMs: 0,
            progress: {
                kind: "determinate",
                value: Math.max(0, initProgress),
                max: 4,
            },
        };
    }

    for (const operation of ACTIVATION_OPERATIONS) {
        const status = {
            translationStatus,
            transcriptionSendStatus,
            transcriptionReceiveStatus,
        }[operation.statusField];
        const activationPending = status?.state === "pending"
            && status.data === false;
        const selectionPending = operation.id === "translation"
            && translationSelectionPending;
        if (activationPending || selectionPending) {
            return {
                id: operation.id,
                titleKey: operation.titleKey,
                delayMs: BLOCKING_OPERATION_DELAY_MS,
                progress: { kind: "indeterminate" },
            };
        }
    }

    return null;
};

export const getMainFunctionPendingCopyKey = (operationId, elapsedMs) => {
    const phase = elapsedMs >= LONG_OPERATION_MS
        ? "long"
        : elapsedMs >= WARM_OPERATION_MS ? "warm" : "start";
    return `main_page.main_function_pending.${operationId}_${phase}`;
};

export const resolveFailedMainFunction = ({ endpoint, errorCode }) => (
    FAILED_MAIN_FUNCTION_BY_ENDPOINT.get(endpoint)
    ?? FAILED_MAIN_FUNCTION_BY_ERROR_CODE.get(errorCode)
    ?? null
);

export const readBooleanBackendResult = (result) => {
    if (typeof result === "boolean") return result;
    if (result && typeof result === "object" && typeof result.data === "boolean") {
        return result.data;
    }
    return undefined;
};
