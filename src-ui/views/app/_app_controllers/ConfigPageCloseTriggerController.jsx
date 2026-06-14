import { useEffect } from "react";

import {
    useVolume,
    useIsOpenedConfigPage,
} from "@logics_common";

import { useHotkeys } from "@logics_configs";

export const ConfigPageCloseTriggerController = () => {
    const { currentIsOpenedConfigPage } = useIsOpenedConfigPage();
    const {
        currentMicThresholdCheckStatus,
        volumeCheckStop_Mic,
        currentSpeakerThresholdCheckStatus,
        volumeCheckStop_Speaker,
    } = useVolume();

    const { registerShortcuts, unregisterAll } = useHotkeys();

    useEffect(() => {
        if (currentIsOpenedConfigPage.data === true) { // When config page is opened.
            unregisterAll();
        } else if (currentIsOpenedConfigPage.data === false) { // When config page is closed.
            registerShortcuts();
            if (currentMicThresholdCheckStatus.data === true) volumeCheckStop_Mic();
            if (currentSpeakerThresholdCheckStatus.data === true) volumeCheckStop_Speaker();
        }
    }, [currentIsOpenedConfigPage.data]);
    return null;
};
