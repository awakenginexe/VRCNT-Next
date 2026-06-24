import styles from "./TopBar.module.scss";

import { RightSideComponents } from "./right_side_components/RightSideComponents";
import { useI18n } from "@useI18n";
import { useIsBackendReady } from "@logics_common";

export const TopBar = () => {
    const { t } = useI18n();
    const { currentIsBackendReady } = useIsBackendReady();
    const isReady = currentIsBackendReady.data === true;

    return (
        <div className={styles.container}>
            <div className={styles.status_strip}>
                <div className={styles.product_copy}>
                    <p className={styles.product_name}>VRCNT-Next</p>
                    <p className={styles.product_desc}>Next Gen VRChat Translation</p>
                </div>
                <div className={styles.status_badge} data-ready={isReady}>
                    <span className={styles.status_dot}></span>
                    <p className={styles.status_label}>
                        {isReady ? t("main_page.state_text_enabled") : t("main_page.language_panels.backend_waiting")}
                    </p>
                </div>
            </div>
            <RightSideComponents />
        </div>
    );
};
