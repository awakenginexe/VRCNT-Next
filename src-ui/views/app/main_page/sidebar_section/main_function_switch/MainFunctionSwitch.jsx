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
    const [pending_seconds, setPendingSeconds] = useState(0);

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
            setPendingSeconds(0);
            return;
        }
        const startedAt = Date.now();
        const timer = setInterval(() => {
            setPendingSeconds(Math.floor((Date.now() - startedAt) / 1000));
        }, 1000);
        return () => clearInterval(timer);
    }, [currentState.state]);

    const pending_messages = {
        translation: {
            start: "main_page.main_function_pending.translation_start",
            warm: "main_page.main_function_pending.translation_warm",
            long: "main_page.main_function_pending.translation_long",
        },
        transcription_send: {
            start: "main_page.main_function_pending.transcription_send_start",
            warm: "main_page.main_function_pending.transcription_send_warm",
            long: "main_page.main_function_pending.transcription_send_long",
        },
        transcription_receive: {
            start: "main_page.main_function_pending.transcription_receive_start",
            warm: "main_page.main_function_pending.transcription_receive_warm",
            long: "main_page.main_function_pending.transcription_receive_long",
        },
        foreground: {
            start: "main_page.main_function_pending.foreground_start",
            warm: "main_page.main_function_pending.foreground_warm",
            long: "main_page.main_function_pending.foreground_long",
        },
    };

    const getPendingMessage = () => {
        const messages = pending_messages[switch_id] ?? pending_messages.foreground;
        if (pending_seconds >= 30) return t(messages.long);
        if (pending_seconds >= 5) return t(messages.warm);
        return t(messages.start);
    };
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
            <div className={getClassNames(styles.switch_container)}
                onMouseEnter={onMouseEnter}
                onMouseLeave={onMouseLeave}
                onMouseDown={onMouseDown}
                onMouseUp={onMouseUp}
                onClick={onClick}
            >
                <div className={styles.label_wrapper}>
                    <SvgComponent className={getClassNames(styles.switch_svg)} />
                    <div className={styles.label_text_wrapper}>
                        <p className={getClassNames(styles.switch_label)}>{switchLabel}</p>
                        {currentState.state === "pending" && (
                            <p className={getClassNames(styles.pending_status)}>{getPendingMessage()}</p>
                        )}
                        {isDisabled && currentState.state !== "pending" && (
                            <p className={getClassNames(styles.pending_status)}>{t("main_page.language_panels.backend_waiting")}</p>
                        )}
                    </div>
                    {children}
                </div>

                <div className={getClassNames(styles.toggle_control)}>
                    <span className={getClassNames(styles.control)}></span>
                </div>

                <div className={getClassNames(styles.switch_indicator)}></div>
                {(currentState.state === "pending")
                    ? <span className={styles.loader}></span>
                    : null
                }
            </div>
        </Tooltip>
    );
};
