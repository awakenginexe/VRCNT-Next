import { useEffect, useState } from "react";
import styles from "./StartupStatusBanner.module.scss";
import { useInitProgress, useInitStatus, useIsBackendReady } from "@logics_common";

export const StartupStatusBanner = () => {
    const { currentInitStatus } = useInitStatus();
    const { currentInitProgress } = useInitProgress();
    const { currentIsBackendReady } = useIsBackendReady();
    const [isDismissed, setIsDismissed] = useState(false);

    useEffect(() => {
        setIsDismissed(false);
    }, [
        currentInitStatus.data.message,
        currentInitStatus.data.detail,
        currentInitStatus.data.phase,
        currentInitStatus.data.visible,
    ]);

    useEffect(() => {
        if (currentInitStatus.data.visible !== true) return;
        if (currentIsBackendReady.data !== true) return;
        if (currentInitProgress.data < 3) return;

        const timeout = setTimeout(() => {
            setIsDismissed(true);
        }, 2200);

        return () => clearTimeout(timeout);
    }, [
        currentInitStatus.data.visible,
        currentInitProgress.data,
        currentIsBackendReady.data,
    ]);

    if (currentInitStatus.data.visible !== true || isDismissed === true) return null;

    return (
        <div className={styles.container}>
            <div className={styles.status_card}>
                <p className={styles.phase}>Backend startup {Math.max(currentInitProgress.data, 1)} / 4</p>
                <p className={styles.message}>{currentInitStatus.data.message}</p>
                {currentInitStatus.data.detail
                    ? <p className={styles.detail}>{currentInitStatus.data.detail}</p>
                    : null
                }
            </div>
        </div>
    );
};
