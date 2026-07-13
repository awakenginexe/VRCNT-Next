import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import yaml from "js-yaml";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const localeFiles = [
    "en.yml",
    "th.yml",
    "ja.yml",
    "ko.yml",
    "zh-Hans.yml",
    "zh-Hant.yml",
];
const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

const expected = {
    "en.yml": {
        dialog_label: "Operation in progress",
        startup_operation: "Starting VRCNT-Next",
        phase_label: "Current step",
        progress_label: "Startup progress",
        progress_steps: "{{current}} of {{total}}",
        progress_indeterminate: "Working…",
        elapsed: "Elapsed {{seconds}}s",
        startup_failed: "Startup could not finish",
        startup_failed_detail: "Restart VRCNT-Next. If this continues, check the application log.",
        backend_startup_progress: "Backend startup {{current}} / {{total}}",
        backend_unavailable: "The backend is unavailable. Your change was not applied.",
        backend_disconnected: "The backend stopped. Restart VRCNT-Next to continue.",
    },
    "th.yml": {
        dialog_label: "กำลังดำเนินการ",
        startup_operation: "กำลังเริ่ม VRCNT-Next",
        phase_label: "ขั้นตอนปัจจุบัน",
        progress_label: "ความคืบหน้าในการเริ่มต้น",
        progress_steps: "{{current}} จาก {{total}}",
        progress_indeterminate: "กำลังดำเนินการ…",
        elapsed: "ผ่านไป {{seconds}} วินาที",
        startup_failed: "ไม่สามารถเริ่มต้นให้เสร็จสมบูรณ์",
        startup_failed_detail: "โปรดเริ่ม VRCNT-Next ใหม่ หากยังเกิดปัญหา ให้ตรวจสอบบันทึกของแอป",
        backend_startup_progress: "เริ่มระบบเบื้องหลัง {{current}} / {{total}}",
        backend_unavailable: "ระบบเบื้องหลังไม่พร้อมใช้งาน การเปลี่ยนแปลงของคุณยังไม่ถูกนำไปใช้",
        backend_disconnected: "ระบบเบื้องหลังหยุดทำงาน โปรดเริ่ม VRCNT-Next ใหม่เพื่อดำเนินการต่อ",
    },
    "ja.yml": {
        dialog_label: "処理中",
        startup_operation: "VRCNT-Next を起動しています",
        phase_label: "現在のステップ",
        progress_label: "起動の進行状況",
        progress_steps: "{{current}} / {{total}}",
        progress_indeterminate: "処理しています…",
        elapsed: "経過 {{seconds}} 秒",
        startup_failed: "起動を完了できませんでした",
        startup_failed_detail: "VRCNT-Next を再起動してください。続く場合はアプリのログを確認してください。",
        backend_startup_progress: "バックエンド起動 {{current}} / {{total}}",
        backend_unavailable: "バックエンドを利用できないため、変更は適用されませんでした。",
        backend_disconnected: "バックエンドが停止しました。VRCNT-Next を再起動してください。",
    },
    "ko.yml": {
        dialog_label: "작업 진행 중",
        startup_operation: "VRCNT-Next 시작 중",
        phase_label: "현재 단계",
        progress_label: "시작 진행률",
        progress_steps: "{{current}} / {{total}}",
        progress_indeterminate: "처리 중…",
        elapsed: "경과 {{seconds}}초",
        startup_failed: "시작을 완료하지 못했습니다",
        startup_failed_detail: "VRCNT-Next를 다시 시작하세요. 문제가 계속되면 앱 로그를 확인하세요.",
        backend_startup_progress: "백엔드 시작 {{current}} / {{total}}",
        backend_unavailable: "백엔드를 사용할 수 없어 변경 사항이 적용되지 않았습니다.",
        backend_disconnected: "백엔드가 중지되었습니다. 계속하려면 VRCNT-Next를 다시 시작하세요.",
    },
    "zh-Hans.yml": {
        dialog_label: "操作进行中",
        startup_operation: "正在启动 VRCNT-Next",
        phase_label: "当前步骤",
        progress_label: "启动进度",
        progress_steps: "{{current}} / {{total}}",
        progress_indeterminate: "正在处理…",
        elapsed: "已用 {{seconds}} 秒",
        startup_failed: "无法完成启动",
        startup_failed_detail: "请重新启动 VRCNT-Next。如果问题持续，请检查应用日志。",
        backend_startup_progress: "后端启动 {{current}} / {{total}}",
        backend_unavailable: "后端不可用，您的更改尚未应用。",
        backend_disconnected: "后端已停止。请重新启动 VRCNT-Next 后继续。",
    },
    "zh-Hant.yml": {
        dialog_label: "操作進行中",
        startup_operation: "正在啟動 VRCNT-Next",
        phase_label: "目前步驟",
        progress_label: "啟動進度",
        progress_steps: "{{current}} / {{total}}",
        progress_indeterminate: "正在處理…",
        elapsed: "已用 {{seconds}} 秒",
        startup_failed: "無法完成啟動",
        startup_failed_detail: "請重新啟動 VRCNT-Next。如果問題持續，請檢查應用程式記錄。",
        backend_startup_progress: "後端啟動 {{current}} / {{total}}",
        backend_unavailable: "後端無法使用，您的變更尚未套用。",
        backend_disconnected: "後端已停止。請重新啟動 VRCNT-Next 後繼續。",
    },
};

