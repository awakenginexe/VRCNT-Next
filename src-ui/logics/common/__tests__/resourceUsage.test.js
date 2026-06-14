import test from "node:test";
import assert from "node:assert/strict";
import {
    formatResourceMetric,
    normalizeGpuMonitorSelection,
} from "../resourceUsageUtils.js";

test("formatResourceMetric renders current percentage when available", () => {
    assert.equal(formatResourceMetric({ available: true, percent: 42.34 }), "42.3%");
});

test("formatResourceMetric renders unavailable without fake values", () => {
    assert.equal(formatResourceMetric({ available: false, percent: null }), "Unavailable");
});

test("normalizeGpuMonitorSelection defaults to auto", () => {
    assert.deepEqual(normalizeGpuMonitorSelection(), { mode: "auto", device_index: null });
});

test("normalizeGpuMonitorSelection keeps valid manual GPU index", () => {
    assert.deepEqual(
        normalizeGpuMonitorSelection({ mode: "manual", device_index: 2 }),
        { mode: "manual", device_index: 2 },
    );
});

test("normalizeGpuMonitorSelection falls back to auto for malformed manual index", () => {
    assert.deepEqual(
        normalizeGpuMonitorSelection({ mode: "manual", device_index: "bad" }),
        { mode: "auto", device_index: null },
    );
});
