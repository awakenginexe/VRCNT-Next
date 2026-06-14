import styles from "./ConfigPage.module.scss";

import { Topbar } from "./topbar/Topbar.jsx";
import { SidebarSection } from "./sidebar_section/SidebarSection.jsx";
import { SettingSection } from "./setting_section/SettingSection.jsx";
import { useIsOpenedConfigPage } from "@logics_common";

export const ConfigPage = () => {
    const { currentIsOpenedConfigPage, setIsOpenedConfigPage } = useIsOpenedConfigPage();

    if (!currentIsOpenedConfigPage.data) return null;

    return (
        <div className={styles.page}>
            <div className={styles.scrim} onClick={() => setIsOpenedConfigPage(false)} />
            <div className={styles.container}>
                <SidebarSection />
                <div className={styles.content_wrapper}>
                    <Topbar />
                    <div className={styles.main_container}>
                        <SettingSection />
                    </div>
                </div>
            </div>
        </div>
    );
};
