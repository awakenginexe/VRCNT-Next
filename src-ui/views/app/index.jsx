import React from "react";
import ReactDOM from "react-dom/client";
import "@root/locales/config.js";
import "./_index_css/root.css";
import "flag-icons/css/flag-icons.min.css";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { createBrowserPreviewWindow, isTauriRuntime } from "@logics_common/tauriRuntime.js";
import { isDesktopOverlayRoute } from "@logics_common/desktopOverlayWindow.js";

import { store } from "@store";

store.appWindow = isTauriRuntime()
    ? getCurrentWindow()
    : createBrowserPreviewWindow();

import { App } from "./App";
import { DesktopOverlayApp } from "./desktop_overlay/DesktopOverlayApp";

const RootApp = isDesktopOverlayRoute()
    ? DesktopOverlayApp
    : App;

ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
        <RootApp />
    </React.StrictMode>,
);
