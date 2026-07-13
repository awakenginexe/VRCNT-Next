import { useI18n } from "@useI18n";
import clsx from "clsx";
import { useEffect, useState } from "react";
import styles from "./MainFunctionSwitch.module.scss";
import TranslationSvg from "@images/translation.svg?react";
import MicSvg from "@images/mic.svg?react";
import HeadphonesSvg from "@images/headphones.svg?react";
import ForegroundSvg from "@images/foreground.svg?react";
import { Tooltip } from "@common_components";
import {
    useIsMainPageCompactMode,
    useMainFunction,
} from "@logics_main";
import { useIsBackendReady as useCommonIsBackendReady } from "@logics_common";
import { getMainFunctionPendingCopyKey } from "@logics_common/blockingOperationState.js";
import { getMainFunctionTooltipMeta } from "./mainFunctionTooltipMeta.js";

export const MainFunctionSwitch = ({ forceCompact = false }) => {
    const { t } = useI18n();
    const { currentIsBackendReady } = useCommonIsBackendReady();

    const {
        toggleTranslation, currentTranslationStatus,
        toggleTranscriptionSend, currentTranscriptionSendStatus,
        toggleTranscriptionReceive, currentTranscriptionReceiveStatus,
        toggleForeground, currentForegroundStatus,
    } = useMainFunction();


    const switch_items = [
        {
            switch_id: "translation",
            label: t("main_page.translation"),
            SvgComponent: TranslationSvg,
            currentState: currentTranslationStatus,
            toggleFunction: toggleTranslation,
            isDisabled: currentIsBackendReady.data !== true,
        },
        {
            switch_id: "transcription_send",
            label: t("main_page.transcription_send"),
            SvgComponent: MicSvg,
            currentState: currentTranscriptionSendStatus,
            toggleFunction: toggleTranscriptionSend,
            isDisabled: currentIsBackendReady.data !== true,
        },
        {
            switch_id: "transcription_receive",
            label: t("main_page.transcription_receive"),
            SvgComponent: HeadphonesSvg,
            currentState: currentTranscriptionReceiveStatus,
            toggleFunction: toggleTranscriptionReceive,
            isDisabled: currentIsBackendReady.data !== true,
        },
        {
            switch_id: "foreground",
            label: t("main_page.foreground"),
            SvgComponent: ForegroundSvg,
            currentState: currentForegroundStatus,
            toggleFunction: toggleForeground,
            isDisabled: false,
        },
    ];

    return (
        <div className={styles.container}>
            {switch_items.map(item => (
                <SwitchContainer
                    key={item.switch_id}
                    switch_id={item.switch_id}
                    switchLabel={item.label}
                    currentState={item.currentState}
                    toggleFunction={item.toggleFunction}
                    SvgComponent={item.SvgComponent}
                    isDisabled={item.isDisabled}
                    forceCompact={forceCompact}
                >
                </SwitchContainer>
            ))}
        </div>
    );
};

export const SwitchContainer = ({ switchLabel, switch_id, children, currentState, toggleFunction, SvgComponent, isDisabled = false, forceCompact = false }) => {
    const { t } = useI18n();
    const [is_hovered, setIsHovered] = useState(false);
    const [is_mouse_down, setIsMouseDown] = useState(false);
    const [pending_elapsed_ms, setPendingElapsedMs] = useState(0);

    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();
    const isCompact = forceCompact || currentIsMainPageCompactMode.data;

    const getClassNames = (baseClass) => clsx(baseClass, {
        [styles.is_compact_mode]: isCompact,
        [styles.is_active]: (currentState.data === true),
        [styles.is_pending]: (currentState.state === "pending"),
        [styles.is_disabled]: isDisabled,
        [styles.is_hovered]: is_hovered,
        [styles.is_mouse_down]: is_mouse_down,
    });

    const onMouseEnter = () => setIsHovered(true);
    const onMouseLeave = () => setIsHovered(false);
    const onMouseDown = () => setIsMouseDown(true);
    const onMouseUp = () => setIsMouseDown(false);
    const onClick = () => {
        if (isDisabled || currentState.state === "pending") return;
        toggleFunction();
    };

    useEffect(() => {
        if (currentState.state !== "pending") {
            setPendingElapsedMs(0);
            return;
        }
        const startedAt = Date.now();
        const timer = setInterval(() => {
            setPendingElapsedMs(Date.now() - startedAt);
        }, 1_000);
        return () => clearInterval(timer);
    }, [currentState.state]);

    const getPendingMessage = () => t(
        getMainFunctionPendingCopyKey(switch_id, pending_elapsed_ms),
    );
    const tooltipMeta = getMainFunctionTooltipMeta(switch_id);

    return (
        <Tooltip
            title={t(tooltipMeta.tooltipTitleKey)}
            detail={t(tooltipMeta.tooltipDetailKey)}
            placement="right"
            className={styles.switch_tooltip}
            contentClassName={styles.switch_tooltip_content}
            usePortal
        >
            <button
                type="button"
                role="switch"
                aria-checked={currentState.data === true}
                aria-busy={currentState.state === "pending"}
                disabled={isDisabled}
                aria-disabled={currentState.state === "pending"}
                className={getClassNames(styles.switch_container)}
                onMouseEnter={onMouseEnter}
                onMouseLeave={onMouseLeave}
                onMouseDown={onMouseDown}
                onMouseUp={onMouseUp}
                onClick={onClick}
            >
                <span className={styles.label_wrapper}>
                    <SvgComponent className={getClassNames(styles.switch_svg)} />
                    <span className={styles.label_text_wrapper}>
                        <span className={getClassNames(styles.switch_label)}>{switchLabel}</span>
                        {currentState.state === "pending" && (
                            <span className={getClassNames(styles.pending_status)}>{getPendingMessage()}</span>
                        )}
                        {isDisabled && currentState.state !== "pending" && (
                            <span className={getClassNames(styles.pending_status)}>{t("main_page.language_panels.backend_waiting")}</span>
                        )}
                    </span>
                    {children}
                </span>

                <span className={getClassNames(styles.toggle_control)}>
                    <span className={getClassNames(styles.control)}></span>
                </span>

                <span className={getClassNames(styles.switch_indicator)}></span>
                {(currentState.state === "pending")
                    ? <span className={styles.loader}></span>
                    : null
                }
            </button>
        </Tooltip>
    );
};
