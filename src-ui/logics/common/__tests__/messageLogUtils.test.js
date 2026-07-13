import test from "node:test";
import assert from "node:assert/strict";

import {
    TRANSLATION_ACTIVE_STATUSES,
    TRANSLATION_TERMINAL_STATUSES,
    createMessageLogEntry,
    formatDurationMs,
    getTranslationPresentation,
    isTranslationTransitionAllowed,
    mergeTranslationUpdateByTrace,
} from "../messageLogUtils.js";

const progressivePayload = () => ({
    trace_id: "speaker-test",
    original: { message: "recognized", transliteration: [] },
    translations: [
        {
            target_slot: "1",
            message: null,
            transliteration: [],
            status: "queued",
            engine: "Google",
            duration_ms: null,
        },
    ],
});

const createProgressiveLog = () => createMessageLogEntry(
    progressivePayload(),
    "received",
    { id: "local-id", createdAt: "10:30", nowMs: 1_000 },
);

test("normalizes legacy translations with stable string target slots", () => {
    const entry = createMessageLogEntry(
        {
            original: { message: "hello", transliteration: [] },
            translations: [
                { message: "hola", transliteration: [] },
                { message: "bonjour", transliteration: [{ text: "bon" }] },
            ],
        },
        "sent",
        { id: "legacy-id", createdAt: "09:15", nowMs: 500 },
    );

    assert.equal(entry.id, "legacy-id");
    assert.equal(entry.created_at, "09:15");
    assert.equal(entry.category, "sent");
    assert.deepEqual(
        entry.messages.translations.map((translation) => translation.target_slot),
        ["1", "2"],
    );
    assert.equal(new Set(entry.messages.translations.map((item) => item.target_slot)).size, 2);
    assert.deepEqual(entry.messages.translations[0], {
        target_slot: "1",
        message: "hola",
        transliteration: [],
        status: "success",
        engine: null,
        previous_engine: null,
        duration_ms: null,
        queue_position: 0,
        error_code: null,
        status_changed_at_ms: 500,
    });
});

test("preserves progressive trace and original while normalizing a queued slot", () => {
    const entry = createProgressiveLog();

    assert.equal(entry.trace_id, "speaker-test");
    assert.deepEqual(entry.messages.original, {
        message: "recognized",
        transliteration: [],
    });
    assert.deepEqual(entry.messages.translations, [
        {
            target_slot: "1",
            message: null,
            transliteration: [],
            status: "queued",
            engine: "Google",
            previous_engine: null,
            duration_ms: null,
            queue_position: 0,
            error_code: null,
            status_changed_at_ms: 1_000,
        },
    ]);
});

test("patches a matching translation immutably without appending a log", () => {
    const first = createProgressiveLog();
    const unrelated = { id: "other", trace_id: "other-trace" };
    const logs = [first, unrelated];

    const patched = mergeTranslationUpdateByTrace(logs, {
        trace_id: "speaker-test",
        target_slot: 1,
        status: "success",
        engine: "Google",
        message: "translated",
        transliteration: [{ text: "translated", pronunciation: "translated" }],
        duration_ms: 620,
        queue_position: 0,
        error_code: null,
    }, 2_000);

    assert.notEqual(patched, logs);
    assert.equal(patched.length, 2);
    assert.notEqual(patched[0], first);
    assert.notEqual(patched[0].messages, first.messages);
    assert.notEqual(
        patched[0].messages.translations,
        first.messages.translations,
    );
    assert.equal(patched[1], unrelated);
    assert.deepEqual(
        {
            id: patched[0].id,
            created_at: patched[0].created_at,
            category: patched[0].category,
            original: patched[0].messages.original,
        },
        {
            id: "local-id",
            created_at: "10:30",
            category: "received",
            original: first.messages.original,
        },
    );
    assert.deepEqual(patched[0].messages.translations[0], {
        target_slot: "1",
        message: "translated",
        transliteration: [{ text: "translated", pronunciation: "translated" }],
        status: "success",
        engine: "Google",
        previous_engine: null,
        duration_ms: 620,
        queue_position: 0,
        error_code: null,
        status_changed_at_ms: 2_000,
    });
});

