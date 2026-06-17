import clsx from "clsx";
import { useI18n } from "@useI18n";

import { useLanguageSettings } from "@logics_main";
import { useTranscription } from "@logics_configs";
import styles from "./LanguageSelector.module.scss";
import {
    getLanguageDisplayLabel,
} from "../../sidebar_section/language_settings/languageDisplayUtils.js";
import { LanguageFlag } from "../../sidebar_section/language_settings/LanguageFlag.jsx";

import { LanguageSelectorTopBar } from "./language_selector_top_bar/LanguageSelectorTopBar";

const LANGUAGE_CODES = {
    "Arabic": "ar",
    "Bulgarian": "bg",
    "Catalan": "ca",
    "Chinese Simplified": "zh",
    "Chinese Traditional": "zh",
    "Croatian": "hr",
    "Czech": "cs",
    "Danish": "da",
    "Dutch": "nl",
    "English": "en",
    "Estonian": "et",
    "Filipino": "tl",
    "Finnish": "fi",
    "French": "fr",
    "Georgian": "ka",
    "German": "de",
    "Greek": "el",
    "Gujarati": "gu",
    "Hebrew": "he",
    "Hindi": "hi",
    "Hungarian": "hu",
    "Italian": "it",
    "Japanese": "ja",
    "Kazakh": "kk",
    "Korean": "ko",
    "Latvian": "lv",
    "Lithuanian": "lt",
    "Norwegian": "nb",
    "Persian": "fa",
    "Polish": "pl",
    "Portuguese": "pt",
    "Romanian": "ro",
    "Russian": "ru",
    "Slovak": "sk",
    "Slovenian": "sl",
    "Spanish": "es",
    "Swedish": "sv",
    "Telugu": "te",
    "Thai": "th",
    "Turkish": "tr",
    "Ukrainian": "uk",
    "Uzbek": "uz",
    "Vietnamese": "vi",
};

const VOSK_MODEL_LANGUAGES = {
    "small-en": ["en"],
    "large-en": ["en"],
    "small-ja": ["ja"],
    "small-zh": ["zh"],
    "small-ko": ["ko"],
    "small-fr": ["fr"],
    "small-en-in": ["en"],
    "small-de": ["de"],
    "small-es": ["es"],
    "small-pt": ["pt"],
    "small-ru": ["ru"],
    "small-tr": ["tr"],
    "small-vn": ["vi"],
    "small-it": ["it"],
    "small-nl": ["nl"],
    "small-ca": ["ca"],
    "ar-mgb2": ["ar"],
    "el-gr": ["el"],
    "small-fa": ["fa"],
    "tl-ph-generic": ["tl"],
    "small-uk": ["uk"],
    "small-kz": ["kk"],
    "small-sv": ["sv"],
    "small-eo": ["eo"],
    "small-hi": ["hi"],
    "small-cs": ["cs"],
    "small-pl": ["pl"],
    "small-uz": ["uz"],
    "br": ["br"],
    "small-gu": ["gu"],
    "small-tg": ["tg"],
    "small-te": ["te"],
    "small-ky": ["ky"],
    "small-ka": ["ka"],
};

const PARAKEET_MODEL_LANGUAGES = {
    "parakeet-tdt-0.6b-v3": [
        "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
        "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
    ],
    "parakeet-tdt-0.6b": ["en"],
    "parakeet-tdt-ctc-0.6b": ["ja"],
    "parakeet-tdt-1.1b": ["en"],
    "canary-1b": ["en", "de", "es", "fr"],
};

const SENSEVOICE_MODEL_LANGUAGES = {
    "sensevoice-small-int8": ["zh", "yue", "en", "ja", "ko"],
    "sensevoice-small-fp32": ["zh", "yue", "en", "ja", "ko"],
};

const getLanguageCode = ({ language, country }, engine) => {
    if (engine === "Vosk" && language === "Chinese Traditional" && country === "Hong Kong") return "";
    if (engine === "SenseVoice") {
        if (language === "Chinese Simplified") return "zh";
        if (language === "Chinese Traditional") return country === "Hong Kong" ? "yue" : "zh";
        if (language === "English") return "en";
        if (language === "Japanese") return "ja";
        if (language === "Korean") return "ko";
        return "";
    }
    return LANGUAGE_CODES[language] ?? "";
};

