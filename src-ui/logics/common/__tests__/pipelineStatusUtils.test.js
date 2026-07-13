import test from "node:test";
import assert from "node:assert/strict";

import {
    PIPELINE_ACTIVE_OUTCOMES,
    createEmptyPipelineStatusState,
    getPipelineStageKey,
    isLatencyActive,
    mergePipelineStatusEvent,
    selectPipelineStatusSummary,
} from "../pipelineStatusUtils.js";

const makeEvent = (overrides = {}) => ({
    schema_version: 1,
    trace_id: "trace-1",
    source: "mic",
    stage: "translation",
    engine: "Google",
    target_slot: "1",
    outcome: "success",
    queue_age_ms: 25,
    duration_ms: 125,
    queue_depth: 0,
    dropped_count: 0,
    observed_at_ms: 1_000,
    error_code: null,
    ...overrides,
});

test("empty state and stage keys follow the schema-v1 contract", () => {
    assert.deepEqual(createEmptyPipelineStatusState(), {
        traces: {},
        latest_by_source: { mic: {}, speaker: {} },
        latest_source: null,
        latest_observed_at_ms: 0,
        announcement_event: null,
    });
    assert.equal(getPipelineStageKey(makeEvent()), "translation:1");
    assert.equal(getPipelineStageKey(makeEvent({ target_slot: null })), "translation:_");
});

test("unknown schemas and source spellings leave state untouched", () => {
    const initial = createEmptyPipelineStatusState();

    assert.strictEqual(
        mergePipelineStatusEvent(initial, makeEvent({ schema_version: 2 })),
        initial,
    );
    assert.strictEqual(
        mergePipelineStatusEvent(initial, makeEvent({ source: "headset" })),
        initial,
    );
});

test("trace stages keep target slots separate and reject only older updates", () => {
    let state = createEmptyPipelineStatusState();
    state = mergePipelineStatusEvent(state, makeEvent({
        target_slot: "1",
        outcome: "sending",
        duration_ms: null,
    }));
    state = mergePipelineStatusEvent(state, makeEvent({
        target_slot: "2",
        outcome: "waiting",
        duration_ms: null,
    }));
    state = mergePipelineStatusEvent(state, makeEvent({
        target_slot: "1",
        outcome: "timeout",
        observed_at_ms: 999,
    }));

    assert.equal(state.traces["trace-1"].stages["translation:1"].outcome, "sending");
    assert.equal(state.traces["trace-1"].stages["translation:2"].outcome, "waiting");

    state = mergePipelineStatusEvent(state, makeEvent({
        target_slot: "1",
        outcome: "fallback",
        duration_ms: null,
        observed_at_ms: 1_000,
    }));

    assert.equal(
        state.traces["trace-1"].stages["translation:1"].outcome,
        "fallback",
        "an equal-millisecond arrival is a distinct, later update",
    );
});

test("equal-millisecond trace retention follows arrival order and stays bounded", () => {
    let state = createEmptyPipelineStatusState();

    for (let index = 0; index < 35; index += 1) {
        state = mergePipelineStatusEvent(state, makeEvent({
            trace_id: `trace-${index}`,
            stage: "output",
            target_slot: null,
            engine: null,
            observed_at_ms: 2_000,
        }), { maxTraces: 32 });
    }

    assert.equal(Object.keys(state.traces).length, 32);
    assert.deepEqual(
        Object.keys(state.traces),
        Array.from({ length: 32 }, (_, index) => `trace-${index + 3}`),
    );
});

test("null-trace metrics stay source-local and never enter the trace dictionary", () => {
    let state = createEmptyPipelineStatusState();
    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: null,
        source: "mic",
        stage: "transcription",
        target_slot: null,
        engine: "Whisper",
        outcome: "running",
        duration_ms: null,
    }));
    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: null,
        source: "speaker",
        stage: "transcription",
        target_slot: null,
        engine: "Whisper",
        outcome: "error",
        error_code: "recovery_failed",
        observed_at_ms: 1_001,
    }));

    assert.deepEqual(state.traces, {});
    assert.equal(state.latest_by_source.mic["transcription:_"].outcome, "running");
    assert.equal(state.latest_by_source.speaker["transcription:_"].outcome, "error");
    assert.equal(state.latest_source, "speaker");
});

