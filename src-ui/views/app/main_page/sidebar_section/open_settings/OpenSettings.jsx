import styles from "./OpenSettings.module.scss";
import { useIsOpenedConfigPage } from "@logics_common";
import { Tooltip } from "@common_components";
import ConfigurationSvg from "@images/configuration.svg?react";
import { getMainFunctionTooltipMeta } from "../main_function_switch/mainFunctionTooltipMeta.js";

export const OpenSettings = () => {
    const { setIsOpenedConfigPage } = useIsOpenedConfigPage();
    const tooltipMeta = getMainFunctionTooltipMeta("settings");

    const openConfigPage = () => {
        setIsOpenedConfigPage(true);
    };

    return (
        <div className={styles.container}>
            <Tooltip
                title={tooltipMeta.tooltipTitle}
                detail={tooltipMeta.tooltipDetail}
                placement="right"
                className={styles.settings_tooltip}
                contentClassName={styles.settings_tooltip_content}
                usePortal
            >
                <div className={styles.open_config_page_button} onClick={openConfigPage}>
                    <ConfigurationSvg className={styles.configuration_svg} />
                </div>
            </Tooltip>
        </div>
    );
};
