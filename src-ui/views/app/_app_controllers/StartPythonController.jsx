import { invoke } from "@tauri-apps/api/core";
import { Command } from "@tauri-apps/plugin-shell";
import { useEffect, useRef } from "react";

import { useStdoutToPython } from "@useStdoutToPython";
import { useReceiveRoutes } from "@useReceiveRoutes";
import { store, useStore_SelectableFontFamilyList } from "@store";
import { arrayToObject } from "@utils";
import { useI18n } from "@useI18n";

import {
    useInitStatus,
    useIsBackendReady,
    useNotificationStatus,
} from "@logics_common";
import { useMainFunction } from "@logics_main";
import { useLanguageSettings } from "@logics_main";
import { isBenignSidecarStderr } from "@logics_common/sidecarStderrUtils.js";

export const StartPythonController = () => {
    const { asyncStartPython } = useStartPython();
    const hasRunRef = useRef(false);
    const { asyncFetchFonts } = useAsyncFetchFonts();

    useEffect(() => {
        if (!hasRunRef.current) {
            asyncStartPython().then(() => {
                startFeedingToWatchDogController();
                asyncFetchFonts();
            }).catch((err) => {
                console.error(err);
            });
        }
        return () => hasRunRef.current = true;
    }, []);

    return null;
};

const useStartPython = () => {
    const { receiveRoutes } = useReceiveRoutes();
    const { showNotification_Error } = useNotificationStatus();
    const { updateInitStatus } = useInitStatus();
    const { currentIsBackendReady } = useIsBackendReady();
    const { clearPendingMainFunctionStatuses } = useMainFunction();
    const { settleSelectedTranslationEngineSelection } = useLanguageSettings();
    const { t } = useI18n();
    const backendReadyRef = useRef(currentIsBackendReady.data);
    const startupErrorNotifiedRef = useRef(false);
    backendReadyRef.current = currentIsBackendReady.data;

    const markBackendStartupError = (error) => {
        const messageKey = "blocking_operation.startup_failed";
        const detailKey = "blocking_operation.startup_failed_detail";
        updateInitStatus({
            visible: true,
            phase: "error",
            message: t(messageKey),
            detail: t(detailKey),
            message_key: "blocking_operation.startup_failed",
            detail_key: "blocking_operation.startup_failed_detail",
        });

        if (!startupErrorNotifiedRef.current) {
            startupErrorNotifiedRef.current = true;
            showNotification_Error(t(messageKey), {
                hide_duration: null,
                category_id: "backend_startup_failed",
            });
        }
        console.error("Backend startup failed.", error);
    };

    const asyncStartPython = async () => {
        const command = Command.sidecar("bin/VRCT-sidecar");
        command.on("error", (error) => {
            markBackendStartupError(error);
        });
        command.on("close", (termination) => {
            store.backend_subprocess = null;
            if (backendReadyRef.current !== true) {
                markBackendStartupError(termination);
                return;
            }
            clearPendingMainFunctionStatuses();
            settleSelectedTranslationEngineSelection();
            showNotification_Error(
                t("blocking_operation.backend_disconnected"),
                {
                    hide_duration: null,
                    category_id: "backend_disconnected",
                },
            );
            console.error("Backend disconnected.", termination);
        });
        command.stdout.on("data", (line) => {
            let parsed_data = "";
            try {
                parsed_data = JSON.parse(line);
                receiveRoutes(parsed_data);
            } catch (error) {
                console.log(error, line);
            }
        });
        command.stderr.on("data", line => {
            if (isBenignSidecarStderr(line)) {
                console.debug("stderr", line);
                return;
            }
            showNotification_Error(
                `An error occurred. Please restart VRCNT-Next or contact the developers. The last line:${JSON.stringify(line)}`, { hide_duration: null }
            );
            console.error("stderr", line);
        });
        try {
            const backend_subprocess = await command.spawn();
            store.backend_subprocess = backend_subprocess;
        } catch (error) {
            markBackendStartupError(error);
            throw error;
        }
    };

    return { asyncStartPython };
};

const useAsyncFetchFonts = () => {
    const { updateSelectableFontFamilyList } = useStore_SelectableFontFamilyList();
    const asyncFetchFonts = async () => {
        try {
            let fonts = await invoke("get_font_list");
            fonts = fonts.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
            updateSelectableFontFamilyList(arrayToObject(fonts));
        } catch (error) {
            console.error("Error fetching fonts:", error);
        }
    };
    return { asyncFetchFonts };
};

const startFeedingToWatchDogController = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    setInterval(() => {
        asyncStdoutToPython("/run/feed_watchdog");
    }, 20000); // 20000ミリ秒 = 20秒
};