test("returns the original array for unknown traces and target slots", () => {
    const logs = [createProgressiveLog()];

    assert.equal(
        mergeTranslationUpdateByTrace(logs, {
            trace_id: "missing",
            target_slot: "1",
            status: "success",
        }, 2_000),
        logs,
    );
    assert.equal(
        mergeTranslationUpdateByTrace(logs, {
            trace_id: "speaker-test",
            target_slot: "missing",
            status: "success",
        }, 2_000),
        logs,
    );
});

test("updates target slots independently", () => {
    const log = createMessageLogEntry(
        {
            ...progressivePayload(),
            translations: [
                { target_slot: "1", status: "queued", engine: "Google" },
                { target_slot: "2", status: "queued", engine: "Google" },
            ],
        },
        "received",
        { id: "two-slots", createdAt: "10:31", nowMs: 1_000 },
    );

    const patched = mergeTranslationUpdateByTrace([log], {
        trace_id: "speaker-test",
        target_slot: "2",
        status: "sending",
        queue_position: 0,
    }, 1_500);

    assert.equal(patched[0].messages.translations[0], log.messages.translations[0]);
    assert.equal(patched[0].messages.translations[0].status, "queued");
    assert.notEqual(patched[0].messages.translations[1], log.messages.translations[1]);
    assert.equal(patched[0].messages.translations[1].status, "sending");
});

test("applies explicit null fields using property presence", () => {
    let logs = [createProgressiveLog()];
    logs = mergeTranslationUpdateByTrace(logs, {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "success",
        message: "translated",
        transliteration: [{ text: "translated" }],
        duration_ms: 400,
        queue_position: 2,
        error_code: "temporary",
    }, 1_500);

    const cleared = mergeTranslationUpdateByTrace(logs, {
        trace_id: "speaker-test",
        target_slot: "1",
        message: null,
        transliteration: null,
        engine: null,
        duration_ms: null,
        queue_position: null,
        error_code: null,
    }, 2_000);
    const translation = cleared[0].messages.translations[0];

    assert.equal(translation.message, null);
    assert.equal(translation.transliteration, null);
    assert.equal(translation.engine, null);
    assert.equal(translation.duration_ms, null);
    assert.equal(translation.queue_position, null);
    assert.equal(translation.error_code, null);
    assert.equal(translation.previous_engine, "Google");
    assert.equal(translation.status_changed_at_ms, 2_000);
});

test("preserves the previous provider across fallback progress", () => {
    const logs = [createProgressiveLog()];
    const fallback = mergeTranslationUpdateByTrace(logs, {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "fallback",
        engine: "Bing",
    }, 1_400);
    const sending = mergeTranslationUpdateByTrace(fallback, {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "sending",
        engine: "Bing",
    }, 1_600);

    assert.equal(
        fallback[0].messages.translations[0].previous_engine,
        "Google",
    );
    assert.equal(
        sending[0].messages.translations[0].previous_engine,
        "Google",
    );
    assert.equal(sending[0].messages.translations[0].engine, "Bing");
    assert.equal(sending[0].messages.translations[0].status_changed_at_ms, 1_600);
});

test("same-status data patches keep the original status-change timestamp", () => {
    const logs = [createProgressiveLog()];
    const patched = mergeTranslationUpdateByTrace(logs, {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "queued",
        duration_ms: 250,
        queue_position: 3,
    }, 9_000);
    const translation = patched[0].messages.translations[0];

    assert.equal(translation.duration_ms, 250);
    assert.equal(translation.queue_position, 3);
    assert.equal(translation.status_changed_at_ms, 1_000);
});

