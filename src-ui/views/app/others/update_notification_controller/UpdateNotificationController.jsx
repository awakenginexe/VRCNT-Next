import { useEffect, useRef } from "react";
import { useStore_OpenedQuickSetting } from "@store";
import {
    useIsBackendReady,
    useNotificationStatus,
    useSoftwareVersion,
} from "@logics_common";

export const UpdateNotificationController = () => {
    const hasNotifiedRef = useRef(false);
    const { currentIsBackendReady } = useIsBackendReady();
    const { currentLatestSoftwareVersionInfo } = useSoftwareVersion();
    const { showNotification_Warning } = useNotificationStatus();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();

    useEffect(() => {
        if (currentIsBackendReady.data !== true) return;
        if (currentLatestSoftwareVersionInfo.data.is_update_available !== true) return;
        if (hasNotifiedRef.current === true) return;

        hasNotifiedRef.current = true;
        showNotification_Warning(
            `VRCNT-Next ${currentLatestSoftwareVersionInfo.data.new_version} is available. Open the Update button to visit GitHub Releases.`,
            {
                category_id: "software_update_available",
                hide_duration: 10000,
            },
        );
        updateOpenedQuickSetting("update_software");
    }, [
        currentIsBackendReady.data,
        currentLatestSoftwareVersionInfo.data.is_update_available,
        currentLatestSoftwareVersionInfo.data.new_version,
        showNotification_Warning,
        updateOpenedQuickSetting,
    ]);

    return null;
};
