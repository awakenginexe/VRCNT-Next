import clsx from "clsx";
import { useEffect, useState } from "react";
import { useI18n } from "@useI18n";
import styles from "./Appearance.module.scss";
import { ui_configs } from "@ui_configs";
import { useStore_SelectableFontFamilyList, useStore_EnablePerformanceMode } from "@store";

import {
    useWindow,
} from "@logics_common";

import {
    useAppearance,
} from "@logics_configs";

import {
    SliderContainer,
    DropdownMenuContainer,
    RadioButtonContainer,
    CheckboxContainer,
} from "../_templates/Templates";

const THEME_ACCENTS = {
    "theme-neon-cyan": "Neon Cyan",
    "theme-midnight-purple": "Midnight Purple",
    "theme-emerald-green": "Emerald Green",
    "theme-sakura-pink": "Sakura Pink",
};

const THEME_ACCENT_CLASSES = Object.keys(THEME_ACCENTS);

export const Appearance = () => {
    return (
        <>
            <UiLanguageContainer />
            <ThemeAccentContainer />
            <UiScalingContainer />
            <MessageLogUiScalingContainer />
            <SendMessageButtonTypeContainer />
            <ShowResendButtonContainer />
            <FontFamilyContainer />
            <TransparencyContainer />
            <PerformanceModeContainer />
        </>
    );
};

const UiLanguageContainer = () => {
    const { t } = useI18n();
    const { currentUiLanguage, setUiLanguage } = useAppearance();

    const is_not_en_lang = currentUiLanguage.data !== "en" && currentUiLanguage.data !== undefined;
    return (
        <RadioButtonContainer
            label={is_not_en_lang ? "UI Language" : t("config_page.appearance.ui_language.label")}
            desc={is_not_en_lang ? t("config_page.appearance.ui_language.label") : false}
            selectFunction={setUiLanguage}
            name="ui_language"
            options={ui_configs.selectable_ui_languages}
            checked_variable={currentUiLanguage}
        />
    );
};

const UiScalingContainer = () => {
    const { t } = useI18n();
    const { currentUiScaling, setUiScaling } = useAppearance();
    const { asyncUpdateBreakPoint } = useWindow();

    return (
        <SliderContainer
            label={t("config_page.appearance.ui_size.label")}
            valueLabelFormat="value %"
            variable={currentUiScaling.data}
            setterFunction={setUiScaling}
            postUpdateAction={asyncUpdateBreakPoint}
            min={40}
            max={200}
            step={10}
            show_label_values={[40, 60, 80, 100, 120, 140, 160, 180, 200]}
        />
    );
};


export const MessageLogUiScalingContainer = () => {
    const { t } = useI18n();
    const { currentMessageLogUiScaling, setMessageLogUiScaling } = useAppearance();

    return (
        <SliderContainer
            label={t("config_page.appearance.textbox_ui_size.label")}
            valueLabelFormat="value %"
            variable={currentMessageLogUiScaling.data}
            setterFunction={setMessageLogUiScaling}
            min={40}
            max={200}
            step={10}
            show_label_values={[40, 60, 80, 100, 120, 140, 160, 180, 200]}
        />
    );
};

const SendMessageButtonTypeContainer = () => {
    const { t } = useI18n();
    const { currentSendMessageButtonType, setSendMessageButtonType } = useAppearance();

    return (
        <RadioButtonContainer
            label={t("config_page.appearance.send_message_button_type.label")}
            selectFunction={setSendMessageButtonType}
            name="send_message_button_type"
            options={[
                { id: "hide", label: t("config_page.appearance.send_message_button_type.hide") },
                { id: "show", label: t("config_page.appearance.send_message_button_type.show") },
                { id: "show_and_disable_enter_key", label: t("config_page.appearance.send_message_button_type.show_and_disable_enter_key") },
            ]}
            checked_variable={currentSendMessageButtonType}
            column={true}
        />
    );
};

const ShowResendButtonContainer = () => {
    const { t } = useI18n();
    const { currentShowResendButton, toggleShowResendButton } = useAppearance();

    return (
        <CheckboxContainer
            label={t("config_page.appearance.show_resend_button.label")}
            desc={t("config_page.appearance.show_resend_button.desc")}
            variable={currentShowResendButton}
            toggleFunction={toggleShowResendButton}
        />
    );
};

const FontFamilyContainer = () => {
    const { t } = useI18n();
    const { currentSelectedFontFamily, setSelectedFontFamily } = useAppearance();

    const selectFunction = (selected_data) => {
        setSelectedFontFamily(selected_data.selected_id);
    };
    const { currentSelectableFontFamilyList } = useStore_SelectableFontFamilyList();

    return (
        <DropdownMenuContainer
            dropdown_id="font_family"
            label={t("config_page.appearance.font_family.label")}
            selected_id={currentSelectedFontFamily.data}
            list={currentSelectableFontFamilyList.data}
            selectFunction={selectFunction}
            state={currentSelectedFontFamily.state}
        />
    );
};

const TransparencyContainer = () => {
    const { t } = useI18n();
    const { currentTransparency, setTransparency } = useAppearance();

    return (
        <SliderContainer
            label={t("config_page.appearance.transparency.label")}
            valueLabelFormat="value %"
            variable={currentTransparency.data}
            setterFunction={setTransparency}
            min={40}
            max={100}
            step={1}
            label_format="value %"
        />
    );
};

const PerformanceModeContainer = () => {
    const { t } = useI18n();
    const { currentEnablePerformanceMode, updateEnablePerformanceMode } = useStore_EnablePerformanceMode();

    const toggleFunction = () => {
        const nextVal = !currentEnablePerformanceMode.data;
        updateEnablePerformanceMode(nextVal);
        localStorage.setItem("enable_performance_mode", nextVal ? "true" : "false");
    };

    return (
        <CheckboxContainer
            label={t("config_page.appearance.performance_mode.label")}
            desc={t("config_page.appearance.performance_mode.desc")}
            variable={currentEnablePerformanceMode}
            toggleFunction={toggleFunction}
        />
    );
};

const ThemeAccentContainer = () => {
    const [selectedTheme, setSelectedTheme] = useState(() => {
        const savedTheme = localStorage.getItem("theme_accent");
        return THEME_ACCENT_CLASSES.includes(savedTheme) ? savedTheme : "theme-neon-cyan";
    });

    const selectFunction = (selected_data) => {
        const newTheme = selected_data.selected_id;
        if (!THEME_ACCENT_CLASSES.includes(newTheme)) return;
        setSelectedTheme(newTheme);
        localStorage.setItem("theme_accent", newTheme);
        document.documentElement.classList.remove(...THEME_ACCENT_CLASSES);
        document.documentElement.classList.add(newTheme);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="theme_accent"
            label="Theme Accent Color"
            selected_id={selectedTheme}
            list={THEME_ACCENTS}
            selectFunction={selectFunction}
            state="ok"
        />
    );
};
