import { useEffect, useRef, useState } from "react";

import { useMainFunction } from "../main/useMainFunction";
import { getBlockingOperationCandidate } from "./blockingOperationState.js";
import { useInitProgress } from "./useInitProgress";
import { useInitStatus } from "./useInitStatus";
import { useIsBackendReady } from "./useIsBackendReady";

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
