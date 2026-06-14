import clsx from "clsx";
import styles from "./TranscriptionEngineSelector.module.scss";
import { chunkArray } from "@utils";
import { useStore_IsOpenedTranscriptionEngineSelector } from "@store";
import { useTranscription } from "@logics_configs";

export const TranscriptionEngineSelector = ({ selected_id }) => {
    const engines = [
        { id: "Google", label: "Google\n(Cloud)", is_available: true },
        { id: "Whisper", label: "Whisper\n(CPU/GPU)", is_available: true },
        { id: "Parakeet", label: "Parakeet\n(GPU)", is_available: true },
        { id: "Vosk", label: "Vosk\n(CPU)", is_available: true },
        { id: "SenseVoice", label: "SenseVoice\n(CPU)", is_available: true },
    ];

    const columns = chunkArray(engines, 2);

    return (
        <div className={styles.container}>
            <div className={styles.relative_container}>
                <div className={styles.wrapper}>
                    {columns.map((column, column_index) => (
                        <div className={styles.column_wrapper} key={`column_${column_index}`}>
                            {column.map(({ id, label, is_available }) => (
                                <EngineBox
                                    key={id}
                                    id={id}
                                    label={label}
                                    is_available={is_available}
                                    is_selected={id === selected_id}
                                />
                            ))}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

const EngineBox = (props) => {
    const { setSelectedTranscriptionEngine } = useTranscription();
    const { updateIsOpenedTranscriptionEngineSelector } = useStore_IsOpenedTranscriptionEngineSelector();

    const box_class_name = clsx(
        styles.box,
        { [styles.is_selected]: props.is_selected },
        { [styles.is_available]: props.is_available }
    );

    const selectEngine = () => {
        if (props.is_selected === false) {
            setSelectedTranscriptionEngine(props.id);
        }
        updateIsOpenedTranscriptionEngineSelector(false);
    };

    return (
        <div className={box_class_name} onClick={selectEngine}>
            <p className={styles.engine_name}>{props.label}</p>
        </div>
    );
};
