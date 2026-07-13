import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import yaml from "js-yaml";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");

const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

const translationStatusCopy = {
    "en.yml": {
        queued: "Waiting for {{engine}} · {{elapsed}}",
        sending: "Translating with {{engine}} · {{elapsed}}",
        fallback: "{{previousEngine}} is slow · trying {{engine}}",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "Translation unavailable · {{engine}} timed out",
        error: "Translation unavailable · {{engine}} failed",
        skipped_overload: "Translation skipped · queue overloaded",
        no_provider: "Translation unavailable · no provider selected",
        unavailable: "Translation unavailable",
        queue_position: "Queue {{position}}",
    },
    "th.yml": {
        queued: "กำลังรอ {{engine}} · {{elapsed}}",
        sending: "กำลังแปลด้วย {{engine}} · {{elapsed}}",
        fallback: "{{previousEngine}} ช้า · กำลังลอง {{engine}}",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "ไม่มีคำแปล · {{engine}} หมดเวลา",
        error: "ไม่มีคำแปล · {{engine}} ล้มเหลว",
        skipped_overload: "ข้ามการแปล · คิวทำงานหนักเกินไป",
        no_provider: "ไม่มีคำแปล · ยังไม่ได้เลือกผู้ให้บริการ",
        unavailable: "ไม่มีคำแปล",
        queue_position: "คิว {{position}}",
    },
    "ja.yml": {
        queued: "{{engine}} を待機中 · {{elapsed}}",
        sending: "{{engine}} で翻訳中 · {{elapsed}}",
        fallback: "{{previousEngine}} が遅延 · {{engine}} を試行中",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "翻訳を利用できません · {{engine}} がタイムアウトしました",
        error: "翻訳を利用できません · {{engine}} が失敗しました",
        skipped_overload: "翻訳をスキップしました · キューが過負荷です",
        no_provider: "翻訳を利用できません · プロバイダーが未選択です",
        unavailable: "翻訳を利用できません",
        queue_position: "キュー {{position}}",
    },
    "ko.yml": {
        queued: "{{engine}} 대기 중 · {{elapsed}}",
        sending: "{{engine}}로 번역 중 · {{elapsed}}",
        fallback: "{{previousEngine}} 지연 · {{engine}} 시도 중",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "번역을 사용할 수 없음 · {{engine}} 시간 초과",
        error: "번역을 사용할 수 없음 · {{engine}} 실패",
        skipped_overload: "번역 건너뜀 · 대기열 과부하",
        no_provider: "번역을 사용할 수 없음 · 제공자 미선택",
        unavailable: "번역을 사용할 수 없음",
        queue_position: "대기열 {{position}}",
    },
    "zh-Hans.yml": {
        queued: "正在等待 {{engine}} · {{elapsed}}",
        sending: "正在使用 {{engine}} 翻译 · {{elapsed}}",
        fallback: "{{previousEngine}} 响应缓慢 · 正在尝试 {{engine}}",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "翻译不可用 · {{engine}} 请求超时",
        error: "翻译不可用 · {{engine}} 失败",
        skipped_overload: "已跳过翻译 · 队列过载",
        no_provider: "翻译不可用 · 未选择服务商",
        unavailable: "翻译不可用",
        queue_position: "队列 {{position}}",
    },
    "zh-Hant.yml": {
        queued: "正在等待 {{engine}} · {{elapsed}}",
        sending: "正在使用 {{engine}} 翻譯 · {{elapsed}}",
        fallback: "{{previousEngine}} 回應緩慢 · 正在嘗試 {{engine}}",
        success_meta: "{{engine}} · {{duration}}",
        timeout: "翻譯無法使用 · {{engine}} 請求逾時",
        error: "翻譯無法使用 · {{engine}} 失敗",
        skipped_overload: "已略過翻譯 · 佇列過載",
        no_provider: "翻譯無法使用 · 未選擇服務商",
        unavailable: "翻譯無法使用",
        queue_position: "佇列 {{position}}",
    },
};

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

test("progressive translation status copy matches all six locale contracts", () => {
    for (const [localeFile, expectedCopy] of Object.entries(translationStatusCopy)) {
        const locale = yaml.load(readSource(`locales/${localeFile}`));
        assert.deepEqual(
            locale?.main_page?.message_log?.translation_status,
            expectedCopy,
            localeFile,
        );
    }
});

test("translation entry contains no visible English status copy", () => {
    const relativePath = (
        "src-ui/views/app/main_page/main_section/message_container/log_box/"
        + "message_container/translation_entry/TranslationEntry.jsx"
    );
    assert.equal(
        fs.existsSync(path.join(repoRoot, relativePath)),
        true,
        "TranslationEntry.jsx must route visible status copy through i18n",
    );

    const source = readSource(relativePath);
    const forbiddenPhrases = Object.values(translationStatusCopy["en.yml"]);

    for (const phrase of forbiddenPhrases) {
        assert.equal(source.includes(phrase), false, phrase);
    }
});
