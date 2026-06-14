import { useI18n } from "@useI18n";
import styles from "./LanguageSettings.module.scss";
import { PresetTabSelector } from "./preset_tab_selector/PresetTabSelector";
import { LanguageSelectorOpenButton } from "./language_selector_open_button/LanguageSelectorOpenButton";
import { LanguageSwapButton } from "./language_swap_button/LanguageSwapButton";
import { TranslatorSelectorOpenButton } from "./translator_selector_open_button/TranslatorSelectorOpenButton";
import { TranscriptionEngineLabel } from "./transcription_engine_label/TranscriptionEngineLabel";
import { AddRemoveTargetLanguageButtons } from "./add_remove_target_language_buttons/AddRemoveTargetLanguageButtons";
import { AddRemoveYourLanguageButtons } from "./add_remove_your_language_buttons/AddRemoveYourLanguageButtons";
import { useStore_IsOpenedTranslatorSelector, useStore_IsOpenedTranscriptionEngineSelector } from "@store";

export const LanguageSettings = () => {
    const { t } = useI18n();
    const { updateIsOpenedTranslatorSelector } = useStore_IsOpenedTranslatorSelector();
    const { updateIsOpenedTranscriptionEngineSelector } = useStore_IsOpenedTranscriptionEngineSelector();
    const closeSelectors = () => {
        updateIsOpenedTranslatorSelector(false);
        updateIsOpenedTranscriptionEngineSelector(false);
    };

    return (
        <div className={styles.container} onMouseLeave={closeSelectors}>
            <p className={styles.title}>{t("main_page.language_settings")}</p>
            <PresetTabSelector />
            <PresetContainer />
        </div>
    );
};

import MicSvg from "@images/mic.svg?react";
import HeadphonesSvg from "@images/headphones.svg?react";
import { useMainFunction } from "@logics_main";
import { useTranscription } from "@logics_configs";

const PresetContainer = () => {
    const { t } = useI18n();
    const { currentTranscriptionSendStatus, currentTranscriptionReceiveStatus } = useMainFunction();
    const { currentSelectedTranscriptionEngine } = useTranscription();
    const transcriptionEngine = currentSelectedTranscriptionEngine?.data;
    const supportsMultipleSpeakingLanguages = transcriptionEngine === "Whisper" || transcriptionEngine === "SenseVoice";

    const yourLanguageSettings = {
        TurnedOnSvgComponent: MicSvg,
        is_turned_on: currentTranscriptionSendStatus.data,
    };

    const yourTranslationLanguageSettings = {
        TurnedOnSvgComponent: HeadphonesSvg,
        is_turned_on: currentTranscriptionReceiveStatus.data,
    };

    const targetLanguageSettings = {
        TurnedOnSvgComponent: HeadphonesSvg,
        is_turned_on: currentTranscriptionReceiveStatus.data,
    };

    return (
        <div className={styles.preset_container}>
            <div className={styles.language_panel}>
                <div className={styles.section_header}>
                    <p className={styles.section_title}>You</p>
                    <p className={styles.section_hint}>Voice input and personal translation output</p>
                </div>
                <div className={styles.selector_stack}>
                    <LanguageSelectorOpenButton {...yourLanguageSettings} selector_key="your_language" target_key="1"/>
                    {supportsMultipleSpeakingLanguages && (
                        <>
                            <LanguageSelectorOpenButton {...yourLanguageSettings} selector_key="your_language" target_key="2"/>
                            <LanguageSelectorOpenButton {...yourLanguageSettings} selector_key="your_language" target_key="3"/>
                            <AddRemoveYourLanguageButtons />
                        </>
                    )}
                    <LanguageSelectorOpenButton {...yourTranslationLanguageSettings} selector_key="your_translation_language" target_key="1"/>
                </div>
            </div>

            <LanguageSwapButton />

            <div className={styles.language_panel}>
                <div className={styles.section_header}>
                    <p className={styles.section_title}>Targets</p>
                    <p className={styles.section_hint}>Choose who you want VRCNT-Next to translate for</p>
                </div>
                <div className={styles.target_language_containers}>
                    <LanguageSelectorOpenButton {...targetLanguageSettings} selector_key="target_language" target_key="1" />
                    <LanguageSelectorOpenButton {...targetLanguageSettings} selector_key="target_language" target_key="2" />
                    <LanguageSelectorOpenButton {...targetLanguageSettings} selector_key="target_language" target_key="3" />
                </div>
                <AddRemoveTargetLanguageButtons />
            </div>

            <div className={styles.engine_panel}>
                <div className={styles.section_header}>
                    <p className={styles.section_title}>Engines</p>
                    <p className={styles.section_hint}>Quick switches for translation and transcription</p>
                </div>
                <div className={styles.engine_controls}>
                    <TranslatorSelectorOpenButton />
                    <TranscriptionEngineLabel />
                </div>
            </div>
        </div>
    );
};
