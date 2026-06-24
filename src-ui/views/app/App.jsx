import { useEffect } from "react";
import { useI18n } from "@useI18n";

import {
    KeyEventController,
    StartPythonController,
    GlobalHotKeyController,
    UiLanguageController,
    ConfigPageCloseTriggerController,
    UiSizeController,
    FontFamilyController,
    TransparencyController,
    CornerRadiusController,
    PerformanceModeController,
} from "./_app_controllers";

import styles from "./App.module.scss";

import { MainPage } from "./main_page/MainPage";
import { ConfigPage } from "./config_page/ConfigPage";
import { DesktopOverlayBridge } from "./desktop_overlay/DesktopOverlayBridge";

import {
    WindowTitleBar,
    StartupStatusBanner,
    UpdateNotificationController,
    UpdatingComponent,
    ModalController,
    SnackbarController,
    AppErrorBoundary,
} from "./others";

import { useIsBackendReady, useIsSoftwareUpdating, useWindow } from "@logics_common";
import { isTauriRuntime } from "@logics_common/tauriRuntime.js";

const THEME_ACCENT_CLASSES = [
    "theme-neon-cyan",
    "theme-midnight-purple",
    "theme-emerald-green",
    "theme-sakura-pink",
];

export const App = () => {
    const { i18n } = useI18n();
    const isTauri = isTauriRuntime();

    useEffect(() => {
        const savedTheme = localStorage.getItem("theme_accent") || "theme-neon-cyan";
        document.documentElement.classList.remove(...THEME_ACCENT_CLASSES);
        document.documentElement.classList.add(
            THEME_ACCENT_CLASSES.includes(savedTheme) ? savedTheme : "theme-neon-cyan"
        );
    }, []);

    return (
        <div className={styles.container}>
            <AppErrorBoundary >
                <KeyEventController />
                {isTauri && <StartPythonController />}
                {isTauri && <GlobalHotKeyController />}
                <UiLanguageController />
                <ConfigPageCloseTriggerController />
                <UiSizeController />
                <FontFamilyController />
                <TransparencyController />
                <CornerRadiusController />
                <PerformanceModeController />
                <DesktopOverlayBridge />
                <Contents key={i18n.language} />

                <SnackbarController />
            </AppErrorBoundary>
        </div>
    );
};

const Contents = () => {
    const { WindowGeometryController } = useWindow();
    const { currentIsSoftwareUpdating } = useIsSoftwareUpdating();
    return (
        <>
            <WindowGeometryController />

            <WindowTitleBar />
            <StartupStatusBanner />
            <UpdateNotificationController />
            {currentIsSoftwareUpdating.data === false
            ?
            <div className={styles.pages_wrapper}>
                <ConfigPage />
                <MainPage />
                <ModalController />
            </div>
            :
            <UpdatingComponent />
            }
        </>
    );
};
