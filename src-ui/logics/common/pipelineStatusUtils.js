const PIPELINE_SCHEMA_VERSION = 1;
const PIPELINE_SOURCES = new Set(["mic", "speaker"]);
const PIPELINE_STAGES = new Set([
    "capture",
    "queue",
    "transcription",
    "translation",
    "output",
]);
const PIPELINE_OUTCOMES = new Set([
    "waiting",
    "running",
    "sending",
    "success",
    "slow",
    "fallback",
    "timeout",
    "error",
    "skipped_overload",
    "recovered",
]);
const PIPELINE_ERROR_OUTCOMES = new Set(["timeout", "error", "skipped_overload"]);
const PIPELINE_ANNOUNCEMENT_OUTCOMES = new Set([
    "timeout",
    "error",
    "skipped_overload",
    "recovered",
]);

export const PIPELINE_ACTIVE_OUTCOMES = new Set([
    "waiting",
    "running",
    "sending",
    "fallback",
]);

export const createEmptyPipelineStatusState = () => ({
    traces: {},
    latest_by_source: { mic: {}, speaker: {} },
    latest_source: null,
    latest_observed_at_ms: 0,
    announcement_event: null,
});

export const getPipelineStageKey = (event) => (
    `${event.stage}:${event.target_slot ?? "_"}`
);

const isNullableString = (value) => value === null || typeof value === "string";
const isNullableNonNegativeNumber = (value) => (
    value === null || (Number.isFinite(value) && value >= 0)
);
const isNonNegativeInteger = (value) => Number.isInteger(value) && value >= 0;

const isPipelineStatusEvent = (event) => (
    event !== null
    && typeof event === "object"
    && event.schema_version === PIPELINE_SCHEMA_VERSION
    && (event.trace_id === null || (typeof event.trace_id === "string" && event.trace_id.length > 0))
    && PIPELINE_SOURCES.has(event.source)
    && typeof event.stage === "string"
    && PIPELINE_STAGES.has(event.stage)
    && isNullableString(event.engine)
    && isNullableString(event.target_slot)
    && typeof event.outcome === "string"
    && PIPELINE_OUTCOMES.has(event.outcome)
    && isNullableNonNegativeNumber(event.queue_age_ms)
    && isNullableNonNegativeNumber(event.duration_ms)
    && isNonNegativeInteger(event.queue_depth)
    && isNonNegativeInteger(event.dropped_count)
    && Number.isFinite(event.observed_at_ms)
    && event.observed_at_ms >= 0
    && isNullableString(event.error_code)
);

const cloneEvent = (event) => ({ ...event });

const getNextTraceArrivalSequence = (traces) => (
    Object.values(traces).reduce(
        (latest, trace) => Math.max(latest, trace._arrival_sequence ?? 0),
        0,
    ) + 1
);

const compareTraceEntries = ([, left], [, right]) => (
    left.latest_observed_at_ms - right.latest_observed_at_ms
    || (left._arrival_sequence ?? 0) - (right._arrival_sequence ?? 0)
);

const trimTraces = (traces, maxTraces) => {
    const boundedMaximum = Number.isInteger(maxTraces) && maxTraces > 0
        ? maxTraces
        : 32;
    const entries = Object.entries(traces).sort(compareTraceEntries);

    return Object.fromEntries(entries.slice(-boundedMaximum));
};

const mergeTraceEvent = (traces, event, maxTraces) => {
    const stageKey = getPipelineStageKey(event);
    const currentTrace = traces[event.trace_id];

    if (currentTrace && currentTrace.source !== event.source) return traces;

    const currentStage = currentTrace?.stages?.[stageKey];
    if (currentStage && event.observed_at_ms < currentStage.observed_at_ms) {
        return traces;
    }

    const nextStages = { ...(currentTrace?.stages ?? {}) };
    if (currentStage && event.observed_at_ms >= currentStage.observed_at_ms) {
        delete nextStages[stageKey];
    }
    nextStages[stageKey] = cloneEvent(event);

    const nextTrace = {
        source: event.source,
        latest_observed_at_ms: Math.max(
            currentTrace?.latest_observed_at_ms ?? 0,
            event.observed_at_ms,
        ),
        _arrival_sequence: (
            !currentTrace || event.observed_at_ms >= currentTrace.latest_observed_at_ms
                ? getNextTraceArrivalSequence(traces)
                : (currentTrace._arrival_sequence ?? 0)
        ),
        stages: nextStages,
    };
    const nextTraces = { ...traces };

    if (
        currentTrace
        && event.observed_at_ms >= currentTrace.latest_observed_at_ms
    ) {
        delete nextTraces[event.trace_id];
    }
    nextTraces[event.trace_id] = nextTrace;

    return trimTraces(nextTraces, maxTraces);
};

const mergeLatestSourceEvent = (latestBySource, event) => {
    const stageKey = getPipelineStageKey(event);
    const currentEvent = latestBySource[event.source]?.[stageKey];

    if (currentEvent && event.observed_at_ms < currentEvent.observed_at_ms) {
        return latestBySource;
    }

    return {
        ...latestBySource,
        [event.source]: {
            ...latestBySource[event.source],
            [stageKey]: cloneEvent(event),
        },
    };
};

