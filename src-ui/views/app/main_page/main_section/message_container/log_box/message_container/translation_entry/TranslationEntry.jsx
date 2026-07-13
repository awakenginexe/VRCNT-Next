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

    const presentation = getTranslationPresentation(entry, nowMs);
    const announcement = useMemo(() => {
        const statusChangedAt = Number(entry?.status_changed_at_ms);
        const stableNowMs = Number.isFinite(statusChangedAt) ? statusChangedAt : 0;
        const stablePresentation = getTranslationPresentation(entry, stableNowMs);
        const parts = [];

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
        entry?.message,
        entry?.previous_engine,
        entry?.queue_position,
        entry?.status,
        entry?.status_changed_at_ms,
        t,
    ]);

    return (
        <div className={styles.container}>
            {entry?.message != null && <MessageText item={entry} />}
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
            <span
                className={styles.sr_only}
                role="status"
                aria-live="polite"
                aria-atomic="true"
            >{announcement}</span>
        </div>
    );
};
