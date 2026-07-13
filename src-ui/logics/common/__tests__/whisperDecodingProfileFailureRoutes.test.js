import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
    buildConfigFailureSettlementMeta,
    handleConfigRouteErrorOutcome,
} from "../../routeFailureSettlement.js";

const profileSetting = {
    Base_Name: "WhisperDecodingProfile",
    failure_settlement_statuses: [500, 503],
    failure_settlement_results: ["fast", "balanced", "accurate"],
};

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

test("Whisper profile failure metadata targets generated backend update only", () => {
    assert.deepEqual(buildConfigFailureSettlementMeta(profileSetting), {
        failure_method_name: "updateFromBackendWhisperDecodingProfile",
        failure_statuses: [500, 503],
        failure_result_values: ["fast", "balanced", "accurate"],
    });
});

test("the profile registry opts its generated set route into guarded failure settlement", () => {
    const settingsSource = readSource(
        "src-ui/logics/configs/config_page_setter/ui_config_setter.js",
    );
    const receiveRoutesSource = readSource("src-ui/logics/useReceiveRoutes.js");
    const profileEntry = settingsSource.match(
        /\{[^{}]*Base_Name:\s*"WhisperDecodingProfile"[^{}]*\}/s,
    )?.[0] ?? "";

    assert.match(profileEntry, /failure_settlement_statuses:\s*\[500, 503\]/);
    assert.match(
        profileEntry,
        /failure_settlement_results:\s*\["fast", "balanced", "accurate"\]/,
    );
    assert.match(
        receiveRoutesSource,
        /method_name:\s*setSuccessMethodName,[\s\S]*?\.\.\.failureSettlementMeta/,
    );
    assert.match(
        receiveRoutesSource,
        /case 500:\s*case 503:[\s\S]*?handleConfigRouteErrorOutcome\(\{[\s\S]*?hookResult:/,
    );
});

for (const { status, result } of [
    { status: 500, result: "accurate" },
    { status: 503, result: "balanced" },
]) {
    test(`${status} profile responses settle pending state and retain the error surface`, () => {
        const calls = [];
        const routeMeta = buildConfigFailureSettlementMeta(profileSetting);
        const hookResult = {
            updateFromBackendWhisperDecodingProfile: (value) => {
                calls.push(["update", value]);
            },
            setSuccessWhisperDecodingProfile: (value) => {
                calls.push(["save-success", value]);
            },
        };

        const settled = handleConfigRouteErrorOutcome({
            routeMeta,
            hookResult,
            status,
            result,
            showError: () => calls.push(["error"]),
        });

        assert.equal(settled, true);
        assert.deepEqual(calls, [
            ["update", result],
            ["error"],
        ]);
    });
}

test("unexpected failure payloads are never written into generated settings", () => {
    const calls = [];
    const routeMeta = buildConfigFailureSettlementMeta(profileSetting);

    const settled = handleConfigRouteErrorOutcome({
        routeMeta,
        hookResult: {
            updateFromBackendWhisperDecodingProfile: (value) => {
                calls.push(["update", value]);
            },
        },
        status: 500,
        result: { error: "arbitrary backend payload" },
        showError: () => calls.push(["error"]),
    });

    assert.equal(settled, false);
    assert.deepEqual(calls, [["error"]]);
});
