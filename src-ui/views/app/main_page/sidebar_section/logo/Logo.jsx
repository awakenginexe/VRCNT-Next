import clsx from "clsx";
import styles from "./Logo.module.scss";
import logoBadge from "@images/vrcnt_logo_badge.png";
import { useIsMainPageCompactMode } from "@logics_main";

export const Logo = () => {
    const { currentIsMainPageCompactMode } = useIsMainPageCompactMode();

    return (
        <div className={clsx(styles.container, {
            [styles.is_compact_mode]: currentIsMainPageCompactMode.data,
        })}>
            <img className={styles.logo_badge} src={logoBadge} alt="VRCNT-Next" />
            <div className={styles.logo_copy}>
                <p className={styles.logo_title}>VRCNT-Next</p>
                <p className={styles.logo_subtitle}>Next Gen VRChat Translation</p>
            </div>
        </div>
    );
};
