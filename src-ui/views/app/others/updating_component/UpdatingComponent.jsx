import styles from "./UpdatingComponent.module.scss";
import { useI18n } from "@useI18n";
import { CircularProgress } from "@common_components";
import logoBadge from "@images/vrcnt_logo_badge.png";

export const UpdatingComponent = () => {
    const { t } = useI18n();

    return (
        <div className={styles.container}>
            <div className={styles.chato_box}>
                <img className={styles.chato_img} src={logoBadge} alt="" />
            </div>
            <div className={styles.circular_box}>
                <CircularProgress size="20rem" sx={{
                    color: "var(--primary_300_color)",
                }}/>
            </div>
            <p className={styles.label}>{t("main_page.updating")}</p>
        </div>
    );
};
