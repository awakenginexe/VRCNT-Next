import { useEffect } from "react";
import { useStore_EnablePerformanceMode } from "@store";

export const PerformanceModeController = () => {
    const { currentEnablePerformanceMode } = useStore_EnablePerformanceMode();

    useEffect(() => {
        if (currentEnablePerformanceMode.data) {
            document.documentElement.classList.add("performance_mode");
        } else {
            document.documentElement.classList.remove("performance_mode");
        }
    }, [currentEnablePerformanceMode.data]);

    return null;
};
