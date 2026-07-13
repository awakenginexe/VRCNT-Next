export const TRANSLATION_ACTIVE_STATUSES = new Set([
    "queued",
    "sending",
    "fallback",
]);

export const TRANSLATION_TERMINAL_STATUSES = new Set([
    "success",
    "timeout",
    "error",
    "skipped_overload",
]);

const TRANSLATION_STATUS_KEY_PREFIX = "main_page.message_log.translation_status";
const PATCHABLE_TRANSLATION_FIELDS = [
    "message",
    "transliteration",
    "status",
    "duration_ms",
    "queue_position",
    "error_code",
];

const hasOwn = (value, property) => Object.hasOwn(value, property);

const generateTimeData = () => new Date().toLocaleTimeString(
    "ja-JP",
    { hour12: false, hour: "2-digit", minute: "2-digit" },
);

const normalizeTranslation = (entry, index, nowMs) => ({
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
});

export const createMessageLogEntry = (payload, category, options = {}) => {
    const data = payload ?? {};
    const nowMs = options.nowMs ?? Date.now();

    return {
        id: options.id ?? crypto.randomUUID(),
        created_at: options.createdAt ?? generateTimeData(),
        category,
        status: "ok",
        trace_id: data.trace_id ?? null,
        messages: {
            original: data.original ?? { message: null, transliteration: [] },
            translations: Array.isArray(data.translations)
                ? data.translations.map((entry, index) => normalizeTranslation(
                    entry ?? {},
                    index,
                    nowMs,
                ))
                : [],
        },
    };
};

export const isTranslationTransitionAllowed = (currentStatus, nextStatus) => !(
    TRANSLATION_TERMINAL_STATUSES.has(currentStatus)
    && TRANSLATION_ACTIVE_STATUSES.has(nextStatus)
);

const patchTranslation = (current, payload, nowMs) => {
    if (
        hasOwn(payload, "status")
        && !isTranslationTransitionAllowed(current.status, payload.status)
    ) {
        return current;
    }

    let changed = false;
    const next = { ...current };

    for (const field of PATCHABLE_TRANSLATION_FIELDS) {
        if (hasOwn(payload, field) && !Object.is(current[field], payload[field])) {
            next[field] = payload[field];
            changed = true;
        }
    }

    if (hasOwn(payload, "engine") && !Object.is(current.engine, payload.engine)) {
        next.previous_engine = current.engine;
        next.engine = payload.engine;
        changed = true;
    }

    const statusChanged = !Object.is(next.status, current.status);
    const engineChanged = !Object.is(next.engine, current.engine);
    if (statusChanged || engineChanged) {
        next.status_changed_at_ms = nowMs;
    }

    return changed ? next : current;
};

export const mergeTranslationUpdateByTrace = (logs, payload, nowMs) => {
    if (!Array.isArray(logs) || payload == null || payload.target_slot == null) {
        return logs;
    }

    const logIndex = logs.findIndex((entry) => entry?.trace_id === payload.trace_id);
    if (logIndex < 0) return logs;

    const log = logs[logIndex];
    const translations = log?.messages?.translations;
    if (!Array.isArray(translations)) return logs;

    const targetSlot = String(payload.target_slot);
    const translationIndex = translations.findIndex(
        (entry) => String(entry?.target_slot) === targetSlot,
    );
    if (translationIndex < 0) return logs;

    const currentTranslation = translations[translationIndex];
    const nextTranslation = patchTranslation(currentTranslation, payload, nowMs);
    if (nextTranslation === currentTranslation) return logs;

    const nextTranslations = [...translations];
    nextTranslations[translationIndex] = nextTranslation;

    const nextLogs = [...logs];
    nextLogs[logIndex] = {
        ...log,
        messages: {
            ...log.messages,
            translations: nextTranslations,
        },
    };
    return nextLogs;
};

export const formatDurationMs = (durationMs) => {
    const numericDuration = Number(durationMs);
    const normalizedDuration = Number.isFinite(numericDuration)
        ? Math.max(0, numericDuration)
        : 0;

    if (normalizedDuration < 1_000) {
        return `${Math.round(normalizedDuration)}ms`;
    }
    return `${(normalizedDuration / 1_000).toFixed(1)}s`;
};

const statusKey = (name) => `${TRANSLATION_STATUS_KEY_PREFIX}.${name}`;

export const getTranslationPresentation = (entry, nowMs = Date.now()) => {
    const translation = entry ?? {};
    const status = translation.status;
    const statusChangedAt = Number(translation.status_changed_at_ms);
    const activeElapsedMs = Number.isFinite(statusChangedAt)
        ? Math.max(0, nowMs - statusChangedAt)
        : 0;
    const terminalElapsedMs = Number.isFinite(Number(translation.duration_ms))
        ? Math.max(0, Number(translation.duration_ms))
        : 0;
    const isActive = TRANSLATION_ACTIVE_STATUSES.has(status);
    const elapsedMs = isActive ? activeElapsedMs : terminalElapsedMs;
    const showQueuePosition = isActive
        && Number(translation.queue_position) > 0;

    if (translation.error_code === "no_provider_configured") {
        return {
            tone: "error",
            textKey: statusKey("no_provider"),
            textValues: {},
            elapsedMs,
            showQueuePosition: false,
        };
    }

    switch (status) {
        case "queued":
        case "sending":
            return {
                tone: "pending",
                textKey: statusKey(status),
                textValues: {
                    engine: translation.engine ?? "",
                    elapsed: formatDurationMs(elapsedMs),
                },
                elapsedMs,
                showQueuePosition,
            };
        case "fallback":
            return {
                tone: "warning",
                textKey: statusKey("fallback"),
                textValues: {
                    engine: translation.engine ?? "",
                    previousEngine: translation.previous_engine ?? "",
                },
                elapsedMs,
                showQueuePosition,
            };
        case "success":
            return {
                tone: "success",
                textKey: statusKey("success_meta"),
                textValues: {
                    engine: translation.engine ?? "",
                    duration: formatDurationMs(elapsedMs),
                },
                elapsedMs,
                showQueuePosition: false,
            };
        case "timeout":
        case "error":
            return {
                tone: "error",
                textKey: statusKey(status),
                textValues: { engine: translation.engine ?? "" },
                elapsedMs,
                showQueuePosition: false,
            };
        case "skipped_overload":
            return {
                tone: "warning",
                textKey: statusKey("skipped_overload"),
                textValues: {},
                elapsedMs,
                showQueuePosition: false,
            };
        default:
            return {
                tone: "error",
                textKey: statusKey("unavailable"),
                textValues: {},
                elapsedMs,
                showQueuePosition: false,
            };
    }
};
