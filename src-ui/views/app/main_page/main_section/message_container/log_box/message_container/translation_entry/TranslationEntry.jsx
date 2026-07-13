import { useEffect, useState } from "react";
import { useI18n } from "@useI18n";
import clsx from "clsx";
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

    return (
        <div className={styles.container} aria-busy={isActive}>
            {entry?.message != null && <MessageText item={entry} />}
            <p
                className={clsx(styles.status, styles[presentation.tone])}
                aria-live={isActive ? "off" : "polite"}
                aria-atomic="true"
            >
                <span>{t(presentation.textKey, presentation.textValues)}</span>
                {presentation.showQueuePosition && (
                    <>
                        <span aria-hidden="true"> · </span>
                        <span>
                            {t(
                                "main_page.message_log.translation_status.queue_position",
                                { position: entry?.queue_position },
                            )}
                        </span>
                    </>
                )}
            </p>
        </div>
    );
};