export const mergePipelineStatusEvent = (
    state,
    event,
    { maxTraces = 32 } = {},
) => {
    if (!isPipelineStatusEvent(event)) return state;

    const currentState = state ?? createEmptyPipelineStatusState();
    const stageKey = getPipelineStageKey(event);
    const currentTraceStage = event.trace_id === null
        ? null
        : currentState.traces?.[event.trace_id]?.stages?.[stageKey];
    const currentSourceStage = currentState.latest_by_source?.[event.source]?.[stageKey];
    const eventIsOlder = event.trace_id === null
        ? currentSourceStage && event.observed_at_ms < currentSourceStage.observed_at_ms
        : currentTraceStage && event.observed_at_ms < currentTraceStage.observed_at_ms;

    if (eventIsOlder) return currentState;
    if (
        event.trace_id !== null
        && currentState.traces?.[event.trace_id]
        && currentState.traces[event.trace_id].source !== event.source
    ) {
        return currentState;
    }

    const nextTraces = event.trace_id === null
        ? currentState.traces
        : mergeTraceEvent(currentState.traces, event, maxTraces);
    const nextLatestBySource = mergeLatestSourceEvent(
        currentState.latest_by_source,
        event,
    );
    const isLatestArrival = event.observed_at_ms >= currentState.latest_observed_at_ms;
    const currentAnnouncement = currentState.announcement_event;
    const shouldAnnounce = (
        PIPELINE_ANNOUNCEMENT_OUTCOMES.has(event.outcome)
        && (
            currentAnnouncement === null
            || event.observed_at_ms >= currentAnnouncement.observed_at_ms
        )
    );

    return {
        ...currentState,
        traces: nextTraces,
        latest_by_source: nextLatestBySource,
        latest_source: isLatestArrival ? event.source : currentState.latest_source,
        latest_observed_at_ms: Math.max(
            currentState.latest_observed_at_ms,
            event.observed_at_ms,
        ),
        announcement_event: shouldAnnounce
            ? cloneEvent(event)
            : currentAnnouncement,
    };
};

export const isLatencyActive = (event) => (
    event?.stage !== "capture"
    && PIPELINE_ACTIVE_OUTCOMES.has(event?.outcome)
);

const getEventElapsedMs = (event, nowMs) => {
    if (!event) return null;

    if (isLatencyActive(event)) {
        return Math.max(
            0,
            Math.round(nowMs - event.observed_at_ms),
        );
    }

    if (event.duration_ms !== null) return Math.round(event.duration_ms);
    if (event.queue_age_ms !== null) return Math.round(event.queue_age_ms);
    return null;
};

const withElapsed = (event, nowMs) => (
    event ? { ...event, elapsed_ms: getEventElapsedMs(event, nowMs) } : null
);

const getLatestEvent = (events, predicate = () => true) => {
    let latest = null;
    for (const event of events) {
        if (!predicate(event)) continue;
        if (latest === null || event.observed_at_ms >= latest.observed_at_ms) {
            latest = event;
        }
    }
    return latest;
};

const getLatestTraceForSource = (traces, source) => {
    let latestTrace = null;
    for (const trace of Object.values(traces ?? {})) {
        if (trace.source !== source) continue;
        if (
            latestTrace === null
            || trace.latest_observed_at_ms >= latestTrace.latest_observed_at_ms
        ) {
            if (
                latestTrace === null
                || trace.latest_observed_at_ms > latestTrace.latest_observed_at_ms
                || (trace._arrival_sequence ?? 0) >= (latestTrace._arrival_sequence ?? 0)
            ) {
                latestTrace = trace;
            }
        }
    }
    return latestTrace;
};

const isTerminalOutput = (event) => (
    event?.stage === "output" && !PIPELINE_ACTIVE_OUTCOMES.has(event.outcome)
);

const getPipelineHealth = (events, nowMs) => {
    if (events.some((event) => PIPELINE_ERROR_OUTCOMES.has(event.outcome))) {
        return "error";
    }

    const isSlow = events.some((event) => {
        if (event.outcome === "slow") return true;
        if (!isLatencyActive(event) && event.outcome !== "success") return false;
        const elapsedMs = getEventElapsedMs(event, nowMs);
        return elapsedMs !== null && elapsedMs >= 2_000;
    });
    return isSlow ? "slow" : "healthy";
};

export const selectPipelineStatusSummary = (state, nowMs = Date.now()) => {
    const currentState = state ?? createEmptyPipelineStatusState();
    const source = PIPELINE_SOURCES.has(currentState.latest_source)
        ? currentState.latest_source
        : null;

    if (source === null) {
        return {
            source: null,
            transcription: null,
            translation: null,
            queue: null,
            total_duration_ms: null,
            health: "healthy",
        };
    }

    const safeNowMs = Number.isFinite(nowMs) ? nowMs : Date.now();
    const sourceEvents = Object.values(currentState.latest_by_source?.[source] ?? {});
    const sourceLifecycleEvents = sourceEvents.filter((event) => event.trace_id === null);
    const latestTrace = getLatestTraceForSource(currentState.traces, source);
    const traceEvents = Object.values(latestTrace?.stages ?? {});
    const transcription = getLatestEvent(
        sourceLifecycleEvents,
        (event) => event.stage === "transcription",
    );
    const translation = getLatestEvent(
        traceEvents,
        (event) => event.stage === "translation",
    );
    const queue = getLatestEvent(
        sourceLifecycleEvents,
        (event) => event.stage === "queue",
    );
    const output = getLatestEvent(
        traceEvents,
        (event) => event.stage === "output",
    );
    const healthEvents = [...sourceLifecycleEvents, ...traceEvents];

    return {
        source,
        transcription: withElapsed(transcription, safeNowMs),
        translation: withElapsed(translation, safeNowMs),
        queue: withElapsed(queue, safeNowMs),
        total_duration_ms: isTerminalOutput(output) ? output.duration_ms : null,
        health: getPipelineHealth(healthEvents, safeNowMs),
    };
};
