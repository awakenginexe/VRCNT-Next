import { useEffect, useMemo, useState } from "react";
import { useI18n } from "@useI18n";
import clsx from "clsx";

import CheckMarkSvg from "@images/check_mark.svg?react";
import WarningSvg from "@images/warning.svg?react";
import ErrorSvg from "@images/error.svg?react";
import { usePipelineStatus } from "@logics_common";
import {
    isLatencyActive,
    selectPipelineStatusSummary,
} from "@logics_common/pipelineStatusUtils.js";

import styles from "./PipelineStatus.module.scss";

const ANNOUNCEMENT_OUTCOMES = new Set([
    "timeout",
    "error",
    "skipped_overload",
    "recovered",
]);

const HEALTH_LABEL_KEYS = {
    healthy: "main_page.pipeline_status.healthy",
    slow: "main_page.pipeline_status.slow",
    error: "main_page.pipeline_status.error",
};

const formatDuration = (durationMs, unavailableLabel) => {
    if (!Number.isFinite(durationMs)) return unavailableLabel;
    if (durationMs < 1_000) return `${Math.max(0, Math.round(durationMs))} ms`;
    const seconds = durationMs / 1_000;
    return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
};

const getStageLabel = (stage, t) => {
    if (stage === "translation") return t("main_page.pipeline_status.cloud");
    if (stage === "queue") return t("main_page.pipeline_status.queue");
    if (stage === "output") return t("main_page.pipeline_status.total");
    return t("main_page.pipeline_status.transcription");
};

const getAnnouncement = (event, t) => {
    if (!event || !ANNOUNCEMENT_OUTCOMES.has(event.outcome)) return "";

    if (event.outcome === "timeout") {
        return t("main_page.pipeline_status.timeout_announcement", {
            engine: event.engine ?? t("main_page.pipeline_status.cloud"),
        });
    }
    if (event.outcome === "skipped_overload") {
        return t("main_page.pipeline_status.overload_announcement");
    }

    const key = event.outcome === "recovered"
        ? "recovered_announcement"
        : "error_announcement";
    return t(`main_page.pipeline_status.${key}`, {
        stage: getStageLabel(event.stage, t),
    });
};

const HealthIcon = ({ health, className }) => {
    if (health === "error") return <ErrorSvg className={className} aria-hidden="true" />;
    if (health === "slow") return <WarningSvg className={className} aria-hidden="true" />;
    return <CheckMarkSvg className={className} aria-hidden="true" />;
};

const StatusItem = ({ label, value, detail, className }) => (
    <div className={clsx(styles.item, className)}>
        <span className={styles.label}>{label}</span>
        <span className={styles.value}>{value}</span>
        {detail && <span className={styles.detail}>{detail}</span>}
    </div>
);

export const PipelineStatus = () => {
    const { t } = useI18n();
    const { currentPipelineStatus } = usePipelineStatus();
    const pipelineState = currentPipelineStatus.data;
    const [nowMs, setNowMs] = useState(Date.now);
    const [announcement, setAnnouncement] = useState("");
    const summary = useMemo(
        () => selectPipelineStatusSummary(pipelineState, nowMs),
        [pipelineState, nowMs],
    );
    const activeLatency = [summary.transcription, summary.translation, summary.queue]
        .some((event) => isLatencyActive(event));

    useEffect(() => {
        if (!activeLatency) return undefined;

        setNowMs(Date.now());
        const timer = setInterval(() => setNowMs(Date.now()), 250);
        return () => clearInterval(timer);
    }, [activeLatency, pipelineState.latest_observed_at_ms]);

    const announcementEvent = pipelineState.announcement_event;
    useEffect(() => {
        if (!announcementEvent || !ANNOUNCEMENT_OUTCOMES.has(announcementEvent.outcome)) {
            return undefined;
        }

        const nextAnnouncement = getAnnouncement(announcementEvent, t);
        setAnnouncement("");
        const timer = setTimeout(() => setAnnouncement(nextAnnouncement), 450);
        return () => clearTimeout(timer);
    }, [
        announcementEvent,
        announcementEvent?.engine,
        announcementEvent?.observed_at_ms,
        announcementEvent?.outcome,
        announcementEvent?.stage,
        t,
    ]);

    const unavailable = t("main_page.pipeline_status.unavailable");
    const waiting = t("main_page.pipeline_status.waiting");
    const sourceLabel = summary.source === "mic"
        ? t("main_page.pipeline_status.speaking")
        : summary.source === "speaker"
            ? t("main_page.pipeline_status.listening")
            : unavailable;
    const transcriptionDuration = formatDuration(
        summary.transcription?.elapsed_ms,
        unavailable,
    );
    const translationDuration = formatDuration(
        summary.translation?.elapsed_ms,
        unavailable,
    );
    const queueDuration = formatDuration(summary.queue?.elapsed_ms, unavailable);
    const totalDuration = formatDuration(summary.total_duration_ms, unavailable);
    const healthLabel = t(HEALTH_LABEL_KEYS[summary.health]);
    const transcriptionDetail = summary.transcription
        ? `${summary.transcription.engine ?? unavailable} · ${isLatencyActive(summary.transcription) ? `${waiting} · ` : ""}${transcriptionDuration}`
        : unavailable;
    const translationDetail = summary.translation
        ? `${summary.translation.engine ?? unavailable} · ${isLatencyActive(summary.translation) ? `${waiting} · ` : ""}${translationDuration}`
        : unavailable;
    const queueValue = summary.queue ? String(summary.queue.queue_depth) : unavailable;

    return (
        <div className={styles.container}>
            <StatusItem
                label={t("main_page.pipeline_status.source")}
                value={sourceLabel}
                className={styles.source_item}
            />
            <StatusItem
                label={t("main_page.pipeline_status.transcription")}
                value={transcriptionDetail}
            />
            <StatusItem
                label={t("main_page.pipeline_status.cloud")}
                value={translationDetail}
            />
            <div className={styles.item}>
                <span className={styles.label}>{t("main_page.pipeline_status.queue")}</span>
                <span className={styles.value}>{queueValue}</span>
                <span className={clsx(styles.health, styles[`health_${summary.health}`])}>
                    <HealthIcon health={summary.health} className={styles.health_icon} />
                    {healthLabel}
                    {summary.queue && <span className={styles.queue_duration}>· {queueDuration}</span>}
                </span>
            </div>
            <StatusItem
                label={t("main_page.pipeline_status.total")}
                value={totalDuration}
                className={styles.total_item}
            />
            <span
                className={styles.sr_only}
                role="status"
                aria-live="polite"
                aria-atomic="true"
            >{announcement}</span>
        </div>
    );
};
