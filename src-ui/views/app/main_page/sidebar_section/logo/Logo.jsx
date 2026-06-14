import styles from "./Logo.module.scss";
import logoBadge from "@images/vrcnt_logo_badge.png";

export const Logo = () => {
    return (
        <div className={styles.container}>
            <img className={styles.logo_badge} src={logoBadge} alt="VRCNT-Next" />
        </div>
    );
};
