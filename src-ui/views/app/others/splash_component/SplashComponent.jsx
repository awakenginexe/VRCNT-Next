import styles from "./SplashComponent.module.scss";
import { StartUpProgressContainer } from "./start_up_progress_container/StartUpProgressContainer";
import { DownloadModelsContainer } from "./download_models_container/DownloadModelsContainer";
import { useWindow } from "@logics_common";
import { CloseButton } from "@common_components";

export const SplashComponent = () => {
    const { asyncCloseApp } = useWindow();
    return (
        <div className={styles.container}>
            <StartUpProgressContainer />
            <DownloadModelsContainer />
            <CloseButton onClick={asyncCloseApp} />
        </div>
    );
};