const interpolationTokens = (value) => (
    [...String(value).matchAll(/{{\s*([^}\s]+)\s*}}/g)]
        .map((match) => match[1])
        .sort()
);

test("all six locales contain the exact blocking-operation copy", () => {
    for (const localeFile of localeFiles) {
        const locale = yaml.load(readSource(`locales/${localeFile}`));
        assert.deepEqual(locale?.blocking_operation, expected[localeFile], localeFile);
    }
});

test("blocking-operation locale schema, values, and interpolation stay in parity", () => {
    const englishKeys = Object.keys(expected["en.yml"]).sort();

    for (const localeFile of localeFiles) {
        const locale = yaml.load(readSource(`locales/${localeFile}`));
        const namespace = locale?.blocking_operation ?? {};
        assert.deepEqual(Object.keys(namespace).sort(), englishKeys, `${localeFile} keys`);

        for (const key of englishKeys) {
            assert.equal(typeof namespace[key], "string", `${localeFile}:${key} type`);
            assert.notEqual(namespace[key].trim(), "", `${localeFile}:${key} empty`);
            assert.deepEqual(
                interpolationTokens(namespace[key]),
                interpolationTokens(expected["en.yml"][key]),
                `${localeFile}:${key} interpolation`,
            );
        }
    }
});

test("all activation start, warm, and long copy remains present in every locale", () => {
    const keys = [
        "translation_start",
        "translation_warm",
        "translation_long",
        "transcription_send_start",
        "transcription_send_warm",
        "transcription_send_long",
        "transcription_receive_start",
        "transcription_receive_warm",
        "transcription_receive_long",
    ];

    for (const localeFile of localeFiles) {
        const locale = yaml.load(readSource(`locales/${localeFile}`));
        const pending = locale?.main_page?.main_function_pending ?? {};
        for (const key of keys) {
            assert.equal(typeof pending[key], "string", `${localeFile}:${key} type`);
            assert.notEqual(pending[key].trim(), "", `${localeFile}:${key} empty`);
        }
    }
});

test("overlay, banner, transport, and lifecycle surfaces use locale keys", () => {
    const sources = {
        app: readSource("src-ui/views/app/App.jsx"),
        overlay: readSource(
            "src-ui/views/app/others/blocking_operation_overlay/BlockingOperationOverlay.jsx",
        ),
        banner: readSource(
            "src-ui/views/app/others/startup_status_banner/StartupStatusBanner.jsx",
        ),
        sidecar: readSource(
            "src-ui/views/app/_app_controllers/StartPythonController.jsx",
        ),
        mainFunction: readSource("src-ui/logics/main/useMainFunction.js"),
        state: readSource("src-ui/logics/common/blockingOperationState.js"),
    };

    for (const key of [
        "progress_label",
        "progress_steps",
        "progress_indeterminate",
        "elapsed",
    ]) {
        assert.match(sources.app, new RegExp(`blocking_operation\\.${key}`));
    }
    assert.match(sources.state, /blocking_operation\.startup_operation/);
    assert.match(sources.banner, /blocking_operation\.backend_startup_progress/);
    assert.match(sources.banner, /blocking_operation\.startup_failed/);
    assert.match(sources.banner, /blocking_operation\.startup_failed_detail/);
    assert.match(sources.sidecar, /blocking_operation\.backend_disconnected/);
    assert.match(sources.mainFunction, /blocking_operation\.backend_unavailable/);
    assert.match(sources.overlay, /\{title\}/);
    assert.match(sources.overlay, /\{phase\}/);
    assert.match(sources.overlay, /\{progressText\}/);
    assert.match(sources.overlay, /\{elapsedText\}/);

    const combinedSource = Object.values(sources).join("\n");
    for (const phrase of Object.values(expected["en.yml"])) {
        assert.equal(combinedSource.includes(phrase), false, phrase);
    }
});