test("summary selects the latest source and terminal output duration", () => {
    let state = createEmptyPipelineStatusState();
    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: null,
        source: "mic",
        stage: "queue",
        target_slot: null,
        engine: null,
        outcome: "waiting",
        queue_age_ms: null,
        duration_ms: null,
        queue_depth: 2,
        observed_at_ms: 900,
    }));
    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: "mic-trace",
        source: "mic",
        target_slot: "1",
        outcome: "success",
        duration_ms: 450,
        observed_at_ms: 1_000,
    }));
    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: "mic-trace",
        source: "mic",
        stage: "output",
        target_slot: null,
        engine: null,
        outcome: "running",
        duration_ms: null,
        observed_at_ms: 1_100,
    }));

    assert.equal(selectPipelineStatusSummary(state, 1_500).total_duration_ms, null);

    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: "mic-trace",
        source: "mic",
        stage: "output",
        target_slot: null,
        engine: null,
        outcome: "success",
        duration_ms: 1_234,
        observed_at_ms: 1_200,
    }));

    const summary = selectPipelineStatusSummary(state, 1_500);
    assert.equal(summary.source, "mic");
    assert.equal(summary.translation.engine, "Google");
    assert.equal(summary.queue.queue_depth, 2);
    assert.equal(summary.total_duration_ms, 1_234);
});

test("the active outcome set is exact and capture liveness never becomes latency", () => {
    assert.deepEqual(
        [...PIPELINE_ACTIVE_OUTCOMES],
        ["waiting", "running", "sending", "fallback"],
    );

    for (const outcome of PIPELINE_ACTIVE_OUTCOMES) {
        assert.equal(isLatencyActive(makeEvent({ outcome })), true, outcome);
    }

    assert.equal(isLatencyActive(makeEvent({ stage: "queue", outcome: "waiting" })), true);
    assert.equal(isLatencyActive(makeEvent({ stage: "queue", outcome: "success" })), false);
    assert.equal(isLatencyActive(makeEvent({ stage: "capture", outcome: "running" })), false);
});

test("active queue elapsed time advances locally and terminal success freezes it", () => {
    let state = mergePipelineStatusEvent(
        createEmptyPipelineStatusState(),
        makeEvent({
            trace_id: null,
            stage: "queue",
            target_slot: null,
            engine: null,
            outcome: "waiting",
            queue_age_ms: null,
            duration_ms: null,
            observed_at_ms: 1_000,
        }),
    );

    assert.equal(selectPipelineStatusSummary(state, 1_250).queue.elapsed_ms, 250);

    state = mergePipelineStatusEvent(state, makeEvent({
        trace_id: null,
        stage: "queue",
        target_slot: null,
        engine: null,
        outcome: "success",
        queue_age_ms: 375,
        duration_ms: null,
        observed_at_ms: 1_400,
    }));

    assert.equal(selectPipelineStatusSummary(state, 9_000).queue.elapsed_ms, 375);
});

test("health changes at 2,000ms for active and successful latency", () => {
    const activeState = mergePipelineStatusEvent(
        createEmptyPipelineStatusState(),
        makeEvent({
            trace_id: null,
            stage: "transcription",
            target_slot: null,
            outcome: "running",
            queue_age_ms: null,
            duration_ms: null,
            observed_at_ms: 1_000,
        }),
    );

    assert.equal(selectPipelineStatusSummary(activeState, 2_999).health, "healthy");
    assert.equal(selectPipelineStatusSummary(activeState, 3_000).health, "slow");

    const fastState = mergePipelineStatusEvent(
        createEmptyPipelineStatusState(),
        makeEvent({ outcome: "success", duration_ms: 1_999 }),
    );
    const slowState = mergePipelineStatusEvent(
        createEmptyPipelineStatusState(),
        makeEvent({ outcome: "success", duration_ms: 2_000 }),
    );

    assert.equal(selectPipelineStatusSummary(fastState, 10_000).health, "healthy");
    assert.equal(selectPipelineStatusSummary(slowState, 10_000).health, "slow");
});

test("capture running is excluded from slow health regardless of age", () => {
    const state = mergePipelineStatusEvent(
        createEmptyPipelineStatusState(),
        makeEvent({
            trace_id: null,
            stage: "capture",
            target_slot: null,
            engine: null,
            outcome: "running",
            queue_age_ms: null,
            duration_ms: null,
            observed_at_ms: 1,
        }),
    );

    assert.equal(selectPipelineStatusSummary(state, 1_000_000).health, "healthy");
});

test("terminal failures are errors and only exceptional or recovery events announce", () => {
    const routineOutcomes = ["waiting", "running", "sending", "fallback", "success"];
    for (const outcome of routineOutcomes) {
        const state = mergePipelineStatusEvent(
            createEmptyPipelineStatusState(),
            makeEvent({ outcome, duration_ms: outcome === "success" ? 100 : null }),
        );
        assert.equal(state.announcement_event, null, outcome);
    }

    for (const outcome of ["timeout", "error", "skipped_overload", "recovered"]) {
        const state = mergePipelineStatusEvent(
            createEmptyPipelineStatusState(),
            makeEvent({ outcome, error_code: `${outcome}_code` }),
        );
        assert.equal(state.announcement_event.outcome, outcome);
        assert.equal(
            selectPipelineStatusSummary(state, 1_000).health,
            outcome === "recovered" ? "healthy" : "error",
        );
    }
});
