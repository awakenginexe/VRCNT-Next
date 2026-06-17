import { useI18n } from "@useI18n";
import clsx from "clsx";
import styles from "./LanguageSelectorOpenButton.module.scss";
import ArrowLeftSvg from "@images/arrow_left.svg?react";
import { useStore_IsOpenedLanguageSelector } from "@store";
import {
    useLanguageSettings,
} from "@logics_main";
import { LanguageFlag } from "../LanguageFlag.jsx";

export const LanguageSelectorOpenButton = ({ TurnedOnSvgComponent, is_turned_on, selector_key, target_key }) => {
    const { t } = useI18n();
    const { updateIsOpenedLanguageSelector, currentIsOpenedLanguageSelector } = useStore_IsOpenedLanguageSelector();

    const {
        currentSelectedPresetTabNumber,
        currentSelectedYourLanguages,
        currentSelectedYourTranslationLanguages,
        currentSelectedTargetLanguages,
        getCurrentYourLanguages,
        getCurrentTargetLanguages,
    } = useLanguageSettings();

    const toggleSelector = () => {
        if (currentIsOpenedLanguageSelector.data[selector_key] === true && currentIsOpenedLanguageSelector.data.target_key === target_key) { // Close Language Selector
            updateIsOpenedLanguageSelector({ your_language: false, your_translation_language: false, target_language: false, target_key: "1" });
        } else { // Open Language Selector
            updateIsOpenedLanguageSelector({
                your_language: selector_key === "your_language",
                your_translation_language: selector_key === "your_translation_language",
                target_language: selector_key === "target_language",
                target_key: target_key,
            });
        }
    };

    const arrow_class_names = clsx(styles.arrow_left_svg, {
        [styles.reverse]: (currentIsOpenedLanguageSelector.data[selector_key] === true && currentIsOpenedLanguageSelector.data.target_key === target_key),
    });

    const category_class_names = clsx(styles.category_svg, {
        [styles.is_turned_on]: is_turned_on,
    });

    const getVariable = (target_selector_key) => {
        const presetKey = currentSelectedPresetTabNumber.data ?? "1";
        if (target_selector_key === "your_language") return {
            ...getCurrentYourLanguages(),
            ...(currentSelectedYourLanguages.data?.[presetKey] ?? {}),
        };
        if (target_selector_key === "your_translation_language") return currentSelectedYourTranslationLanguages.data?.[presetKey] ?? {};
        if (target_selector_key === "target_language") return currentSelectedTargetLanguages.data?.[presetKey] ?? getCurrentTargetLanguages();
        return {};
    };

    const getTitle = (target_selector_key) => {
        if (target_selector_key === "your_language") {
            return target_key === "1"
                ? t("main_page.language_panels.your_speaking_language")
                : t("main_page.language_panels.your_speaking_language_indexed", { index: target_key });
        }
        if (target_selector_key === "your_translation_language") return t("main_page.language_panels.your_translation_language");
        if (target_selector_key === "target_language") {
            const targetLanguages = getCurrentTargetLanguages();
            if (targetLanguages?.["2"]?.enable === false) return t("main_page.target_language");
            return `${t("main_page.target_language")} ${target_key}`;
        }
    };

    const title = getTitle(selector_key);
    const selectedGroup = getVariable(selector_key);
    const selectedEntry = selectedGroup?.[target_key];

    if (selectedEntry?.enable === false) return null;

    const language_text = selectedEntry?.language ?? t("main_page.language_panels.loading");
    const country_text = selectedEntry?.country ?? t("main_page.language_panels.loading");

    return (
        <div className={styles.container}>
            <div className={styles.title_container}>
                <TurnedOnSvgComponent className={category_class_names} />
                <p className={styles.title}>{title}</p>
            </div>
            <div className={styles.dropdown_menu_container} onClick={toggleSelector}>
                <div className={styles.language_details}>
                    <LanguageFlag country={country_text} className={styles.flag_badge} />
                    <div className={styles.language_copy}>
                        <p className={styles.selected_language}>{language_text}</p>
                        <p className={styles.selected_country}>{country_text}</p>
                    </div>
                </div>
                <ArrowLeftSvg className={arrow_class_names} />
            </div>
        </div>
    );
};
