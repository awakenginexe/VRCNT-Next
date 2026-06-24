import { useState } from "react";
import clsx from "clsx";
import { useI18n } from "@useI18n";
import { Tooltip } from "@common_components";
import { openDesktopOverlayWindow } from "@logics_common";
import { useIsMainPageCompactMode } from "@logics_main";
import ForegroundSvg from "@images/foreground.svg?react";
import styles from "./DesktopOverlayButton.module.scss";

export const DesktopOverlayButton = () => {
    const { t } = useI18n();
    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    const [isPending, setIsPending] = useState(false);
    const isCompact = currentIsMainPageCompactMode.data === true;

    const openOverlay = async () => {
        if (isPending) return;
        setIsPending(true);
        try {
            await openDesktopOverlayWindow();
        } catch (error) {
            console.error("Unable to open desktop overlay.", error);
        } finally {
            setIsPending(false);
        }
    };

    return (
        <Tooltip
            title={t("main_page.desktop_overlay.tooltip_title")}
            detail={t("main_page.desktop_overlay.tooltip_detail")}
            placement="right"
            className={styles.tooltip}
            contentClassName={styles.tooltip_content}
            usePortal
        >
            <button
                className={clsx(styles.button, {
                    [styles.is_compact_mode]: isCompact,
                    [styles.is_pending]: isPending,
                })}
                onClick={openOverlay}
                aria-label={t("main_page.desktop_overlay.open_label")}
            >
                <ForegroundSvg className={styles.icon} />
                <span className={styles.label}>{t("main_page.desktop_overlay.open_label")}</span>
                <span className={styles.status_dot}></span>
            </button>
        </Tooltip>
    );
};
