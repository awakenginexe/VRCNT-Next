import { useWindow } from "@logics_common";
import clsx from "clsx";
import styles from "./WindowTitleBar.module.scss";
import XMarkSvg from "@images/cancel.svg?react";
import SquareSvg from "@images/square.svg?react";
import LineSvg from "@images/line.svg?react";
import PerformanceIcon from "@images/mui_discover_tune.svg?react";
import logoBadge from "@images/vrcnt_logo_badge.png";
import { useStore_EnablePerformanceMode } from "@store";

export const WindowTitleBar = () => {
    const { asyncCloseApp, asyncToggleMaximizeApp, asyncMinimizeApp } = useWindow();
    const { currentEnablePerformanceMode, updateEnablePerformanceMode } = useStore_EnablePerformanceMode();

    const togglePerformanceMode = () => {
        const nextVal = !currentEnablePerformanceMode.data;
        updateEnablePerformanceMode(nextVal);
        localStorage.setItem("enable_performance_mode", nextVal ? "true" : "false");
    };

    return (
        <div className={styles.container}>
            <div className={styles.wrapper} data-tauri-drag-region>
                <div className={styles.title_wrapper}>
                    <img className={styles.title_logo} src={logoBadge} alt="" />
                    <p className={styles.title_text}>VRCNT-Next</p>
                    <p className={styles.title_subtitle}>Next Gen VRChat Translation</p>
                </div>

                <div className={styles.window_control_wrapper}>
                    <div
                        className={clsx(styles.performance_button, {
                            [styles.is_active]: currentEnablePerformanceMode.data,
                        })}
                        onClick={togglePerformanceMode}
                        title="Toggle Performance Mode (disables blurs/animations to save CPU/GPU)"
                    >
                        <PerformanceIcon className={styles.performance_svg} />
                    </div>
                    <div className={styles.minimize_button} onClick={asyncMinimizeApp}>
                        <LineSvg className={styles.line_svg}/>
                    </div>
                    <div className={styles.maximize_button} onClick={asyncToggleMaximizeApp}>
                        <SquareSvg className={styles.square_svg}/>
                    </div>
                    <div className={styles.close_button} onClick={asyncCloseApp}>
                        <XMarkSvg className={styles.x_mark_svg}/>
                    </div>
                </div>
            </div>
        </div>
    );
};
