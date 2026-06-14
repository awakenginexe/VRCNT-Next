import { useState } from "react";
import { useSoftwareVersion } from "./useSoftwareVersion";
import { useNotificationStatus } from "./useNotificationStatus";

export const useUpdateSoftware = () => {
    const { currentLatestSoftwareVersionInfo } = useSoftwareVersion();
    const { showNotification_Error, showNotification_Success } = useNotificationStatus();
    const [updateState, setUpdateState] = useState({
        status: "idle",
        progress: 0,
        message: "",
    });

    const openReleaseFallback = () => {
        const releaseUrl = currentLatestSoftwareVersionInfo.data.release_url;
        if (releaseUrl) window.open(releaseUrl, "_blank", "noopener,noreferrer");
    };

    const updateSoftware = async () => {
        try {
            setUpdateState({ status: "opening", progress: 1, message: "Opening releases..." });
            openReleaseFallback();
            showNotification_Success("Opened VRCNT-Next releases.");
            setUpdateState({ status: "idle", progress: 0, message: "" });
        } catch (error) {
            console.error("Update failed:", error);
            setUpdateState({
                status: "error",
                progress: 0,
                message: "Could not open releases.",
            });
            showNotification_Error(`Could not open releases: ${String(error)}`, { hide_duration: 10000 });
        }
    };

    return {
        updateSoftware,
        updateState,
    };
};
