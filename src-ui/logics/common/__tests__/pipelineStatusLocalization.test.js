import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import yaml from "js-yaml";

import { createEmptyPipelineStatusState } from "../pipelineStatusUtils.js";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const localeFiles = ["en.yml", "th.yml", "ja.yml", "ko.yml", "zh-Hans.yml", "zh-Hant.yml"];

const expected = {
    "en.yml": {
        source: "Source",
        listening: "Listening",
        speaking: "Speaking",
        transcription: "Transcription",
        cloud: "Cloud",
        queue: "Queue",
        total: "Total",
        healthy: "Healthy",
        slow: "Slow",
        error: "Error",
        waiting: "Waiting",
        unavailable: "Unavailable",
        timeout_announcement: "{{engine}} translation timed out",
        overload_announcement: "Translation queue overloaded",
        error_announcement: "{{stage}} failed",
        recovered_announcement: "{{stage}} recovered",
    },
    "th.yml": {
        source: "แหล่งเสียง",
        listening: "กำลังฟัง",
        speaking: "กำลังพูด",
        transcription: "ถอดเสียง",
        cloud: "คลาวด์",
        queue: "คิว",
        total: "รวม",
        healthy: "ปกติ",
        slow: "ช้า",
        error: "ข้อผิดพลาด",
        waiting: "กำลังรอ",
        unavailable: "ไม่พร้อมใช้งาน",
        timeout_announcement: "การแปลด้วย {{engine}} หมดเวลา",
        overload_announcement: "คิวการแปลทำงานหนักเกินไป",
        error_announcement: "{{stage}} ล้มเหลว",
        recovered_announcement: "{{stage}} กลับมาทำงานแล้ว",
    },
    "ja.yml": {
        source: "入力",
        listening: "リスニング",
        speaking: "スピーキング",
        transcription: "文字起こし",
        cloud: "クラウド",
        queue: "キュー",
        total: "合計",
        healthy: "正常",
        slow: "低速",
        error: "エラー",
        waiting: "待機中",
        unavailable: "利用不可",
        timeout_announcement: "{{engine}} の翻訳がタイムアウトしました",
        overload_announcement: "翻訳キューが過負荷です",
        error_announcement: "{{stage}} が失敗しました",
        recovered_announcement: "{{stage}} が復旧しました",
    },
    "ko.yml": {
        source: "소스",
        listening: "듣기",
        speaking: "말하기",
        transcription: "음성 인식",
        cloud: "클라우드",
        queue: "대기열",
        total: "전체",
        healthy: "정상",
        slow: "느림",
        error: "오류",
        waiting: "대기 중",
        unavailable: "사용 불가",
        timeout_announcement: "{{engine}} 번역 시간 초과",
        overload_announcement: "번역 대기열 과부하",
        error_announcement: "{{stage}} 실패",
        recovered_announcement: "{{stage}} 복구됨",
    },
    "zh-Hans.yml": {
        source: "来源",
        listening: "正在聆听",
        speaking: "正在说话",
        transcription: "转写",
        cloud: "云端",
        queue: "队列",
        total: "总计",
        healthy: "正常",
        slow: "缓慢",
        error: "错误",
        waiting: "等待中",
        unavailable: "不可用",
        timeout_announcement: "{{engine}} 翻译超时",
        overload_announcement: "翻译队列过载",
        error_announcement: "{{stage}} 失败",
        recovered_announcement: "{{stage}} 已恢复",
    },
    "zh-Hant.yml": {
        source: "來源",
        listening: "正在聆聽",
        speaking: "正在說話",
        transcription: "轉錄",
        cloud: "雲端",
        queue: "佇列",
        total: "總計",
        healthy: "正常",
        slow: "緩慢",
        error: "錯誤",
        waiting: "等待中",
        unavailable: "無法使用",
        timeout_announcement: "{{engine}} 翻譯逾時",
        overload_announcement: "翻譯佇列過載",
        error_announcement: "{{stage}} 失敗",
        recovered_announcement: "{{stage}} 已恢復",
    },
};

const interpolationTokens = (value) => (
    [...String(value).matchAll(/{{\s*([^}\s]+)\s*}}/g)]
        .map((match) => match[1])
        .sort()
);

test("all six pipeline-status namespaces match the approved copy exactly", () => {
    assert.deepEqual(createEmptyPipelineStatusState().traces, {});

    for (const localeFile of localeFiles) {
        const locale = yaml.load(
            fs.readFileSync(path.join(repoRoot, "locales", localeFile), "utf8"),
        );
        assert.deepEqual(locale?.main_page?.pipeline_status, expected[localeFile], localeFile);
    }
});

test("pipeline-status keys and interpolation tokens match English", () => {
    const english = expected["en.yml"];
    const englishKeys = Object.keys(english).sort();

    for (const localeFile of localeFiles) {
        const locale = yaml.load(
            fs.readFileSync(path.join(repoRoot, "locales", localeFile), "utf8"),
        );
        const namespace = locale?.main_page?.pipeline_status ?? {};

        assert.deepEqual(Object.keys(namespace).sort(), englishKeys, `${localeFile} keys`);
        for (const key of englishKeys) {
            assert.deepEqual(
                interpolationTokens(namespace[key]),
                interpolationTokens(english[key]),
                `${localeFile}:${key}`,
            );
        }
    }
});
