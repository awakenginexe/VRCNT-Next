import clsx from "clsx";
import { useI18n } from "@useI18n";
import styles from "./TranscriptionEngineLabel.module.scss";
import { useTranscription } from "@logics_configs";
import { useStore_IsOpenedTranscriptionEngineSelector } from "@store";
import { TranscriptionEngineSelector } from "./transcription_engine_selector/TranscriptionEngineSelector";
import {
    getAllowedTranscriptionComputeTypes,
    getQuickDeviceOptions,
    getSelectedDeviceMode,
    isAutoOnlyTranscriptionEngine,
} from "../transcriptionRuntimeUtils.js";

export const TranscriptionEngineLabel = () => {
    const { t } = useI18n();
    const {
        currentSelectedTranscriptionEngine,
        currentSelectableTranscriptionComputeDeviceList,
        currentSelectedTranscriptionComputeDevice,
        setSelectedTranscriptionComputeDevice,
        currentSelectedTranscriptionComputeType,
        setSelectedTranscriptionComputeType,
        currentSelectedWhisperWeightType,
        currentSelectedVoskWeightType,
        currentSelectedParakeetWeightType,
        currentSelectedSenseVoiceWeightType,
    } = useTranscription();

    const {
        currentIsOpenedTranscriptionEngineSelector,
        updateIsOpenedTranscriptionEngineSelector,
    } = useStore_IsOpenedTranscriptionEngineSelector();

    const engine = currentSelectedTranscriptionEngine?.data ?? t("main_page.language_panels.loading");
    const deviceMap = currentSelectableTranscriptionComputeDeviceList?.data ?? {};
    const selectedDevice = currentSelectedTranscriptionComputeDevice?.data ?? null;
    const selectedMode = getSelectedDeviceMode(selectedDevice);
    const deviceOptions = getQuickDeviceOptions(deviceMap, engine);
    const activeDevice =
        deviceOptions.find((option) => option.id === selectedMode)?.device ??
        deviceOptions.find((option) => option.device)?.device ??
        selectedDevice;
    const computeTypeOptions = getAllowedTranscriptionComputeTypes({
        engine,
        device: activeDevice,
    });
    const selectedComputeType = currentSelectedTranscriptionComputeType?.data ?? "auto";
    const currentModelName =
        engine === "Whisper" ? currentSelectedWhisperWeightType?.data :
        engine === "Vosk" ? currentSelectedVoskWeightType?.data :
        engine === "Parakeet" ? currentSelectedParakeetWeightType?.data :
        engine === "SenseVoice" ? currentSelectedSenseVoiceWeightType?.data :
        null;

    const openSelector = () => {
        updateIsOpenedTranscriptionEngineSelector(!currentIsOpenedTranscriptionEngineSelector.data);
    };

    const selectDeviceMode = (mode) => {
        const target = deviceOptions.find((option) => option.id === mode);
        if (target?.device) {
            setSelectedTranscriptionComputeDevice(target.device);
        }
    };

    const selectComputeType = (computeType) => {
        setSelectedTranscriptionComputeType(computeType);
    };

    return (
        <div className={styles.container}>
            <div className={styles.engine_label_button} onClick={openSelector}>
                <div className={styles.label_copy}>
                    <p className={styles.label_heading}>{t("main_page.language_panels.engine")}</p>
                    <p className={styles.label_value}>{engine}</p>
                    {currentModelName && <p className={styles.model_value}>{currentModelName}</p>}
                </div>
                <p className={styles.edit_hint}>{t("main_page.language_panels.change")}</p>
            </div>
            <div className={styles.quick_switch_block}>
                <div className={styles.quick_switch_header}>
                    <p className={styles.quick_switch_title}>{t("main_page.language_panels.device")}</p>
                    <p className={styles.quick_switch_hint}>{t("main_page.language_panels.device_desc")}</p>
                </div>
                <div className={styles.option_row}>
                    {deviceOptions.map((option) => (
                        <button
                            key={option.id}
                            type="button"
                            className={clsx(styles.option_button, {
                                [styles.is_selected]: selectedMode === option.id,
                                [styles.is_disabled]: option.disabled,
                            })}
                            onClick={() => selectDeviceMode(option.id)}
                            disabled={option.disabled}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
            </div>
            <div className={styles.quick_switch_block}>
                <div className={styles.quick_switch_header}>
                    <p className={styles.quick_switch_title}>{t("main_page.language_panels.processing_type")}</p>
                    <p className={styles.quick_switch_hint}>
                        {isAutoOnlyTranscriptionEngine(engine)
                            ? t("main_page.language_panels.processing_type_locked")
                            : t("main_page.language_panels.processing_type_whisper")}
                    </p>
                </div>
                <div className={styles.processing_scroll_area}>
                    <div className={styles.option_row}>
                        {computeTypeOptions.map((computeType) => (
                            <button
                                key={computeType}
                                type="button"
                                className={clsx(styles.option_button, styles.compute_type_button, {
                                    [styles.is_selected]: selectedComputeType === computeType,
                                })}
                                onClick={() => selectComputeType(computeType)}
                            >
                                {computeType}
                            </button>
                        ))}
                    </div>
                </div>
            </div>
            {currentIsOpenedTranscriptionEngineSelector.data &&
                <TranscriptionEngineSelector
                    selected_id={engine}
                />
            }
        </div>
    );
};
