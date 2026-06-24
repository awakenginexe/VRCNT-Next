export const isTauriRuntime = () => (
    typeof window !== "undefined" &&
    Boolean(window.__TAURI_INTERNALS__)
);

const noopAsync = async () => {};
const noopDisposeAsync = async () => {};

export const createBrowserPreviewWindow = () => ({
    outerPosition: async () => ({ x: 0, y: 0 }),
    outerSize: async () => ({
        width: window.innerWidth,
        height: window.innerHeight,
    }),
    innerSize: async () => ({
        width: window.innerWidth,
        height: window.innerHeight,
    }),
    isMinimized: async () => false,
    isMaximized: async () => false,
    setPosition: noopAsync,
    setSize: noopAsync,
    setAlwaysOnTop: noopAsync,
    startDragging: noopAsync,
    maximize: noopAsync,
    unmaximize: noopAsync,
    minimize: noopAsync,
    unminimize: noopAsync,
    setFocus: noopAsync,
    close: noopAsync,
    listen: noopDisposeAsync,
    onMoved: noopDisposeAsync,
    onResized: async (callback) => {
        window.addEventListener("resize", callback);
        return () => window.removeEventListener("resize", callback);
    },
});
