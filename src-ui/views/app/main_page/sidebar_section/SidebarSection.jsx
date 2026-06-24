import clsx from "clsx";

import styles from "./SidebarSection.module.scss";
import { useStore_IsOpenedLanguageSelector } from "@store";
import { useIsMainPageCompactMode } from "@logics_main";

import { Logo } from "./logo/Logo";
import { MainFunctionSwitch } from "./main_function_switch/MainFunctionSwitch";
import { OpenSettings } from "./open_settings/OpenSettings";
import { SidebarCollapseButton } from "./sidebar_collapse_button/SidebarCollapseButton";
import { DesktopOverlayButton } from "./desktop_overlay_button/DesktopOverlayButton";

export const SidebarSection = () => {
    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    const container_class_name = clsx(styles.container, {
        [styles.is_compact_mode]: currentIsMainPageCompactMode.data
    });
    const sidebar_width = currentIsMainPageCompactMode.data ? "8.8rem" : "28.8rem";
    const container_style = {
        width: sidebar_width,
        minWidth: sidebar_width,
        maxWidth: sidebar_width,
        flex: `0 0 ${sidebar_width}`,
    };

    const { currentIsOpenedLanguageSelector } = useStore_IsOpenedLanguageSelector();
    const scroll_container_class_names = clsx(styles.scroll_container, {
        [styles.is_opened]: (
            currentIsOpenedLanguageSelector.data.your_language === true ||
            currentIsOpenedLanguageSelector.data.your_translation_language === true ||
            currentIsOpenedLanguageSelector.data.target_language === true
        )
    });

    return (
        <div className={container_class_name} style={container_style}>
            <SidebarCollapseButton />
            <Logo />
            <div className={scroll_container_class_names}>
                <MainFunctionSwitch />
                <div className={styles.utility_actions}>
                    <DesktopOverlayButton />
                </div>
            </div>
            <OpenSettings />
        </div>
    );
};
