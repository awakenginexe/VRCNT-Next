import test from "node:test";
import assert from "node:assert/strict";

import { getMainFunctionTooltipMeta, mainFunctionTooltipOrder } from "../mainFunctionTooltipMeta.js";

test("main sidebar controls have short tooltip text", () => {
    for (const controlId of mainFunctionTooltipOrder) {
        const meta = getMainFunctionTooltipMeta(controlId);
        assert.ok(meta.tooltipTitleKey.startsWith("main_page.main_function_tooltips."));
        assert.ok(meta.tooltipDetailKey.startsWith("main_page.main_function_tooltips."));
    }
});

test("settings tooltip explains it opens configuration", () => {
    assert.deepEqual(getMainFunctionTooltipMeta("settings"), {
        tooltipTitleKey: "main_page.main_function_tooltips.settings_title",
        tooltipDetailKey: "main_page.main_function_tooltips.settings_detail",
    });
});
