import {
    atom,
    useAtomValue,
    useSetAtom
} from "jotai";

import {
    generateTestConversationData,
} from "./_test_data.js"

import {
    translator_status,
} from "@ui_configs";

import { EMPTY_RESOURCE_USAGE } from "./common/resourceUsageUtils.js";
import { createEmptyPipelineStatusState } from "./common/pipelineStatusUtils.js";
import { isTauriRuntime } from "./common/tauriRuntime.js";

const IS_TAURI_RUNTIME = isTauriRuntime();

export const store = {
    backend_subprocess: null,
    setting_box_scroll_container: null,
    log_box_ref: null,
    text_area_ref: null,
    last_executed_time_startTyping: 0,
};

const createLanguageSlot = (language = "", country = "", enable = true) => ({
    language,
    country,
    enable,
});

const createYourLanguagePresetMap = () => ({
    1: {
        1: createLanguageSlot(),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    2: {
        1: createLanguageSlot(),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    3: {
        1: createLanguageSlot(),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
});

const createTargetLanguagePresetMap = () => ({
    1: {
        1: createLanguageSlot(),
        2: createLanguageSlot("", "", false),
        3: createLanguageSlot("", "", false),
    },
    2: {
        1: createLanguageSlot(),
        2: createLanguageSlot("", "", false),
        3: createLanguageSlot("", "", false),
    },
    3: {
        1: createLanguageSlot(),
        2: createLanguageSlot("", "", false),
        3: createLanguageSlot("", "", false),
    },
});

const createPreviewYourLanguagePresetMap = () => ({
    1: {
        1: createLanguageSlot("Japanese", "Japan"),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    2: {
        1: createLanguageSlot("English", "United States"),
        2: createLanguageSlot("Japanese", "Japan", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    3: {
        1: createLanguageSlot("Korean", "Korea"),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Japanese", "Japan", false),
    },
});

const createPreviewTargetLanguagePresetMap = () => ({
    1: {
        1: createLanguageSlot("English", "United States"),
        2: createLanguageSlot("Japanese", "Japan", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    2: {
        1: createLanguageSlot("Japanese", "Japan"),
        2: createLanguageSlot("English", "United States", false),
        3: createLanguageSlot("Chinese Simplified", "China", false),
    },
    3: {
        1: createLanguageSlot("English", "United States"),
        2: createLanguageSlot("Japanese", "Japan", false),
        3: createLanguageSlot("Korean", "Korea", false),
    },
});

const PREVIEW_RESOURCE_USAGE = {
    cpu: { available: true, percent: 24 },
    gpu: { available: true, percent: 68 },
    ram: { available: true, percent: 42 },
    vram: { available: true, percent: 61 },
    gpu_devices: [
        { device_index: 0, device_name: "RTX 4090" },
    ],
    selected_gpu_index: 0,
};

const PREVIEW_MESSAGE_LOGS = [
    {
        id: "preview-received-1",
        category: "received",
        status: "ok",
        created_at: "14:02",
        messages: {
            original: {
                message: "今日は新しいワールドに行ってみませんか？",
                transliteration: [],
            },
            translations: [
                {
                    message: "Why don't we go to a new world today?",
                    transliteration: [],
                },
            ],
        },
    },
    {
        id: "preview-sent-1",
        category: "sent",
        status: "ok",
        created_at: "14:03",
        messages: {
            original: {
                message: "賛成！どこかおすすめありますか？",
                transliteration: [],
            },
            translations: [
                {
                    message: "Agreed! Do you have any recommendations?",
                    transliteration: [],
                },
            ],
        },
    },
];

const generatePropertyNames = (base_name) => ({
    error: `error${base_name}`,
    pending: `pending${base_name}`,
    current: `current${base_name}`,
    update: `update${base_name}`,
    add: `add${base_name}`,
});

export const dynamicStoreRegistry = {};

export const createAtomWithHook = (initialValue, base_name, options) => {
    const property_names = generatePropertyNames(base_name);
    const atomInstance = atom({
        state: (options?.is_state_ok) ? "ok" : "pending",
        data: initialValue,
    });

    const useHook = () => {
        const currentAtom = useAtomValue(atomInstance);
        const setAtom = useSetAtom(atomInstance);

        const pendingAtom = () => {
            setAtom((old_value) => ({
                state: "pending",
                data: old_value.data,
            }));
        };

        const updateAtom = (payload, options = {}) => {
            const { remain_state = false, set_state, lock_state } = options;

            setAtom((currentValue) => {
                let new_state;
                if (lock_state) {
                    new_state = set_state;
                } else {
                    if (currentValue.lock_state) {
                        new_state = currentValue.state;
                    } else {
                        new_state = set_state ?? (remain_state ? currentValue.state : "ok");
                    }
                }

                const updated_data = typeof payload === "function"
                    ? payload(currentValue)
                    : payload;

                return {
                    state: new_state,
                    data: updated_data,
                };
            });
        };

        const errorAtom = () => {
            setAtom((old_value) => ({
                state: "error",
                data: old_value.data,
            }));
        };

        const addAtom = (value) => {
            setAtom((old_value) => {
                return {
                    state: "ok",
                    data: [...old_value.data, value],
                };
            });
        };

        return {
            [property_names.error]: errorAtom,
            [property_names.pending]: pendingAtom,
            [property_names.current]: currentAtom,
            [property_names.update]: updateAtom,
            [property_names.add]: addAtom,
        };
    };

    try {
        const hookName = `useStore_${base_name}`;
        const atomName = `Atom_${base_name}`;
        dynamicStoreRegistry[hookName] = useHook;
        dynamicStoreRegistry[atomName] = atomInstance;
    } catch (e) {
        console.warn("dynamic registration failed for", base_name, e);
    }

    return { atomInstance, useHook };
};


export const getStoreHook = (baseName) => {
    const hookName = `useStore_${baseName}`;
    return dynamicStoreRegistry[hookName];
};

export const registerMany = (settingsArray = []) => {
    for (const s of settingsArray) {
        try {
            const hookName = `useStore_${s.Base_Name}`;
            if (dynamicStoreRegistry[hookName]) {
                continue;
            }

            createAtomWithHook(s.default_value, s.Base_Name, s.options || {});
        } catch (e) {
            console.warn("registerMany failed for", s.Base_Name, e);
        }
    }
};



// Common
export const { atomInstance: Atom_IsBackendReady, useHook: useStore_IsBackendReady } = createAtomWithHook(!IS_TAURI_RUNTIME, "IsBackendReady");
export const { atomInstance: Atom_IsVrctAvailable, useHook: useStore_IsVrctAvailable } = createAtomWithHook(true, "IsVrctAvailable");
export const { atomInstance: Atom_IsOscAvailable, useHook: useStore_IsOscAvailable } = createAtomWithHook(true, "IsOscAvailable");
export const { atomInstance: Atom_ComputeMode, useHook: useStore_ComputeMode } = createAtomWithHook("", "ComputeMode");
export const { atomInstance: Atom_ResourceUsage, useHook: useStore_ResourceUsage } = createAtomWithHook(
    IS_TAURI_RUNTIME ? EMPTY_RESOURCE_USAGE : PREVIEW_RESOURCE_USAGE,
    "ResourceUsage",
    {is_state_ok: true}
);
export const { atomInstance: Atom_PipelineStatus, useHook: useStore_PipelineStatus } = createAtomWithHook(
    createEmptyPipelineStatusState(),
    "PipelineStatus",
    {is_state_ok: true}
);
export const { atomInstance: Atom_IsOpenedConfigPage, useHook: useStore_IsOpenedConfigPage } = createAtomWithHook(false, "IsOpenedConfigPage");
export const { atomInstance: Atom_MainFunctionsStateMemory, useHook: useStore_MainFunctionsStateMemory } = createAtomWithHook({
    transcription_send: false,
    transcription_receive: false,
}, "MainFunctionsStateMemory");
export const { atomInstance: Atom_OpenedQuickSetting, useHook: useStore_OpenedQuickSetting } = createAtomWithHook("", "OpenedQuickSetting");
export const { atomInstance: Atom_LatestSoftwareVersionInfo, useHook: useStore_LatestSoftwareVersionInfo } = createAtomWithHook({
    is_update_available: false,
    new_version: "0.0.0",
    release_url: "https://github.com/awakenginexe/VRCNT-Next/releases",
}, "LatestSoftwareVersionInfo");
export const { atomInstance: Atom_InitProgress, useHook: useStore_InitProgress } = createAtomWithHook(IS_TAURI_RUNTIME ? 0 : 4, "InitProgress");
export const { atomInstance: Atom_InitStatus, useHook: useStore_InitStatus } = createAtomWithHook({
    visible: IS_TAURI_RUNTIME,
    message: "Starting VRCNT-Next",
    detail: "Preparing startup.",
    phase: "starting",
}, "InitStatus");
export const { atomInstance: Atom_IsBreakPoint, useHook: useStore_IsBreakPoint } = createAtomWithHook(false, "IsBreakPoint");
export const { atomInstance: Atom_IsSoftwareUpdating, useHook: useStore_IsSoftwareUpdating } = createAtomWithHook(false, "IsSoftwareUpdating");
export const { atomInstance: Atom_NotificationStatus, useHook: useStore_NotificationStatus } = createAtomWithHook({
    status: "",
    is_open: false,
    key: 0,
    message: "",
}, "NotificationStatus");
export const { atomInstance: Atom_IsLMStudioConnected, useHook: useStore_IsLMStudioConnected } = createAtomWithHook(false, "IsLMStudioConnected");
export const { atomInstance: Atom_IsOllamaConnected, useHook: useStore_IsOllamaConnected } = createAtomWithHook(false, "IsOllamaConnected");
export const { atomInstance: Atom_EnablePerformanceMode, useHook: useStore_EnablePerformanceMode } = createAtomWithHook(localStorage.getItem("enable_performance_mode") === "true", "EnablePerformanceMode", {is_state_ok: true});

// Main Page
// Common
export const { atomInstance: Atom_IsMainPageCompactMode, useHook: useStore_IsMainPageCompactMode } = createAtomWithHook(false, "IsMainPageCompactMode");

// Sidebar Section
export const { atomInstance: Atom_TranslationStatus, useHook: useStore_TranslationStatus } = createAtomWithHook(!IS_TAURI_RUNTIME, "TranslationStatus", {is_state_ok: true});
export const { atomInstance: Atom_TranscriptionSendStatus, useHook: useStore_TranscriptionSendStatus } = createAtomWithHook(!IS_TAURI_RUNTIME, "TranscriptionSendStatus", {is_state_ok: true});
export const { atomInstance: Atom_TranscriptionReceiveStatus, useHook: useStore_TranscriptionReceiveStatus } = createAtomWithHook(!IS_TAURI_RUNTIME, "TranscriptionReceiveStatus", {is_state_ok: true});
export const { atomInstance: Atom_ForegroundStatus, useHook: useStore_ForegroundStatus } = createAtomWithHook(false, "ForegroundStatus", {is_state_ok: true});

export const { atomInstance: Atom_SelectedPresetTabNumber, useHook: useStore_SelectedPresetTabNumber } = createAtomWithHook("1", "SelectedPresetTabNumber");
export const { atomInstance: Atom_SelectedYourLanguages, useHook: useStore_SelectedYourLanguages } = createAtomWithHook(
    IS_TAURI_RUNTIME ? createYourLanguagePresetMap() : createPreviewYourLanguagePresetMap(),
    "SelectedYourLanguages"
);
export const { atomInstance: Atom_SelectedYourTranslationLanguages, useHook: useStore_SelectedYourTranslationLanguages } = createAtomWithHook(
    IS_TAURI_RUNTIME ? createYourLanguagePresetMap() : createPreviewTargetLanguagePresetMap(),
    "SelectedYourTranslationLanguages"
);
export const { atomInstance: Atom_SelectedTargetLanguages, useHook: useStore_SelectedTargetLanguages } = createAtomWithHook(
    IS_TAURI_RUNTIME ? createTargetLanguagePresetMap() : createPreviewTargetLanguagePresetMap(),
    "SelectedTargetLanguages"
);

export const { atomInstance: Atom_TranslationEngines, useHook: useStore_TranslationEngines } = createAtomWithHook(
    translator_status,
    "TranslationEngines",
    {is_state_ok: !IS_TAURI_RUNTIME}
);
export const { atomInstance: Atom_SelectedTranslationEngines, useHook: useStore_SelectedTranslationEngines } = createAtomWithHook(
    IS_TAURI_RUNTIME ? {1:"", 2:"", 3:""} : {1:"DeepL_API", 2:"CTranslate2", 3:"Google"},
    "SelectedTranslationEngines"
);
export const { atomInstance: Atom_TranslationEngineSelectionTransition, useHook: useStore_TranslationEngineSelectionTransition } = createAtomWithHook(
    null,
    "TranslationEngineSelectionTransition",
    {is_state_ok: true}
);
export const { atomInstance: Atom_IsOpenedTranslatorSelector, useHook: useStore_IsOpenedTranslatorSelector } = createAtomWithHook(false, "IsOpenedTranslatorSelector");
export const { atomInstance: Atom_IsOpenedTranscriptionEngineSelector, useHook: useStore_IsOpenedTranscriptionEngineSelector } = createAtomWithHook(false, "IsOpenedTranscriptionEngineSelector");

// Language Selector
export const { atomInstance: Atom_IsOpenedLanguageSelector, useHook: useStore_IsOpenedLanguageSelector } = createAtomWithHook(
    { your_language: false, your_translation_language: false, target_language: false, target_key: "1" },
    "IsOpenedLanguageSelector"
);
export const { atomInstance: Atom_SelectableLanguageList, useHook: useStore_SelectableLanguageList } = createAtomWithHook([], "SelectableLanguageList");

// Message Container
export const { atomInstance: Atom_MessageLogs, useHook: useStore_MessageLogs } = createAtomWithHook(IS_TAURI_RUNTIME ? [] : PREVIEW_MESSAGE_LOGS, "MessageLogs");
// export const { atomInstance: Atom_MessageLogs, useHook: useStore_MessageLogs } = createAtomWithHook(generateTestConversationData(20), "MessageLogs"); // For testing
export const { atomInstance: Atom_MessageInputBoxRatio, useHook: useStore_MessageInputBoxRatio } = createAtomWithHook(IS_TAURI_RUNTIME ? 20 : 11, "MessageInputBoxRatio");
export const { atomInstance: Atom_MessageInputValue, useHook: useStore_MessageInputValue } = createAtomWithHook("", "MessageInputValue");



// Config Page
// Common
export const { atomInstance: Atom_SoftwareVersion, useHook: useStore_SoftwareVersion } = createAtomWithHook("-", "SoftwareVersion");
export const { atomInstance: Atom_SelectedConfigTabId, useHook: useStore_SelectedConfigTabId } = createAtomWithHook("device", "SelectedConfigTabId");
export const { atomInstance: Atom_SettingBoxScrollPosition, useHook: useStore_SettingBoxScrollPosition } = createAtomWithHook(0, "SettingBoxScrollPosition");
export const { atomInstance: Atom_IsOpenedDropdownMenu, useHook: useStore_IsOpenedDropdownMenu } = createAtomWithHook("", "IsOpenedDropdownMenu");

// Device
export const { atomInstance: Atom_MicVolume, useHook: useStore_MicVolume } = createAtomWithHook(0, "MicVolume");
export const { atomInstance: Atom_SpeakerVolume, useHook: useStore_SpeakerVolume } = createAtomWithHook(0, "SpeakerVolume");

export const { atomInstance: Atom_MicThresholdCheckStatus, useHook: useStore_MicThresholdCheckStatus } = createAtomWithHook(false, "MicThresholdCheckStatus", {is_state_ok: true});
export const { atomInstance: Atom_SpeakerThresholdCheckStatus, useHook: useStore_SpeakerThresholdCheckStatus } = createAtomWithHook(false, "SpeakerThresholdCheckStatus", {is_state_ok: true});

export const { atomInstance: Atom_SelectableFontFamilyList, useHook: useStore_SelectableFontFamilyList } = createAtomWithHook({}, "SelectableFontFamilyList");


export const { atomInstance: Atom_IsOpenedMicWordFilterList, useHook: useStore_IsOpenedMicWordFilterList } = createAtomWithHook(false, "IsOpenedMicWordFilterList");

export const { atomInstance: Atom_MessageFormat_ExampleViewFilter, useHook: useStore_MessageFormat_ExampleViewFilter } = createAtomWithHook({
    send: "Simplified",
    received: "Simplified",
}, "MessageFormat_ExampleViewFilter");


// Hotkeys
export const { atomInstance: Atom_Hotkeys, useHook: useStore_Hotkeys } = createAtomWithHook({
    toggle_vrct_visibility: null,
    toggle_translation: null,
    toggle_transcription_send: null,
    toggle_transcription_receive: null,
}, "Hotkeys");

// Supporters
export const { atomInstance: Atom_SupportersData, useHook: useStore_SupportersData } = createAtomWithHook(null, "SupportersData", {is_state_ok: true});

// About VRCT
export const { atomInstance: Atom_VrctPosterIndex, useHook: useStore_VrctPosterIndex } = createAtomWithHook(0, "VrctPosterIndex");
export const { atomInstance: Atom_PosterShowcaseWorldPageIndex, useHook: useStore_PosterShowcaseWorldPageIndex } = createAtomWithHook(0, "PosterShowcaseWorldPageIndex");
