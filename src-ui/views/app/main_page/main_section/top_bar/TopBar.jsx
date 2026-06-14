import styles from "./TopBar.module.scss";

import { RightSideComponents } from "./right_side_components/RightSideComponents";

export const TopBar = () => {
    return (
        <div className={styles.container}>
            <div className={styles.status_strip}>
                <p className={styles.product_name}>VRCNT-Next</p>
                <p className={styles.product_desc}>VRChat Next Translation</p>
            </div>
            <RightSideComponents />
        </div>
    );
};
