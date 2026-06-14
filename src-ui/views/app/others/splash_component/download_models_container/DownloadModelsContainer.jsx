import styles from "./DownloadModelsContainer.module.scss";
import logoBadge from "@images/vrcnt_logo_badge.png";

import {
    useTranslation,
    useTranscription,
} from "@logics_configs";

export const DownloadModelsContainer = () => {
    const { currentCTranslate2WeightTypeStatus } = useTranslation();
    const { currentWhisperWeightTypeStatus } = useTranscription();

    const downloadingCTranslate2 = currentCTranslate2WeightTypeStatus.data.filter(d => d.progress !== null);
    const downloadingWhisper = currentWhisperWeightTypeStatus.data.filter(d => d.progress !== null);

    if (downloadingCTranslate2.length === 0 && downloadingWhisper.length === 0) return null;

    return (
        <div className={styles.container}>
            <div className={styles.progress_container}>
                {downloadingCTranslate2.map((model) => (
                    <DownloadModelsProgress key={model.id} progress={model.progress} type_label={`Translation: ${model.id}`} />
                ))}
                {downloadingWhisper.map((model) => (
                    <DownloadModelsProgress key={model.id} progress={model.progress} type_label={`Transcription: ${model.id}`} />
                ))}
            </div>
            <div className={styles.labels_wrapper}>
                <div className={styles.logo_box}>
                    <img className={styles.logo_image} src={logoBadge} alt="VRCNT-Next" />
                    <p className={styles.logo_title}>VRCNT-Next</p>
                    <p className={styles.logo_caption}>Preparing local AI models</p>
                </div>
            </div>
        </div>
    );
};


const DownloadModelsProgress = (props) => {
    if (props.progress === null) return null;
    const circular_progress = Math.floor(props.progress / 5) * 5;

    const progress_color = generateGradientColor({
        value: circular_progress,
        colorStart: [242, 242, 242], // #f2f2f2
        colorEnd: [72, 164, 149], // #48a495
    });

    return(
        <div className={styles.progress_bar_container}>
            <div className={styles.progress_bar_wrapper}>
                <div
                    className={styles.progress_bar}
                    style={{
                        width: `${props.progress}%`,
                        backgroundColor: progress_color,
                    }}
                />
            </div>
            <p className={styles.progress_label}>{`${props.type_label}: ${Math.round(props.progress)}%`}</p>
        </div>
    );
};


const generateGradientColor = ({ value, colorStart, colorEnd }) => {
    const normalizedValue = Math.max(0, Math.min(100, value)) / 100;
    const interpolatedColor = colorStart.map((start, i) => {
        const end = colorEnd[i];
        return Math.round(start + (end - start) * normalizedValue);
    });
    const hexColor = `#${interpolatedColor.map(val => val.toString(16).padStart(2, '0')).join('')}`;
    return hexColor;
};
