import { useState } from "react";
import clsx from "clsx";
import styles from "./ResourceMonitor.module.scss";
import { useResourceUsage } from "@logics_common";
import { formatResourceMetric } from "@logics_common/resourceUsageUtils.js";

const RESOURCE_ITEMS = [
    { key: "cpu", label: "CPU" },
    { key: "gpu", label: "GPU" },
    { key: "ram", label: "RAM" },
    { key: "vram", label: "VRAM" },
];

export const ResourceMonitor = () => {
    const {
        currentResourceUsage,
        gpuMonitorSelection,
        setGpuMonitorSelection,
    } = useResourceUsage();
    const [openGpuMenuCardKey, setOpenGpuMenuCardKey] = useState(null);
    const resourceUsage = currentResourceUsage.data;
    const gpuDevices = resourceUsage?.gpu_devices ?? [];
    const canSelectGpu = gpuDevices.length > 0;

    const closeGpuMenu = () => setOpenGpuMenuCardKey(null);
    const toggleGpuMenu = (cardKey) => {
        if (canSelectGpu) {
            setOpenGpuMenuCardKey((current) => current === cardKey ? null : cardKey);
        }
    };

    const selectGpuMonitor = (selection) => {
        setGpuMonitorSelection(selection);
        closeGpuMenu();
    };

    return (
        <div className={styles.container}>
            {RESOURCE_ITEMS.map((item) => (
                <ResourceCard
                    key={item.key}
                    label={item.label}
                    metric={resourceUsage?.[item.key]}
                    isGpuSelectable={["gpu", "vram"].includes(item.key) && canSelectGpu}
                    isGpuMenuOpen={openGpuMenuCardKey === item.key}
                    onToggleGpuMenu={() => toggleGpuMenu(item.key)}
                    gpuDevices={gpuDevices}
                    selectedGpuIndex={resourceUsage?.selected_gpu_index}
                    gpuMonitorSelection={gpuMonitorSelection}
                    onSelectGpuMonitor={selectGpuMonitor}
                />
            ))}
        </div>
    );
};

const ResourceCard = ({
    label,
    metric,
    isGpuSelectable,
    isGpuMenuOpen,
    onToggleGpuMenu,
    gpuDevices,
    selectedGpuIndex,
    gpuMonitorSelection,
    onSelectGpuMonitor,
}) => {
    const isAvailable = metric?.available && metric.percent !== null && metric.percent !== undefined;
    const percent = isAvailable ? Math.max(0, Math.min(100, Number(metric.percent))) : 0;
    const cardClassName = clsx(styles.card, {
        [styles.is_selectable]: isGpuSelectable,
        [styles.is_menu_open]: isGpuMenuOpen,
    });

    return (
        <div className={cardClassName} onClick={isGpuSelectable ? onToggleGpuMenu : undefined}>
            <div className={styles.card_header}>
                <div className={styles.label_group}>
                    <p className={styles.label}>{label}</p>
                    {isGpuSelectable && (
                        <p className={styles.gpu_selection_label}>
                            {getGpuSelectionLabel(gpuMonitorSelection, selectedGpuIndex)}
                        </p>
                    )}
                </div>
                <p className={styles.value}>{formatResourceMetric(metric)}</p>
            </div>
            <div className={styles.meter_track}>
                <span
                    className={styles.meter_fill}
                    style={{ width: `${percent}%` }}
                    data-unavailable={!isAvailable}
                />
            </div>
            {isGpuMenuOpen && (
                <GpuMonitorMenu
                    gpuDevices={gpuDevices}
                    selectedGpuIndex={selectedGpuIndex}
                    gpuMonitorSelection={gpuMonitorSelection}
                    onSelectGpuMonitor={onSelectGpuMonitor}
                />
            )}
        </div>
    );
};

const getGpuSelectionLabel = (selection, selectedGpuIndex) => {
    if (selection?.mode === "manual") return `GPU ${selection.device_index}`;
    if (selectedGpuIndex !== null && selectedGpuIndex !== undefined) return `Auto GPU ${selectedGpuIndex}`;
    return "Auto";
};

const GpuMonitorMenu = ({
    gpuDevices,
    selectedGpuIndex,
    gpuMonitorSelection,
    onSelectGpuMonitor,
}) => {
    const isAutoSelected = gpuMonitorSelection?.mode !== "manual";

    return (
        <div className={styles.gpu_menu} onClick={(event) => event.stopPropagation()}>
            <button
                className={clsx(styles.gpu_menu_item, {
                    [styles.is_selected]: isAutoSelected,
                })}
                onClick={() => onSelectGpuMonitor({ mode: "auto", device_index: null })}
            >
                <span className={styles.gpu_menu_title}>Auto</span>
                <span className={styles.gpu_menu_desc}>
                    AI GPU{selectedGpuIndex !== null && selectedGpuIndex !== undefined ? ` ${selectedGpuIndex}` : ""}
                </span>
            </button>
            {gpuDevices.map((device) => (
                <button
                    key={device.device_index}
                    className={clsx(styles.gpu_menu_item, {
                        [styles.is_selected]:
                            gpuMonitorSelection?.mode === "manual" &&
                            gpuMonitorSelection.device_index === device.device_index,
                    })}
                    onClick={() => onSelectGpuMonitor({ mode: "manual", device_index: device.device_index })}
                >
                    <span className={styles.gpu_menu_title}>GPU {device.device_index}</span>
                    <span className={styles.gpu_menu_desc}>{device.device_name}</span>
                </button>
            ))}
        </div>
    );
};
