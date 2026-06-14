import { useStore_SelectedPresetTabNumber, useStore_SelectedYourLanguages, useStore_SelectedYourTranslationLanguages, useStore_SelectedTargetLanguages, useStore_TranslationEngines, useStore_SelectedTranslationEngines, useStore_SelectableLanguageList } from "@store";
import { useStdoutToPython } from "@useStdoutToPython";
import { translator_status } from "@ui_configs";

export const useLanguageSettings = () => {
    const { asyncStdoutToPython } = useStdoutToPython();

    const {
        currentSelectedYourLanguages,
        updateSelectedYourLanguages,
        pendingSelectedYourLanguages,
    } = useStore_SelectedYourLanguages();
    const {
        currentSelectedYourTranslationLanguages,
        updateSelectedYourTranslationLanguages,
        pendingSelectedYourTranslationLanguages,
    } = useStore_SelectedYourTranslationLanguages();
    const {
        currentSelectedTargetLanguages,
        updateSelectedTargetLanguages,
        pendingSelectedTargetLanguages,
    } = useStore_SelectedTargetLanguages();
    const {
        currentSelectedPresetTabNumber,
        updateSelectedPresetTabNumber,
        pendingSelectedPresetTabNumber,
    } = useStore_SelectedPresetTabNumber();
    const {
        currentTranslationEngines,
        updateTranslationEngines,
        pendingTranslationEngines,
    } = useStore_TranslationEngines();
    const {
        currentSelectedTranslationEngines,
        updateSelectedTranslationEngines,
        pendingSelectedTranslationEngines,
    } = useStore_SelectedTranslationEngines();

    const {
        currentSelectableLanguageList,
        updateSelectableLanguageList,
    } = useStore_SelectableLanguageList();

    const getPresetKey = () => currentSelectedPresetTabNumber.data ?? "1";

    const createFallbackYourLanguages = () => ({
        1: { language: "", country: "", enable: true },
        2: { language: "English", country: "United States", enable: false },
        3: { language: "Chinese Simplified", country: "China", enable: false },
    });

    const createFallbackTargetLanguages = () => ({
        1: { language: "", country: "", enable: true },
        2: { language: "", country: "", enable: false },
        3: { language: "", country: "", enable: false },
    });

    const getCurrentYourLanguages = () => {
        const presetKey = getPresetKey();
        return {
            ...createFallbackYourLanguages(),
            ...(currentSelectedYourLanguages.data?.[presetKey] ?? {}),
        };
    };

    const getCurrentTargetLanguages = () => {
        const presetKey = getPresetKey();
        return {
            ...createFallbackTargetLanguages(),
            ...(currentSelectedTargetLanguages.data?.[presetKey] ?? {}),
        };
    };


    const getSelectedPresetTabNumber = () => {
        pendingSelectedPresetTabNumber();
        asyncStdoutToPython("/get/data/selected_tab_no");
    };

    const setSelectedPresetTabNumber = (preset_number) => {
        pendingSelectedPresetTabNumber();

        asyncStdoutToPython("/set/data/selected_tab_no", preset_number);
    };


    const getSelectedYourLanguages = () => {
        pendingSelectedPresetTabNumber();
        asyncStdoutToPython("/get/data/selected_your_languages");
    };

    const setSelectedYourLanguages = (selected_language_data) => {
        pendingSelectedYourLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedYourLanguages.data ?? {});
        send_obj[presetKey] = {
            ...createFallbackYourLanguages(),
            ...(send_obj[presetKey] ?? {}),
        };
        const targetKey = selected_language_data.target_key ?? "1";
        send_obj[presetKey][targetKey] ??= { language: "", country: "", enable: true };
        send_obj[presetKey][targetKey].language = selected_language_data.language;
        send_obj[presetKey][targetKey].country = selected_language_data.country;
        send_obj[presetKey][targetKey].enable = true;
        asyncStdoutToPython("/set/data/selected_your_languages", send_obj);
    };

    const addYourLanguage = () => {
        pendingSelectedYourLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedYourLanguages.data ?? {});
        send_obj[presetKey] = {
            ...createFallbackYourLanguages(),
            ...(send_obj[presetKey] ?? {}),
        };
        let target_key = "2";
        if (send_obj[presetKey]["2"].enable === true) {
            target_key = "3";
        }
        if (!send_obj[presetKey][target_key].language) {
            send_obj[presetKey][target_key].language = target_key === "2" ? "English" : "Chinese Simplified";
            send_obj[presetKey][target_key].country = target_key === "2" ? "United States" : "China";
        }
        send_obj[presetKey][target_key].enable = true;
        asyncStdoutToPython("/set/data/selected_your_languages", send_obj);
    };

    const removeYourLanguage = () => {
        pendingSelectedYourLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedYourLanguages.data ?? {});
        send_obj[presetKey] = {
            ...createFallbackYourLanguages(),
            ...(send_obj[presetKey] ?? {}),
        };
        let target_key = "3";
        if (send_obj[presetKey]["3"].enable === false) {
            target_key = "2";
        }
        send_obj[presetKey][target_key].enable = false;
        asyncStdoutToPython("/set/data/selected_your_languages", send_obj);
    };

    const getSelectedYourTranslationLanguages = () => {
        pendingSelectedYourTranslationLanguages();
        asyncStdoutToPython("/get/data/selected_your_translation_languages");
    };

    const setSelectedYourTranslationLanguages = (selected_language_data) => {
        pendingSelectedYourTranslationLanguages();
        const send_obj = {
            ...currentSelectedYourTranslationLanguages.data,
            [currentSelectedPresetTabNumber.data]: {
                1: {
                    language: selected_language_data.language,
                    country: selected_language_data.country,
                    enable: true,
                }
            }
        };
        asyncStdoutToPython("/set/data/selected_your_translation_languages", send_obj);
    };


    const getSelectedTargetLanguages = () => {
        pendingSelectedTargetLanguages();
        asyncStdoutToPython("/get/data/selected_target_languages");
    };

    const setSelectedTargetLanguages = (selected_language_data) => {
        pendingSelectedTargetLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedTargetLanguages.data ?? {});
        send_obj[presetKey] ??= createFallbackTargetLanguages();
        send_obj[presetKey][selected_language_data.target_key] ??= { language: "", country: "", enable: true };
        send_obj[presetKey][selected_language_data.target_key].language = selected_language_data.language,
        send_obj[presetKey][selected_language_data.target_key].country = selected_language_data.country,
        asyncStdoutToPython("/set/data/selected_target_languages", send_obj);
    };

    const addTargetLanguage = () => {
        pendingSelectedTargetLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedTargetLanguages.data ?? {});
        send_obj[presetKey] ??= createFallbackTargetLanguages();
        let target_key = "2";
        if (send_obj[presetKey]["2"].enable === true) {
            target_key = "3";
        }
        send_obj[presetKey][target_key].enable = true,
        asyncStdoutToPython("/set/data/selected_target_languages", send_obj);
    };
    const removeTargetLanguage = () => {
        pendingSelectedTargetLanguages();
        const presetKey = getPresetKey();
        const send_obj = structuredClone(currentSelectedTargetLanguages.data ?? {});
        send_obj[presetKey] ??= createFallbackTargetLanguages();
        let target_key = "3";
        if (send_obj[presetKey]["3"].enable === false) {
            target_key = "2";
        }
        send_obj[presetKey][target_key].enable = false,
        asyncStdoutToPython("/set/data/selected_target_languages", send_obj);
    };


    const getTranslationEngines = () => {
        pendingTranslationEngines();
        asyncStdoutToPython("/get/data/selectable_translation_engines");
    };

    const updateTranslatorAvailability = (payload) => {
        const keys = payload;
        const updated_list = translator_status.map(translator => ({
            ...translator,
            is_available: keys.includes(translator.id),
        }));
        updateTranslationEngines(updated_list);
    };


    const getSelectedTranslationEngines = () => {
        pendingSelectedTranslationEngines();
        asyncStdoutToPython("/get/data/selected_translation_engines");
    };

    const setSelectedTranslationEngines = (selected_translator) => {
        pendingSelectedTranslationEngines();
        const send_obj = structuredClone(currentSelectedTranslationEngines.data ?? {});
        send_obj[getPresetKey()] = selected_translator;
        asyncStdoutToPython("/set/data/selected_translation_engines", send_obj);
    };

    const swapSelectedLanguages = () => {
        pendingSelectedYourLanguages();
        pendingSelectedYourTranslationLanguages();
        pendingSelectedTargetLanguages();
        asyncStdoutToPython("/run/swap_your_language_and_target_language");
    };

    const updateBothSelectedLanguages = (payload) => {
        updateSelectedYourLanguages(payload.your);
        if (payload.your_translation) updateSelectedYourTranslationLanguages(payload.your_translation);
        updateSelectedTargetLanguages(payload.target);
    };


    const getSelectableLanguageList = () => {
        asyncStdoutToPython("/get/data/selectable_language_list");
    };


    return {
        currentSelectedPresetTabNumber,
        getSelectedPresetTabNumber,
        updateSelectedPresetTabNumber,
        setSelectedPresetTabNumber,

        currentSelectedYourLanguages,
        getSelectedYourLanguages,
        updateSelectedYourLanguages,
        setSelectedYourLanguages,
        getCurrentYourLanguages,
        addYourLanguage,
        removeYourLanguage,

        currentSelectedYourTranslationLanguages,
        getSelectedYourTranslationLanguages,
        updateSelectedYourTranslationLanguages,
        setSelectedYourTranslationLanguages,

        currentSelectedTargetLanguages,
        getSelectedTargetLanguages,
        updateSelectedTargetLanguages,
        setSelectedTargetLanguages,
        getCurrentTargetLanguages,

        addTargetLanguage,
        removeTargetLanguage,

        currentTranslationEngines,
        getTranslationEngines,
        updateTranslationEngines,
        updateTranslatorAvailability,

        currentSelectedTranslationEngines,
        getSelectedTranslationEngines,
        updateSelectedTranslationEngines,
        setSelectedTranslationEngines,

        swapSelectedLanguages,
        updateBothSelectedLanguages,

        currentSelectableLanguageList,
        getSelectableLanguageList,
        updateSelectableLanguageList,
    };
};
