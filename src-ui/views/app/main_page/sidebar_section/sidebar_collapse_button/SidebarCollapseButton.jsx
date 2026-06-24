import clsx from "clsx";
import { useI18n } from "@useI18n";
import { Tooltip } from "@common_components";
import { useIsMainPageCompactMode } from "@logics_main";
import ArrowLeftSvg from "@images/arrow_left.svg?react";
import styles from "./SidebarCollapseButton.module.scss";

export const SidebarCollapseButton = () => {
    const { t } = useI18n();
    const { toggleIsMainPageCompactMode, currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    const isCompact = currentIsMainPageCompactMode.data === true;

    const classNames = clsx(styles.arrow_svg, {
        [styles.reverse]: isCompact,
    });

    return (
        <Tooltip
            title={isCompact
                ? t("main_page.sidebar_panel.expand_title")
                : t("main_page.sidebar_panel.collapse_title")}
            detail={isCompact
                ? t("main_page.sidebar_panel.expand_detail")
                : t("main_page.sidebar_panel.collapse_detail")}
            placement="right"
            className={clsx(styles.tooltip, { [styles.is_compact_mode]: isCompact })}
            contentClassName={styles.tooltip_content}
            usePortal
        >
            <button
                className={clsx(styles.button, { [styles.is_compact_mode]: isCompact })}
                onClick={toggleIsMainPageCompactMode}
                aria-label={isCompact
                    ? t("main_page.sidebar_panel.expand_title")
                    : t("main_page.sidebar_panel.collapse_title")}
            >
                <ArrowLeftSvg className={classNames} />
            </button>
        </Tooltip>
    );
};
