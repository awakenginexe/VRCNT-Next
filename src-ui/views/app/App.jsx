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
    BlockingOperationOverlay,
} from "./others";

import {
    useBlockingOperation,
    useIsSoftwareUpdating,
    useWindow,
} from "@logics_common";
import { getMainFunctionPendingCopyKey } from "@logics_common/blockingOperationState.js";
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
    const { t } = useI18n();
    const { WindowGeometryController } = useWindow();
    const { currentIsSoftwareUpdating } = useIsSoftwareUpdating();
    const { isBlocking, operation } = useBlockingOperation();
    const overlayProps = operation === null
        ? null
        : (() => {
            const isStartup = operation.id === "startup";
            const phase = isStartup
                ? (operation.phaseKey
                    ? t(operation.phaseKey)
                    : operation.phase ?? "")
                : t(getMainFunctionPendingCopyKey(
                    operation.id,
                    operation.elapsedMs,
                ));
            const detail = operation.detailKey
                ? t(operation.detailKey)
                : operation.detail ?? "";
            const progressText = operation.progress.kind === "determinate"
                ? t("blocking_operation.progress_steps", {
                    current: operation.progress.value,
                    total: operation.progress.max,
                })
                : t("blocking_operation.progress_indeterminate");

            return {
                operationId: operation.id,
                title: t(operation.titleKey),
                phase,
                detail,
                progress: operation.progress,
                progressLabel: t("blocking_operation.progress_label"),
                progressText,
                elapsedText: t("blocking_operation.elapsed", {
                    seconds: Math.floor(operation.elapsedMs / 1000),
                }),
            };
        })();

    return (
        <>
            <WindowGeometryController />

            <WindowTitleBar />
            <div className={styles.app_body}>
                <StartupStatusBanner />
                <UpdateNotificationController />
                <div
                    className={styles.pages_wrapper}
                    inert={isBlocking ? "" : undefined}
                >
                    {currentIsSoftwareUpdating.data === false ? (
                        <>
                            <ConfigPage />
                            <MainPage />
                            <ModalController />
                        </>
                    ) : <UpdatingComponent />}
                </div>
                {overlayProps ? (
                    <BlockingOperationOverlay
                        open={isBlocking}
                        operationId={overlayProps.operationId}
                        title={overlayProps.title}
                        phase={overlayProps.phase}
                        detail={overlayProps.detail}
                        progress={overlayProps.progress}
                        progressLabel={overlayProps.progressLabel}
                        progressText={overlayProps.progressText}
                        elapsedText={overlayProps.elapsedText}
                    />
                ) : null}
            </div>
        </>
    );
};
