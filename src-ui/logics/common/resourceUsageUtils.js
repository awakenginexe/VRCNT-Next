export const EMPTY_RESOURCE_USAGE = {
    cpu: { available: false, percent: null },
    gpu: { available: false, percent: null },
    ram: { available: false, percent: null },
    vram: { available: false, percent: null },
    gpu_devices: [],
    selected_gpu_index: null,
};

export const GPU_MONITOR_SELECTION_STORAGE_KEY = "vrcnt_next.resource_monitor.gpu_selection";
export const DEFAULT_GPU_MONITOR_SELECTION = { mode: "auto", device_index: null };

export const formatResourceMetric = (metric) => {
    if (!metric?.available || metric.percent === null || metric.percent === undefined) return "Unavailable";
    const value = Number(metric.percent);
    if (Number.isNaN(value)) return "Unavailable";
    return `${value.toFixed(1)}%`;
};

export const normalizeGpuMonitorSelection = (selection = DEFAULT_GPU_MONITOR_SELECTION) => {
    if (selection?.mode === "manual") {
        const deviceIndex = Number(selection.device_index);
        if (Number.isInteger(deviceIndex) && deviceIndex >= 0) {
            return { mode: "manual", device_index: deviceIndex };
        }
    }

    return DEFAULT_GPU_MONITOR_SELECTION;
};
