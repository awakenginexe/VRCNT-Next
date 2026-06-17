export const mainFunctionTooltipOrder = [
    "translation",
    "transcription_send",
    "transcription_receive",
    "foreground",
    "settings",
];

const mainFunctionTooltipMeta = {
    translation: {
        tooltipTitleKey: "main_page.main_function_tooltips.translation_title",
        tooltipDetailKey: "main_page.main_function_tooltips.translation_detail",
    },
    transcription_send: {
        tooltipTitleKey: "main_page.main_function_tooltips.transcription_send_title",
        tooltipDetailKey: "main_page.main_function_tooltips.transcription_send_detail",
    },
    transcription_receive: {
        tooltipTitleKey: "main_page.main_function_tooltips.transcription_receive_title",
        tooltipDetailKey: "main_page.main_function_tooltips.transcription_receive_detail",
    },
    foreground: {
        tooltipTitleKey: "main_page.main_function_tooltips.foreground_title",
        tooltipDetailKey: "main_page.main_function_tooltips.foreground_detail",
    },
    settings: {
        tooltipTitleKey: "main_page.main_function_tooltips.settings_title",
        tooltipDetailKey: "main_page.main_function_tooltips.settings_detail",
    },
};

export const getMainFunctionTooltipMeta = (controlId) => mainFunctionTooltipMeta[controlId] ?? {
    tooltipTitleKey: "main_page.main_function_tooltips.control_title",
    tooltipDetailKey: "main_page.main_function_tooltips.control_detail",
};
