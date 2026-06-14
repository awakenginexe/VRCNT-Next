import clsx from "clsx";
import styles from "./AddRemoveTargetLanguageButtons.module.scss";
import RemoveSvg from "@images/remove.svg?react";
import AddSvg from "@images/add.svg?react";

import { useLanguageSettings } from "@logics_main";

export const AddRemoveTargetLanguageButtons = () => {
    const {
        getCurrentTargetLanguages,
        removeTargetLanguage,
        addTargetLanguage,
    } = useLanguageSettings();

    const targetLanguages = getCurrentTargetLanguages();
    const secondTargetEnabled = targetLanguages?.["2"]?.enable === true;
    const thirdTargetEnabled = targetLanguages?.["3"]?.enable === true;

    const remove_button_class = clsx(styles.remove_target_language_button, {
        [styles.is_disabled]: !secondTargetEnabled,
    });
    const add_button_class = clsx(styles.add_target_language_button, {
        [styles.is_disabled]: thirdTargetEnabled,
    });

    return (
        <div className={styles.add_remove_target_language_container}>
            <div className={remove_button_class} onClick={secondTargetEnabled ? removeTargetLanguage : undefined}>
                <RemoveSvg className={styles.remove_svg} />
            </div>
            <div className={add_button_class} onClick={thirdTargetEnabled ? undefined : addTargetLanguage}>
                <AddSvg className={styles.add_svg} />
            </div>
        </div>
    );
};