const buildSupportGuard = ({ selectorType, engine, voskWeightType, parakeetWeightType, sensevoiceWeightType }) => {
    const isEngineLimited = engine === "Vosk" || engine === "Parakeet" || engine === "SenseVoice";
    const shouldRestrict = isEngineLimited && (
        selectorType === "your_language" ||
        selectorType === "target_language"
    );

    if (shouldRestrict === false) {
        return { isActive: false, engine, isSupported: () => true };
    }

    const supportedCodes = new Set(
        engine === "Vosk" ? (VOSK_MODEL_LANGUAGES[voskWeightType] ?? []) :
        engine === "SenseVoice" ? (SENSEVOICE_MODEL_LANGUAGES[sensevoiceWeightType] ?? []) :
        (PARAKEET_MODEL_LANGUAGES[parakeetWeightType] ?? [])
    );

    return {
        isActive: true,
        engine,
        isSupported: (languageData) => {
            const languageCode = getLanguageCode(languageData, engine);
            return languageCode !== "" && supportedCodes.has(languageCode);
        },
    };
};

export const LanguageSelector = ({ title, onClickFunction, selectorType }) => {
    const { t } = useI18n();
    const { currentSelectableLanguageList } = useLanguageSettings();
    const {
        currentSelectedTranscriptionEngine,
        currentSelectedVoskWeightType,
        currentSelectedParakeetWeightType,
        currentSelectedSenseVoiceWeightType,
    } = useTranscription();

    const supportGuard = buildSupportGuard({
        selectorType,
        engine: currentSelectedTranscriptionEngine?.data,
        voskWeightType: currentSelectedVoskWeightType?.data,
        parakeetWeightType: currentSelectedParakeetWeightType?.data,
        sensevoiceWeightType: currentSelectedSenseVoiceWeightType?.data,
    });

    const groupLanguagesByFirstLetter = (languages) => {
        return languages.reduce((acc, { language, country }) => {
            const firstLetter = language[0].toUpperCase();
            if (!acc[firstLetter]) {
                acc[firstLetter] = [];
            }
            acc[firstLetter].push({ language, country });
            return acc;
        }, {});
    };

    const groupedLanguages = groupLanguagesByFirstLetter(currentSelectableLanguageList.data);

    return (
        <div className={styles.container}>
            <LanguageSelectorTopBar title={title}/>
            {supportGuard.isActive && (
                <p className={styles.language_support_hint}>
                    {t("main_page.language_selector.model_support_hint", { engine: supportGuard.engine })}
                </p>
            )}
            <div className={styles.language_list_scroll_wrapper}>
                <div className={styles.language_list}>
                    {Object.entries(groupedLanguages).map(([letter, languages]) => (
                        <LanguageGroup
                            key={letter}
                            onClickFunction={onClickFunction}
                            letter={letter}
                            languages={languages}
                            supportGuard={supportGuard}
                            t={t}
                        />
                    ))}
                </div>
            </div>
        </div>
    );
};

const LanguageGroup = ({ onClickFunction, letter, languages, supportGuard, t }) => {
    return (
        <div className={styles.language_each_letter_box}>
            <div className={styles.language_letter_header}>
                <p className={styles.language_latter}>{letter}</p>
                <div className={styles.language_letter_divider}></div>
            </div>
            {languages.map((language_data, index) => (
                <LanguageButton
                    key={index}
                    onClickFunction={onClickFunction}
                    language_data={language_data}
                    isDisabled={supportGuard.isSupported(language_data) === false}
                    disabledReason={t("main_page.language_selector.unsupported_by_model", { engine: supportGuard.engine })}
                />
            ))}
        </div>
    );
};

const LanguageButton = ({ onClickFunction, language_data, isDisabled, disabledReason }) => {

    const adjustedOnClickFunction = () => {
        if (isDisabled === true) return;
        onClickFunction({
            language: language_data.language,
            country: language_data.country,
        });
    };

    const languageButtonClass = clsx(styles.language_button, {
        [styles.is_disabled]: isDisabled === true,
    });

    return (
        <div
            className={languageButtonClass}
            onClick={adjustedOnClickFunction}
            aria-disabled={isDisabled === true}
            title={isDisabled === true ? disabledReason : undefined}
        >
            <div className={styles.language_identity}>
                <LanguageFlag country={language_data.country} className={styles.language_flag} />
                <div className={styles.language_copy}>
                    <p className={styles.language_label}>{language_data.language}</p>
                    <p className={styles.country_label}>{language_data.country}</p>
                </div>
            </div>
            <p className={styles.language_chip}>{getLanguageDisplayLabel(language_data)}</p>
        </div>
    );
};
