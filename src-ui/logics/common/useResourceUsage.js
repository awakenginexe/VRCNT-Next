import { useCallback, useEffect, useRef, useState } from "react";
import { useStore_ResourceUsage } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";
import {
    DEFAULT_GPU_MONITOR_SELECTION,
    GPU_MONITOR_SELECTION_STORAGE_KEY,
    normalizeGpuMonitorSelection,
} from "./resourceUsageUtils.js";

const readInitialGpuMonitorSelection = () => {
    if (typeof window === "undefined") return DEFAULT_GPU_MONITOR_SELECTION;

    try {
        return normalizeGpuMonitorSelection(
            JSON.parse(window.localStorage.getItem(GPU_MONITOR_SELECTION_STORAGE_KEY)),
        );
    } catch {
        return DEFAULT_GPU_MONITOR_SELECTION;
    }
};

export const useResourceUsage = () => {
    const { currentResourceUsage, updateResourceUsage } = useStore_ResourceUsage();
    const { asyncStdoutToPython } = useStdoutToPython();
    const [gpuMonitorSelection, setGpuMonitorSelectionState] = useState(readInitialGpuMonitorSelection);
    const gpuMonitorSelectionRef = useRef(gpuMonitorSelection);

    useEffect(() => {
        gpuMonitorSelectionRef.current = gpuMonitorSelection;
    }, [gpuMonitorSelection]);

    const requestResourceUsage = useCallback((selection = gpuMonitorSelectionRef.current) => {
        asyncStdoutToPython("/get/data/resource_usage", normalizeGpuMonitorSelection(selection));
    }, []);

    const setGpuMonitorSelection = useCallback((selection) => {
        const normalizedSelection = normalizeGpuMonitorSelection(selection);
        setGpuMonitorSelectionState(normalizedSelection);

        if (typeof window !== "undefined") {
            window.localStorage.setItem(
                GPU_MONITOR_SELECTION_STORAGE_KEY,
                JSON.stringify(normalizedSelection),
            );
        }

        requestResourceUsage(normalizedSelection);
    }, [requestResourceUsage]);

    useEffect(() => {
        requestResourceUsage();
        const timer = setInterval(requestResourceUsage, 2500);
        return () => clearInterval(timer);
    }, [requestResourceUsage]);

    return {
        currentResourceUsage,
        updateResourceUsage,
        requestResourceUsage,
        gpuMonitorSelection,
        setGpuMonitorSelection,
    };
};
