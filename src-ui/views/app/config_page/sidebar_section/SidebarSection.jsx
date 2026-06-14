import styles from "./SidebarSection.module.scss";
import { useState } from "react";
import clsx from "clsx";
import { useI18n } from "@useI18n";
import {
    useStore_SelectedConfigTabId,
    useStore_IsBreakPoint,
} from "@store";

import MicSvg from "@images/mic.svg?react";
import AppearanceSvg from "@images/mui_palette.svg?react";
import TranslationSvg from "@images/translation.svg?react";
import GraphicEqSvg from "@images/mui_graphic_eq.svg?react";
import HMDSvg from "@images/mui_head_mounted_device.svg?react";
import DiscoverTuneSvg from "@images/mui_discover_tune.svg?react";
import KeyboardAltSvg from "@images/mui_keyboard_alt.svg?react";
import CodeBlocksSvg from "@images/mui_code_blocks.svg?react";
import CrownSvg from "@images/mui_crown.svg?react";
import logoBadge from "@images/vrcnt_logo_badge.png";

import { Tooltip } from "@common_components";
import { VersionLabel } from "../version_label/VersionLabel.jsx";
import { getSidebarTabMeta } from "./sidebarTabMeta.js";

export const SidebarSection = () => {
    const { currentIsBreakPoint } = useStore_IsBreakPoint();
    const [isHovered, setIsHovered] = useState(false);

    const container_class_names = clsx(styles.container, {
        [styles.is_small]: currentIsBreakPoint.data,
    });

    const isCompact = currentIsBreakPoint.data && !isHovered;

    return (
        <div
            className={container_class_names}
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
        >
            <div className={styles.scroll_container}>
                <div className={styles.scroll_content}>
                    <div className={styles.tabs_wrapper}>
                        <Tab tab_id="device" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="appearance" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="translation" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="transcription" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="vr" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="others" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="hotkeys" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="advanced_settings" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                    </div>
                    <div className={styles.separated_tabs_wrapper}>
                        <Tab tab_id="supporters" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                        <Tab tab_id="about_vrct" isSmall={currentIsBreakPoint.data} isHovered={isHovered} />
                    </div>
                </div>
                <VersionLabel isCompact={isCompact} />
            </div>
        </div>
    );
};

const TabIcon = ({ tab_id, className }) => {
    switch (tab_id) {
        case "device": return <MicSvg className={className} />;
        case "appearance": return <AppearanceSvg className={clsx(className, styles.mui_icon)} />;
        case "translation": return <TranslationSvg className={className} />;
        case "transcription": return <GraphicEqSvg className={clsx(className, styles.mui_icon)} />;
        case "vr": return <HMDSvg className={clsx(className, styles.mui_icon)} />;
        case "others" : return <DiscoverTuneSvg className={clsx(className, styles.mui_icon)} />;
        case "hotkeys": return <KeyboardAltSvg className={clsx(className, styles.mui_icon)} />;
        case "advanced_settings": return <CodeBlocksSvg className={clsx(className, styles.mui_icon)} />;
        case "supporters": return <CrownSvg className={clsx(className, styles.mui_icon, styles.supporters_icon)} />;
        case "about_vrct": return <img className={clsx(className, styles.about_vrct_icon)} src={logoBadge} alt="" />;
        default: return null;
    }
};

const Tab = (props) => {
    const { t } = useI18n();
    const { updateSelectedConfigTabId, currentSelectedConfigTabId } = useStore_SelectedConfigTabId();
    const tabMeta = getSidebarTabMeta(props.tab_id, t);

    const onclickFunction = () => {
        updateSelectedConfigTabId(props.tab_id);
    };

    const is_selected = currentSelectedConfigTabId.data === props.tab_id;

    const tab_container_class_names = clsx(styles["tab_container"], {
        [styles["is_selected"]]: is_selected,
        [styles["is_small"]]: props.isSmall && !props.isHovered
    });
    const switch_indicator_class_names = clsx(styles["switch_indicator"], {
        [styles["is_selected"]]: is_selected
    });
    const onKeyDown = (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onclickFunction();
    };

    return (
        <Tooltip
            title={tabMeta.tooltipTitle}
            detail={tabMeta.tooltipDetail}
            placement="right"
            className={styles.tab_tooltip}
            contentClassName={styles.tab_tooltip_content}
            disabled={!props.isSmall}
        >
            <div
                className={tab_container_class_names}
                onClick={onclickFunction}
                onKeyDown={onKeyDown}
                role="button"
                tabIndex={is_selected ? -1 : 0}
            >
                <div className={styles.tab_icon_wrapper}>
                    <TabIcon tab_id={props.tab_id} className={styles.tab_icon} />
                </div>
                <p className={clsx(styles.tab_text, {
                    [styles.hide]: props.isSmall && !props.isHovered
                })}>{tabMeta.label}</p>
                <div className={switch_indicator_class_names}></div>
            </div>
        </Tooltip>
    );
};
