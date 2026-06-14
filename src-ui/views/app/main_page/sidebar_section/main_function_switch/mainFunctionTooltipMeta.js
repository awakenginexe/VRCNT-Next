export const mainFunctionTooltipOrder = [
    "translation",
    "transcription_send",
    "transcription_receive",
    "foreground",
    "settings",
];

const mainFunctionTooltipMeta = {
    translation: {
        tooltipTitle: "Translation",
        tooltipDetail: "Turn chat translation on or off.",
    },
    transcription_send: {
        tooltipTitle: "Speaking",
        tooltipDetail: "Transcribe your microphone for chat.",
    },
    transcription_receive: {
        tooltipTitle: "Listening",
        tooltipDetail: "Transcribe audio you hear from others.",
    },
    foreground: {
        tooltipTitle: "Always on top",
        tooltipDetail: "Keep VRCNT-Next above other windows.",
    },
    settings: {
        tooltipTitle: "Settings",
        tooltipDetail: "Open app configuration.",
    },
};

export const getMainFunctionTooltipMeta = (controlId) => mainFunctionTooltipMeta[controlId] ?? {
    tooltipTitle: "Control",
    tooltipDetail: "Use this app control.",
};
