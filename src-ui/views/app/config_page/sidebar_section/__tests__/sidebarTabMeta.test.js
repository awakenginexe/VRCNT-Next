import test from "node:test";
import assert from "node:assert/strict";

import { getSidebarTabMeta, sidebarTabOrder } from "../sidebarTabMeta.js";

test("all sidebar tabs have short tooltip titles and details", () => {
    for (const tabId of sidebarTabOrder) {
        const meta = getSidebarTabMeta(tabId);
        assert.ok(meta.tooltipTitle.length > 0);
        assert.ok(meta.tooltipDetail.length > 0);
        assert.ok(meta.tooltipTitle.length <= 24);
        assert.ok(meta.tooltipDetail.length <= 56);
    }
});

test("credit tab points users to original VRCT credit", () => {
    assert.deepEqual(getSidebarTabMeta("supporters"), {
        label: "Credit",
        tooltipTitle: "Credit",
        tooltipDetail: "View original VRCT project credit.",
    });
});