test("terminal translations cannot regress while active states can jump to terminal", () => {
    assert.deepEqual([...TRANSLATION_ACTIVE_STATUSES], ["queued", "sending", "fallback"]);
    assert.deepEqual(
        [...TRANSLATION_TERMINAL_STATUSES],
        ["success", "timeout", "error", "skipped_overload"],
    );
    assert.equal(isTranslationTransitionAllowed("queued", "success"), true);
    assert.equal(isTranslationTransitionAllowed("sending", "timeout"), true);
    assert.equal(isTranslationTransitionAllowed("success", "sending"), false);
    assert.equal(isTranslationTransitionAllowed("error", "fallback"), false);

    const terminal = mergeTranslationUpdateByTrace([createProgressiveLog()], {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "success",
        message: "done",
    }, 2_000);
    const regressed = mergeTranslationUpdateByTrace(terminal, {
        trace_id: "speaker-test",
        target_slot: "1",
        status: "sending",
        message: null,
    }, 3_000);

    assert.equal(regressed, terminal);
    assert.equal(regressed[0].messages.translations[0].status, "success");
    assert.equal(regressed[0].messages.translations[0].message, "done");
});

test("formats milliseconds below one second and one-decimal seconds at or above it", () => {
    assert.equal(formatDurationMs(0), "0ms");
    assert.equal(formatDurationMs(620), "620ms");
    assert.equal(formatDurationMs(999), "999ms");
    assert.equal(formatDurationMs(1_000), "1.0s");
    assert.equal(formatDurationMs(1_449), "1.4s");
    assert.equal(formatDurationMs(1_450), "1.4s");
});

test("maps active, terminal, and provider-error states to localized presentation", () => {
    const base = {
        engine: "Google",
        previous_engine: null,
        duration_ms: null,
        queue_position: 2,
        error_code: null,
        status_changed_at_ms: 1_000,
    };
    const key = (name) => `main_page.message_log.translation_status.${name}`;

    assert.deepEqual(getTranslationPresentation({ ...base, status: "queued" }, 2_400), {
        tone: "pending",
        textKey: key("queued"),
        textValues: { engine: "Google", elapsed: "1.4s" },
        elapsedMs: 1_400,
        showQueuePosition: true,
    });
    assert.deepEqual(getTranslationPresentation({ ...base, status: "sending" }, 2_400), {
        tone: "pending",
        textKey: key("sending"),
        textValues: { engine: "Google", elapsed: "1.4s" },
        elapsedMs: 1_400,
        showQueuePosition: true,
    });
    assert.deepEqual(getTranslationPresentation({
        ...base,
        status: "fallback",
        engine: "Bing",
        previous_engine: "Google",
        queue_position: 0,
    }, 2_400), {
        tone: "warning",
        textKey: key("fallback"),
        textValues: { engine: "Bing", previousEngine: "Google" },
        elapsedMs: 1_400,
        showQueuePosition: false,
    });
    assert.deepEqual(getTranslationPresentation({
        ...base,
        status: "success",
        engine: "Bing",
        duration_ms: 620,
        queue_position: 0,
    }, 5_000), {
        tone: "success",
        textKey: key("success_meta"),
        textValues: { engine: "Bing", duration: "620ms" },
        elapsedMs: 620,
        showQueuePosition: false,
    });
    assert.equal(
        getTranslationPresentation({ ...base, status: "timeout" }, 5_000).textKey,
        key("timeout"),
    );
    assert.equal(
        getTranslationPresentation({ ...base, status: "error" }, 5_000).textKey,
        key("error"),
    );
    assert.equal(
        getTranslationPresentation({ ...base, status: "skipped_overload" }, 5_000).textKey,
        key("skipped_overload"),
    );
    assert.equal(
        getTranslationPresentation({
            ...base,
            status: "error",
            error_code: "no_provider_configured",
        }, 5_000).textKey,
        key("no_provider"),
    );
    assert.equal(
        getTranslationPresentation({ ...base, status: null }, 5_000).textKey,
        key("unavailable"),
    );
});
