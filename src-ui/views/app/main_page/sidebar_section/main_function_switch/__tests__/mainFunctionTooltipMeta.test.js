import test from "node:test";
import assert from "node:assert/strict";

import { getMainFunctionTooltipMeta, mainFunctionTooltipOrder } from "../mainFunctionTooltipMeta.js";

test("main sidebar controls have short tooltip text", () => {
    for (const controlId of mainFunctionTooltipOrder) {
        const meta = getMainFunctionTooltipMeta(controlId);
        assert.ok(meta.tooltipTitle.length > 0);
        assert.ok(meta.tooltipDetail.length > 0);
        assert.ok(meta.tooltipTitle.length <= 24);
        assert.ok(meta.tooltipDetail.length <= 56);
    }
});

test("settings tooltip explains it opens configuration", () => {
    assert.deepEqual(getMainFunctionTooltipMeta("settings"), {
        tooltipTitle: "Settings",
        tooltipDetail: "Open app configuration.",
    });
});
