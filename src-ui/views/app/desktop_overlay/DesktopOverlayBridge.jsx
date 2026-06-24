import { useEffect } from "react";
import {
    DESKTOP_OVERLAY_CHANNEL,
    DESKTOP_OVERLAY_STORAGE_KEY,
    createDesktopOverlayPayload,
} from "@logics_common";
import {
    useStore_MessageLogs,
    useStore_TranslationStatus,
    useStore_TranscriptionSendStatus,
    useStore_TranscriptionReceiveStatus,
} from "@store";
import { useAppearance } from "@logics_configs";

export const DesktopOverlayBridge = () => {
    const { currentMessageLogs } = useStore_MessageLogs();
    const { currentTranslationStatus } = useStore_TranslationStatus();
    const { currentTranscriptionSendStatus } = useStore_TranscriptionSendStatus();
    const { currentTranscriptionReceiveStatus } = useStore_TranscriptionReceiveStatus();
    const { currentUiLanguage } = useAppearance();

    useEffect(() => {
        const payload = createDesktopOverlayPayload({
            messageLogs: currentMessageLogs.data,
            translationEnabled: currentTranslationStatus.data === true,
            speakingEnabled: currentTranscriptionSendStatus.data === true,
            listeningEnabled: currentTranscriptionReceiveStatus.data === true,
            uiLanguage: currentUiLanguage.data,
        });

        try {
            localStorage.setItem(DESKTOP_OVERLAY_STORAGE_KEY, JSON.stringify(payload));
            const channel = new BroadcastChannel(DESKTOP_OVERLAY_CHANNEL);
            channel.postMessage(payload);
            channel.close();
        } catch (error) {
            console.warn("Unable to publish desktop overlay payload.", error);
        }
    }, [
        currentMessageLogs.data,
        currentTranslationStatus.data,
        currentTranscriptionSendStatus.data,
        currentTranscriptionReceiveStatus.data,
        currentUiLanguage.data,
    ]);

    return null;
};
