import clsx from "clsx";
import styles from "./StartUpProgressContainer.module.scss";
import logoBadge from "@images/vrcnt_logo_badge.png";

import { useInitProgress, useInitStatus } from "@logics_common";

export const StartUpProgressContainer = () => {
    const { currentInitProgress } = useInitProgress();
    const { currentInitStatus } = useInitStatus();

    const progress = currentInitProgress.data;
    return (
        <div className={styles.container}>
            <div className={styles.progress_bar_wrapper}>
                {[...Array(4)].map((_, index) => (
                    <div
                        key={index}
                        className={clsx(styles.progress_bar, {
                            [styles.progressed]: index < progress && progress !== 0,
                        })}
                    >
                        {index === 3
                            ?
                            <div className={styles.chato_box}>
                                <img className={styles.chato_img} src={logoBadge} alt="" />
                            </div>
                            : null
                        }
                    </div>
                ))}
            </div>
            <div className={styles.labels_wrapper}>
                <div className={styles.brand_block}>
                    <img className={styles.vrct_starting_up_img} src={logoBadge} alt="VRCNT-Next" />
                    <p className={styles.brand_name}>VRCNT-Next</p>
                    <p className={styles.vrct_explanation_img}>VRChat Next Translation</p>
                    <p className={styles.status_message}>{currentInitStatus.data.message}</p>
                    <p className={styles.status_detail}>{currentInitStatus.data.detail}</p>
                </div>
            </div>
        </div>
    );
};
