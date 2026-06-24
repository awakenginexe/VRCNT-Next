import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
    DESKTOP_OVERLAY_WINDOW_LABEL,
    buildDesktopOverlayRoute,
    buildDesktopOverlayWindowOptions,
    openDesktopOverlayWindow,
} from "../desktopOverlayWindow.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "../../../..");

test("desktop overlay window options create a separate frosted utility window", () => {
    assert.equal(DESKTOP_OVERLAY_WINDOW_LABEL, "desktop-overlay");
    assert.equal(buildDesktopOverlayRoute(), "index.html?window=desktop-overlay");

    assert.deepEqual(buildDesktopOverlayWindowOptions(), {
        url: "index.html?window=desktop-overlay",
        title: "VRCNT-Next Desktop Overlay",
        width: 520,
        height: 240,
        minWidth: 360,
        minHeight: 160,
        decorations: false,
        transparent: true,
        shadow: false,
        resizable: true,
        alwaysOnTop: true,
        skipTaskbar: false,
        visible: true,
        center: true,
        focus: true,
    });
});

test("opening desktop overlay focuses an existing overlay before creating a new one", async () => {
    const calls = [];
    const existingWindow = {
        async unminimize() {
            calls.push("unminimize");
        },
        async setFocus() {
            calls.push("setFocus");
        },
    };

    const result = await openDesktopOverlayWindow({
        isTauri: true,
        WebviewWindow: {
            async getByLabel(label) {
                calls.push(["getByLabel", label]);
                return existingWindow;
            },
        },
    });

    assert.equal(result, existingWindow);
    assert.deepEqual(calls, [
        ["getByLabel", "desktop-overlay"],
        "unminimize",
        "setFocus",
    ]);
});

test("opening desktop overlay creates the utility window when no overlay exists", async () => {
    const calls = [];

    class FakeWebviewWindow {
        constructor(label, options) {
            calls.push(["create", label, options]);
            this.label = label;
            this.options = options;
        }

        static async getByLabel(label) {
            calls.push(["getByLabel", label]);
            return null;
        }
    }

    const result = await openDesktopOverlayWindow({
        isTauri: true,
        WebviewWindow: FakeWebviewWindow,
    });

    assert.equal(result.label, "desktop-overlay");
    assert.equal(result.options.url, "index.html?window=desktop-overlay");
    assert.equal(result.options.alwaysOnTop, true);
    assert.deepEqual(calls[0], ["getByLabel", "desktop-overlay"]);
    assert.equal(calls[1][0], "create");
});

test("opening desktop overlay rejects when Tauri reports a creation error", async () => {
    class FailingWebviewWindow {
        constructor(label, options) {
            this.label = label;
            this.options = options;
            this.listeners = new Map();
            queueMicrotask(() => {
                const listener = this.listeners.get("tauri://error");
                listener?.({ payload: "permission denied" });
            });
        }

        static async getByLabel() {
            return null;
        }

        async once(eventName, listener) {
            this.listeners.set(eventName, listener);
            return async () => this.listeners.delete(eventName);
        }
    }

    await assert.rejects(
        openDesktopOverlayWindow({
            isTauri: true,
            WebviewWindow: FailingWebviewWindow,
        }),
        /permission denied/,
    );
});

test("tauri capabilities allow the main window to create the desktop overlay window", async () => {
    const capabilityPath = resolve(repoRoot, "src-tauri/capabilities/vrct_capability.json");
    const capability = JSON.parse(await readFile(capabilityPath, "utf8"));

    assert.ok(capability.windows.includes("main"));
    assert.ok(capability.windows.includes("desktop-overlay"));
    assert.ok(capability.permissions.includes("core:webview:allow-create-webview-window"));
});
