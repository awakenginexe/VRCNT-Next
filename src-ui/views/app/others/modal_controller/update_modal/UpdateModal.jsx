import clsx from "clsx";
import styles from "./UpdateModal.module.scss";
import { useI18n } from "@useI18n";
import { useStore_OpenedQuickSetting } from "@store";
import {
    useUpdateSoftware,
    useSoftwareVersion,
} from "@logics_common";

export const UpdateModal = () => {
    const { t } = useI18n();
    const { updateOpenedQuickSetting } = useStore_OpenedQuickSetting();
    const { updateSoftware, updateState } = useUpdateSoftware();
    const { currentLatestSoftwareVersionInfo } = useSoftwareVersion();

    const is_latest_version_already = currentLatestSoftwareVersionInfo.data.is_update_available === false;
    const is_updating = ["opening"].includes(updateState.status);
    const progress_percent = Math.round((updateState.progress ?? 0) * 100);

    const onClickUpdateSoftware = () => {
        updateSoftware();
    }

    const accept_button_class_name = clsx(styles.accept_button, {
        [styles.is_latest_version_already]: is_latest_version_already,
    })

    return (
        <div className={styles.container}>
            <div className={styles.wrapper}>
                <div className={styles.update_section_wrapper}>
                    <div className={styles.update_section}>
                        <div className={styles.single_update_section}>
                            <button
                                className={accept_button_class_name}
                                onClick={onClickUpdateSoftware}
                                disabled={is_latest_version_already || is_updating}
                            >
                                {is_updating ? "Opening..." : "Open Releases"}
                            </button>
                            <CurrentVersionLabel is_latest_version_already={is_latest_version_already} />
                            {!is_latest_version_already && (
                                <p className={styles.current_version_label}>New version {currentLatestSoftwareVersionInfo.data.new_version} is available</p>
                            )}
                            {is_updating && (
                                <div className={styles.progress_wrapper}>
                                    <div className={styles.progress_bar}>
                                        <div
                                            className={styles.progress_fill}
                                            style={{ width: `${progress_percent}%` }}
                                        />
                                    </div>
                                    <p className={styles.current_version_label}>{updateState.message} {progress_percent > 0 ? `${progress_percent}%` : ""}</p>
                                </div>
                            )}
                            <p className={styles.version_desc}>{t("update_modal.cuda_desc")}</p>
                        </div>

                        <p className={styles.update_desc}>Open GitHub Releases to download the latest installer.</p>
                    </div>
                </div>

                <div className={styles.button_wrapper}>
                    <button className={styles.deny_button} onClick={() => updateOpenedQuickSetting("")} >{t("update_modal.close_modal")}</button>
                </div>
            </div>
        </div>
    );
};

const CurrentVersionLabel = (props) => {
    const { t } = useI18n();

    if (props.is_latest_version_already) {
        return <p className={styles.current_version_label}>{t("update_modal.is_latest_version_already")}</p>;
    }
    return null;
};
