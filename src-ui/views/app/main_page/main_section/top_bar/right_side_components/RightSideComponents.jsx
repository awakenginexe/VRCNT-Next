import { useI18n } from "@useI18n";
import styles from "./RightSideComponents.module.scss";
import HelpSvg from "@images/help.svg?react";

import { useStore_OpenedQuickSetting } from "@store";
import {
    useIsOscAvailable,
    useSoftwareVersion,
} from "@logics_common";

import {
    useAppearance,
    useVr,
    useOthers,
} from "@logics_configs";
import { OpenQuickSettingButton } from "./_buttons/OpenQuickSettingButton";

import { generateLocalizedDocumentUrl } from "@ui_configs";

export const RightSideComponents = () => {
    const { currentUiLanguage } = useAppearance();

    return (
        <div className={styles.container}>

            <OpenUpdateQuickSetting />
            <OpenVrcMicMuteSyncQuickSetting />
            <OpenOverlayQuickSetting />
            <a
                className={styles.help_and_info_button}
                href={generateLocalizedDocumentUrl(currentUiLanguage.data).vrct_document_ui_guide_url}
                target="_blank"
                rel="noreferrer"
            >
                <HelpSvg className={styles.help_svg} />
            </a>
        </div>
    );
};

const OpenUpdateQuickSetting = () => {
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const { currentLatestSoftwareVersionInfo } = useSoftwareVersion();

    if (currentLatestSoftwareVersionInfo.data.is_update_available !== true) return null;

    const onClickFunction = () => {
        updateOpenedQuickSetting("update_software");
    };

    return (
        <OpenQuickSettingButton
            label="Update"
            variable={true}
            onClickFunction={onClickFunction}
        />
    );
};

const OpenOverlayQuickSetting = () => {
    // const { t } = useI18n();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const {
        currentIsEnabledOverlaySmallLog,
        currentIsEnabledOverlayLargeLog,
    } = useVr();

    const onClickFunction = () => {
        updateOpenedQuickSetting("overlay");
    };

    const is_enable = currentIsEnabledOverlaySmallLog.data === true || currentIsEnabledOverlayLargeLog.data === true;

    return (
        <OpenQuickSettingButton
            label="Overlay(VR)"
            variable={is_enable}
            onClickFunction={onClickFunction}
        />
    );
};
const OpenVrcMicMuteSyncQuickSetting = () => {
    const { t } = useI18n();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const { currentIsOscAvailable } = useIsOscAvailable();
    const { currentEnableVrcMicMuteSync } = useOthers();

    const onClickFunction = () => {
        updateOpenedQuickSetting("vrc_mic_mute_sync");
    };

    return (
        <OpenQuickSettingButton
            label={t("config_page.others.vrc_mic_mute_sync.label")}
            variable={currentEnableVrcMicMuteSync.data}
            is_available={currentIsOscAvailable.data}
            onClickFunction={onClickFunction}
        />
    );
};
