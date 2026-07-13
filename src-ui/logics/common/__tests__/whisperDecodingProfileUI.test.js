import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import yaml from "js-yaml";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

const settingsSource = readSource(
    "src-ui/logics/configs/config_page_setter/ui_config_setter.js",
);
const settingsLogicsSource = readSource(
    "src-ui/logics/configs/config_page_setter/useSettingsLogics.js",
);
const configsIndexSource = readSource("src-ui/logics/configs/index.js");
const transcriptionSource = readSource(
    "src-ui/views/app/config_page/setting_section/setting_box/transcription/Transcription.jsx",
);

const localeFiles = [
    "en.yml",
    "th.yml",
    "ja.yml",
    "ko.yml",
    "zh-Hans.yml",
    "zh-Hant.yml",
];

const expectedLocales = {
    "en.yml": {
        label: "Whisper decoding profile",
        desc: "Choose the balance between VR responsiveness and decoder accuracy.",
        fast: "Fast · beam 1 · lowest latency",
        balanced: "Balanced · beam 2 · recommended for VRChat",
        accurate: "Accurate · beam 5 · highest decoder cost",
    },
    "th.yml": {
        label: "โปรไฟล์การถอดรหัส Whisper",
        desc: "เลือกระหว่างความลื่นไหลใน VR และความแม่นยำของตัวถอดรหัส",
        fast: "เร็ว · beam 1 · หน่วงต่ำสุด",
        balanced: "สมดุล · beam 2 · แนะนำสำหรับ VRChat",
        accurate: "แม่นยำ · beam 5 · ใช้การประมวลผลสูงสุด",
    },
    "ja.yml": {
        label: "Whisper デコードプロファイル",
        desc: "VR の応答性とデコード精度のバランスを選びます。",
        fast: "高速 · beam 1 · 最小遅延",
        balanced: "バランス · beam 2 · VRChat 推奨",
        accurate: "高精度 · beam 5 · デコード負荷最大",
    },
    "ko.yml": {
        label: "Whisper 디코딩 프로필",
        desc: "VR 반응성과 디코더 정확도의 균형을 선택합니다.",
        fast: "빠름 · beam 1 · 최저 지연",
        balanced: "균형 · beam 2 · VRChat 권장",
        accurate: "정확 · beam 5 · 최고 디코더 부하",
    },
    "zh-Hans.yml": {
        label: "Whisper 解码配置",
        desc: "选择 VR 响应速度与解码准确度之间的平衡。",
        fast: "快速 · beam 1 · 最低延迟",
        balanced: "均衡 · beam 2 · 推荐用于 VRChat",
        accurate: "准确 · beam 5 · 解码开销最高",
    },
    "zh-Hant.yml": {
        label: "Whisper 解碼設定",
        desc: "選擇 VR 回應速度與解碼準確度之間的平衡。",
        fast: "快速 · beam 1 · 最低延遲",
        balanced: "均衡 · beam 2 · 建議用於 VRChat",
        accurate: "精確 · beam 5 · 解碼負載最高",
    },
};

const getSettingEntry = (baseName) => {
    const escapedBaseName = baseName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return settingsSource.match(
        new RegExp(`\\{[^{}]*Base_Name:\\s*"${escapedBaseName}"[^{}]*\\}`, "s"),
    )?.[0] ?? null;
};

const getQuotedProperty = (entry, property) => (
    entry?.match(new RegExp(`${property}:\\s*"([^"]+)"`))?.[1] ?? null
);

test("Whisper decoding profile has the exact dynamic config contract", () => {
    const entry = getSettingEntry("WhisperDecodingProfile");

    assert.ok(entry, "WhisperDecodingProfile must be registered");
    assert.equal(getQuotedProperty(entry, "Category"), "Transcription");
    assert.equal(getQuotedProperty(entry, "Base_Name"), "WhisperDecodingProfile");
    assert.equal(getQuotedProperty(entry, "default_value"), "balanced");
    assert.equal(getQuotedProperty(entry, "ui_template_id"), "select");
    assert.equal(getQuotedProperty(entry, "logics_template_id"), "get_set");
    assert.equal(getQuotedProperty(entry, "base_endpoint_name"), "whisper_decoding_profile");

    const endpoint = getQuotedProperty(entry, "base_endpoint_name");
    assert.equal(`/get/data/${endpoint}`, "/get/data/whisper_decoding_profile");
    assert.equal(`/set/data/${endpoint}`, "/set/data/whisper_decoding_profile");
    assert.match(
        settingsLogicsSource,
        /asyncStdoutToPython\(`\/get\/data\/\$\{s\.base_endpoint_name\}`\)/,
    );
    assert.match(
        settingsLogicsSource,
        /asyncStdoutToPython\(`\/set\/data\/\$\{s\.base_endpoint_name\}`, value\)/,
    );
});

test("useTranscription generates current and setter profile fields without a new store path", () => {
    assert.match(
        configsIndexSource,
        /useTranscription\s*=\s*createCategoryHook\("Transcription"\)/,
    );
    assert.match(settingsLogicsSource, /const currentExportName = `current\$\{base\}`/);
    assert.match(settingsLogicsSource, /const setExportName = `set\$\{base\}`/);
    assert.match(
        settingsLogicsSource,
        /if \(s\.logics_template_id === "get_set"\)[\s\S]*?result\[setExportName\] = buildSet\(\)/,
    );
    assert.match(
        settingsSource,
        /const COMMON_PROPS = \[\s*"current",[\s\S]*?"set",/,
    );
});

test("the exact profile options render once, next to compute settings, and only for Whisper", () => {
    const invocation = /\{engine === "Whisper" && <WhisperDecodingProfile_Box \/>\}/g;
    assert.equal([...transcriptionSource.matchAll(invocation)].length, 1);
    assert.match(
        transcriptionSource,
        /<TranscriptionComputeDevice_Box \/>[\s\S]*?\{engine === "Whisper" && <WhisperDecodingProfile_Box \/>\}/,
    );
    assert.match(
        transcriptionSource,
        /const WHISPER_DECODING_PROFILE_IDS = Object\.freeze\(\["fast", "balanced", "accurate"\]\)/,
    );
    assert.match(
        transcriptionSource,
        /currentWhisperDecodingProfile,[\s\S]*?setWhisperDecodingProfile/,
    );
    assert.match(
        transcriptionSource,
        /WHISPER_DECODING_PROFILE_IDS\.includes\(selected_data\.selected_id\)[\s\S]*?setWhisperDecodingProfile\(selected_data\.selected_id\)/,
    );
    assert.match(transcriptionSource, /dropdown_id="whisper_decoding_profile"/);
    for (const option of ["fast", "balanced", "accurate"]) {
        assert.match(
            transcriptionSource,
            new RegExp(`${option}:\\s*t\\("config_page\\.transcription\\.whisper_decoding_profile\\.${option}"\\)`),
        );
    }
});

test("all six locales provide the exact decoding-profile copy with schema parity", () => {
    const englishKeys = Object.keys(expectedLocales["en.yml"]).sort();

    for (const localeFile of localeFiles) {
        const locale = yaml.load(readSource(`locales/${localeFile}`));
        const namespace = locale?.config_page?.transcription?.whisper_decoding_profile;

        assert.deepEqual(namespace, expectedLocales[localeFile], localeFile);
        assert.deepEqual(Object.keys(namespace).sort(), englishKeys, `${localeFile} keys`);
    }
});
