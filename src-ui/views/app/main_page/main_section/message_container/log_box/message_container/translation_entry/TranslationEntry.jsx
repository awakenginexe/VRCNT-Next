import { useEffect, useMemo, useState } from "react";
import { useI18n } from "@useI18n";
import {
    getTranslationPresentation,
    TRANSLATION_ACTIVE_STATUSES,
} from "@logics_common/messageLogUtils.js";
import { MessageText } from "../MessageText";
import styles from "./TranslationEntry.module.scss";

export const TranslationEntry = ({ entry }) => {
    const { t } = useI18n();
    const hasStatus = entry?.status != null;
    const isActive = TRANSLATION_ACTIVE_STATUSES.has(entry?.status);
    const [nowMs, setNowMs] = useState(() => Date.now());

    useEffect(() => {
        if (!isActive) return undefined;

        setNowMs(Date.now());
        const intervalId = setInterval(() => {
            setNowMs(Date.now());
        }, 250);

        return () => clearInterval(intervalId);
    }, [entry?.status, entry?.status_changed_at_ms, isActive]);

    const presentation = hasStatus
        ? getTranslationPresentation(entry, nowMs)
        : null;
    const announcement = useMemo(() => {
        if (!hasStatus) return "";
        const statusChangedAt = Number(entry?.status_changed_at_ms);
        const stableNowMs = Number.isFinite(statusChangedAt) ? statusChangedAt : 0;
        const stablePresentation = getTranslationPresentation(entry, stableNowMs);
        const parts = [];

        if (entry?.language) parts.push(`${entry.language}:`);
        if (entry?.message) parts.push(entry.message);
        parts.push(t(stablePresentation.textKey, stablePresentation.textValues));
        if (stablePresentation.showQueuePosition) {
            parts.push(t(
                "main_page.message_log.translation_status.queue_position",
                { position: entry?.queue_position },
            ));
        }
        return parts.join(" · ");
    }, [
        entry?.duration_ms,
        entry?.engine,
        entry?.error_code,
        entry?.language,
        entry?.message,
        entry?.previous_engine,
        entry?.queue_position,
        entry?.status,
        entry?.status_changed_at_ms,
        hasStatus,
        t,
    ]);

    return (
        <div className={styles.container}>
            <div className={styles.content}>
                {entry?.language && (
                    <span className={styles.language}>{entry.language}:</span>
                )}
                {entry?.message != null && <MessageText item={entry} />}
                {entry?.message != null && hasStatus && (
                    <span className={styles.separator} aria-hidden="true">·</span>
                )}
                {hasStatus && (
                    <p className={styles.status} aria-hidden="true">
                        <span className={styles[presentation.tone]}>
                            <span>{t(presentation.textKey, presentation.textValues)}</span>
                            {presentation.showQueuePosition && (
                                <>
                                    <span> · </span>
                                    <span>
                                        {t(
                                            "main_page.message_log.translation_status.queue_position",
                                            { position: entry?.queue_position },
                                        )}
                                    </span>
                                </>
                            )}
                        </span>
                    </p>
                )}
            </div>
            {hasStatus && (
                <span
                    className={styles.sr_only}
                    role="status"
                    aria-live="polite"
                    aria-atomic="true"
                >{announcement}</span>
            )}
        </div>
    );
};
