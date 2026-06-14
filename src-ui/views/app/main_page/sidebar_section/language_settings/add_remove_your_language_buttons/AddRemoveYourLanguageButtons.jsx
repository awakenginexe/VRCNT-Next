import clsx from "clsx";
import styles from "./AddRemoveYourLanguageButtons.module.scss";
import RemoveSvg from "@images/remove.svg?react";
import AddSvg from "@images/add.svg?react";

import { useLanguageSettings } from "@logics_main";
import { useTranscription } from "@logics_configs";

export const AddRemoveYourLanguageButtons = () => {
    const {
        getCurrentYourLanguages,
        removeYourLanguage,
        addYourLanguage,
    } = useLanguageSettings();
    const { currentSelectedTranscriptionEngine } = useTranscription();

    const engine = currentSelectedTranscriptionEngine?.data;
    if (engine !== "Whisper" && engine !== "SenseVoice") return null;

    const yourLanguages = getCurrentYourLanguages();
    const secondLanguageEnabled = yourLanguages?.["2"]?.enable === true;
    const thirdLanguageEnabled = yourLanguages?.["3"]?.enable === true;

    const remove_button_class = clsx(styles.remove_your_language_button, {
        [styles.is_disabled]: !secondLanguageEnabled,
    });
    const add_button_class = clsx(styles.add_your_language_button, {
        [styles.is_disabled]: thirdLanguageEnabled,
    });

    return (
        <div className={styles.add_remove_your_language_container}>
            <div className={remove_button_class} onClick={secondLanguageEnabled ? removeYourLanguage : undefined}>
                <RemoveSvg className={styles.remove_svg} />
            </div>
            <div className={add_button_class} onClick={thirdLanguageEnabled ? undefined : addYourLanguage}>
                <AddSvg className={styles.add_svg} />
            </div>
        </div>
    );
};
