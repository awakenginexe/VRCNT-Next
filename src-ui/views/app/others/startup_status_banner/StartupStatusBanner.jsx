import { useEffect, useState } from "react";
import styles from "./StartupStatusBanner.module.scss";
import { useInitProgress, useInitStatus, useIsBackendReady } from "@logics_common";
import { useI18n } from "@useI18n";

export const StartupStatusBanner = () => {
    const { t } = useI18n();
    const { currentInitStatus } = useInitStatus();
    const { currentInitProgress } = useInitProgress();
    const { currentIsBackendReady } = useIsBackendReady();
    const [isDismissed, setIsDismissed] = useState(false);
    const isError = currentInitStatus.data.phase === "error";

    useEffect(() => {
        setIsDismissed(false);
    }, [
        currentInitStatus.data.message,
        currentInitStatus.data.detail,
        currentInitStatus.data.phase,
        currentInitStatus.data.visible,
        currentInitStatus.data.message_key,
        currentInitStatus.data.detail_key,
    ]);

    useEffect(() => {
        if (isError) return undefined;
        if (currentInitStatus.data.visible !== true) return undefined;
        if (currentIsBackendReady.data !== true) return undefined;

        const timeout = setTimeout(() => {
            setIsDismissed(true);
        }, 2200);

        return () => clearTimeout(timeout);
    }, [
        isError,
        currentInitStatus.data.visible,
        currentInitStatus.data.message,
        currentInitStatus.data.detail,
        currentInitStatus.data.message_key,
        currentInitStatus.data.detail_key,
        currentIsBackendReady.data,
    ]);

    const shouldShowError = isError;
    const shouldShowOptionalStatus = (
        currentInitStatus.data.visible === true
        && currentIsBackendReady.data === true
        && !isError
        && !isDismissed
    );
    if (!shouldShowError && !shouldShowOptionalStatus) return null;

    const message = isError
        ? t(currentInitStatus.data.message_key
            || "blocking_operation.startup_failed")
        : currentInitStatus.data.message_key
            ? t(currentInitStatus.data.message_key)
            : currentInitStatus.data.message;
    const detail = isError
        ? t(currentInitStatus.data.detail_key
            || "blocking_operation.startup_failed_detail")
        : currentInitStatus.data.detail_key
            ? t(currentInitStatus.data.detail_key)
            : currentInitStatus.data.detail;

    return (
        <div
            className={styles.container}
            role={isError ? "alert" : "status"}
            aria-live={isError ? "assertive" : "polite"}
            aria-atomic="true"
        >
            <div className={styles.status_card}>
                {!isError ? (
                    <p className={styles.phase}>
                        {t("blocking_operation.backend_startup_progress", {
                            current: Math.max(currentInitProgress.data, 1),
                            total: 4,
                        })}
                    </p>
                ) : null}
                <p className={styles.message}>{message}</p>
                {detail
                    ? <p className={styles.detail}>{detail}</p>
                    : null
                }
            </div>
        </div>
    );
};
