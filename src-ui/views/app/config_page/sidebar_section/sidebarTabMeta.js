export const sidebarTabOrder = [
    "device",
    "appearance",
    "translation",
    "transcription",
    "vr",
    "others",
    "hotkeys",
    "advanced_settings",
    "supporters",
    "about_vrct",
];

const sidebarTabMeta = {
    device: {
        label: "Device",
        tooltipTitle: "Audio devices",
        tooltipDetail: "Choose microphone and speaker input.",
    },
    appearance: {
        label: "Appearance",
        tooltipTitle: "Appearance",
        tooltipDetail: "Adjust theme, scale, and window style.",
    },
    translation: {
        label: "Translation",
        tooltipTitle: "Translation",
        tooltipDetail: "Configure translation engines and output.",
    },
    transcription: {
        label: "Transcription",
        tooltipTitle: "Transcription",
        tooltipDetail: "Tune speech recognition settings.",
    },
    vr: {
        label: "VR",
        tooltipTitle: "VR overlay",
        tooltipDetail: "Set VRChat overlay and OSC behavior.",
    },
    others: {
        label: "Others",
        tooltipTitle: "Other settings",
        tooltipDetail: "Manage general app behavior.",
    },
    hotkeys: {
        label: "Hotkeys",
        tooltipTitle: "Hotkeys",
        tooltipDetail: "Set keyboard shortcuts.",
    },
    advanced_settings: {
        label: "Advanced Settings",
        tooltipTitle: "Advanced",
        tooltipDetail: "Change expert-level options.",
    },
    supporters: {
        label: "Credit",
        tooltipTitle: "Credit",
        tooltipDetail: "View original VRCT project credit.",
    },
    about_vrct: {
        label: "About VRCNT-Next",
        tooltipTitle: "About",
        tooltipDetail: "See project details and links.",
    },
};

export const getSidebarTabMeta = (tabId, translate) => {
    const meta = sidebarTabMeta[tabId] ?? {
        label: tabId,
        tooltipTitle: tabId,
        tooltipDetail: "Open this settings section.",
    };

    if (tabId === "vr" || tabId === "supporters" || tabId === "about_vrct") {
        return meta;
    }

    return {
        ...meta,
        label: typeof translate === "function"
            ? translate(`config_page.side_menu_labels.${tabId}`)
            : meta.label,
    };
};
