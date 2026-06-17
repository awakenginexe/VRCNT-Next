import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");

const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

test("main page visible copy is routed through localization", () => {
    const files = [
        "src-ui/views/app/main_page/main_section/MainSection.jsx",
        "src-ui/views/app/main_page/main_section/language_selector/LanguageSelector.jsx",
        "src-ui/views/app/main_page/main_section/top_bar/right_side_components/RightSideComponents.jsx",
        "src-ui/views/app/main_page/sidebar_section/language_settings/LanguageSettings.jsx",
        "src-ui/views/app/main_page/sidebar_section/language_settings/language_selector_open_button/LanguageSelectorOpenButton.jsx",
        "src-ui/views/app/main_page/sidebar_section/language_settings/transcription_engine_label/TranscriptionEngineLabel.jsx",
        "src-ui/views/app/main_page/sidebar_section/main_function_switch/MainFunctionSwitch.jsx",
        "src-ui/views/app/main_page/sidebar_section/main_function_switch/mainFunctionTooltipMeta.js",
    ];
    const source = files.map(readSource).join("\n");
    const forbiddenPhrases = [
        "Voice input and personal translation output",
        "Choose who you want VRCNT-Next to translate for",
        "Quick switches for translation and transcription",
        "Your speaking language",
        "Your translation language",
        "Quick switch between CPU and GPU",
        "Processing Type",
        "Locked to Auto for this engine",
        "Choose the runtime mode for Whisper",
        "Overlay(VR)",
        "Starting translator",
        "Waiting for backend startup",
        "Turn chat translation on or off.",
        "Open app configuration.",
        "only enables languages supported by the selected model.",
    ];

    for (const phrase of forbiddenPhrases) {
        assert.equal(source.includes(phrase), false, phrase);
    }
});
