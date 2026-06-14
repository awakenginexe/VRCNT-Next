import { useI18n } from "@useI18n";
import styles from "./TranslatorSelectorOpenButton.module.scss";
import { TranslatorSelector } from "./translator_selector/TranslatorSelector";
import { useStore_IsOpenedTranslatorSelector } from "@store";
import { useLanguageSettings } from "@logics_main";

export const TranslatorSelectorOpenButton = () => {
    const { t } = useI18n();
    const {
        currentSelectedPresetTabNumber,
        currentTranslationEngines,
        currentSelectedTranslationEngines,
    } = useLanguageSettings();

    // const new_labels = [
    //     {id: "CTranslate2", label: "AI\nCTranslate2"}
    // ];

    const translation_engines = currentTranslationEngines.data;
    // const translation_engines = updateLabelsById(currentTranslationEngines.data, new_labels);

    const selected_engine_value = currentSelectedTranslationEngines.data?.[currentSelectedPresetTabNumber.data];
    const selected_engine_ids = Array.isArray(selected_engine_value)
        ? selected_engine_value
        : [selected_engine_value].filter(Boolean);

    const is_selected_same_language = false;

    const getSelectedLabel = () => {
        return selected_engine_ids
            .map(engine_id => translation_engines.find(d => d.id === engine_id)?.label ?? engine_id)
            .join(" + ");
    };

    const is_loading = currentTranslationEngines.state === "pending";
    const selected_label = is_loading ? "Loading..." : getSelectedLabel();


    const { currentIsOpenedTranslatorSelector, updateIsOpenedTranslatorSelector} = useStore_IsOpenedTranslatorSelector();

    const openTranslatorSelector = () => {
        updateIsOpenedTranslatorSelector(!currentIsOpenedTranslatorSelector.data);
    };

    return (
        <div className={styles.container}>
            <div className={styles.translator_selector_button} onClick={openTranslatorSelector}>
                <p className={styles.label}>{t("main_page.translator")}:</p>
                <p className={styles.label}>{selected_label}</p>
            </div>
            {currentIsOpenedTranslatorSelector.data &&
                <TranslatorSelector
                    selected_ids={selected_engine_ids}
                    translation_engines={translation_engines}
                    is_selected_same_language={is_selected_same_language}
                />
            }
        </div>
    );
};
