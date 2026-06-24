import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import { useI18n } from "@useI18n";
import {
    DESKTOP_OVERLAY_CHANNEL,
    readDesktopOverlayPayload,
    createDesktopOverlayPayload,
} from "@logics_common";
import { store, useStore_MessageLogs } from "@store";
import ConfigurationSvg from "@images/configuration.svg?react";
import ForegroundSvg from "@images/foreground.svg?react";
import XMarkSvg from "@images/x_mark.svg?react";
import styles from "./DesktopOverlayApp.module.scss";

const THEME_ACCENT_CLASSES = [
    "theme-neon-cyan",
    "theme-midnight-purple",
    "theme-emerald-green",
    "theme-sakura-pink",
];

const DESKTOP_OVERLAY_SETTINGS_KEY = "vrcnt-next-desktop-overlay-settings";

const DEFAULT_OVERLAY_SETTINGS = {
    pinned: true,
    opacity: 92,
    scale: 100,
    translationsOnly: false,
    expanded: true,
};

const readOverlaySettings = () => {
    try {
        const rawSettings = localStorage.getItem(DESKTOP_OVERLAY_SETTINGS_KEY);
        return rawSettings
            ? { ...DEFAULT_OVERLAY_SETTINGS, ...JSON.parse(rawSettings) }
            : DEFAULT_OVERLAY_SETTINGS;
    } catch (error) {
        console.warn("Unable to read desktop overlay settings.", error);
        return DEFAULT_OVERLAY_SETTINGS;
    }
};

export const DesktopOverlayApp = () => {
    const { t, i18n } = useI18n();
    const { currentMessageLogs } = useStore_MessageLogs();
    const [payload, setPayload] = useState(() => (
        readDesktopOverlayPayload() ?? createDesktopOverlayPayload({ messageLogs: currentMessageLogs.data })
    ));
    const [settings, setSettings] = useState(readOverlaySettings);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);

    useEffect(() => {
        document.documentElement.classList.add(styles.desktop_overlay_root);
        document.body.classList.add(styles.desktop_overlay_body);

        const savedTheme = localStorage.getItem("theme_accent") || "theme-neon-cyan";
        document.documentElement.classList.remove(...THEME_ACCENT_CLASSES);
        document.documentElement.classList.add(
            THEME_ACCENT_CLASSES.includes(savedTheme) ? savedTheme : "theme-neon-cyan"
        );

        return () => {
            document.documentElement.classList.remove(styles.desktop_overlay_root);
            document.body.classList.remove(styles.desktop_overlay_body);
        };
    }, []);

    useEffect(() => {
        try {
            localStorage.setItem(DESKTOP_OVERLAY_SETTINGS_KEY, JSON.stringify(settings));
            store.appWindow?.setAlwaysOnTop?.(settings.pinned === true);
        } catch (error) {
            console.warn("Unable to update desktop overlay settings.", error);
        }
    }, [settings]);

    useEffect(() => {
        if (payload?.uiLanguage) i18n.changeLanguage(payload.uiLanguage);
    }, [i18n, payload?.uiLanguage]);

    useEffect(() => {
        try {
            const channel = new BroadcastChannel(DESKTOP_OVERLAY_CHANNEL);
            channel.onmessage = (event) => setPayload(event.data);
            return () => channel.close();
        } catch (error) {
            console.warn("Unable to listen for desktop overlay payload.", error);
            return undefined;
        }
    }, []);

    useEffect(() => {
        const intervalId = setInterval(() => {
            const storedPayload = readDesktopOverlayPayload();
            if (storedPayload) setPayload(storedPayload);
        }, 600);

        return () => clearInterval(intervalId);
    }, []);

    const visibleLogs = useMemo(() => {
        const logs = payload?.messageLogs ?? [];
        return settings.expanded ? logs.slice(-3) : logs.slice(-1);
    }, [payload, settings.expanded]);

    const startDragging = (event) => {
        if (event.button !== 0) return;
        if (event.target.closest("button, input, label")) return;
        store.appWindow?.startDragging?.();
    };

    const updateSetting = (key, value) => {
        setSettings((currentSettings) => ({
            ...currentSettings,
            [key]: value,
        }));
    };

    const closeOverlay = () => {
        store.appWindow?.close?.();
    };

    return (
        <div
            className={styles.overlay_shell}
            style={{
                "--desktop-overlay-opacity": `${settings.opacity / 100}`,
                "--desktop-overlay-scale": `${settings.scale / 100}`,
            }}
            onMouseDown={startDragging}
        >
            <section className={styles.overlay_panel}>
                <header className={styles.header}>
                    <div className={styles.title_group}>
                        <p className={styles.eyebrow}>VRCNT-Next</p>
                        <h1 className={styles.title}>{t("main_page.desktop_overlay.title")}</h1>
                    </div>
                    <div className={styles.header_controls}>
                        <button
                            className={clsx(styles.icon_button, { [styles.is_active]: settings.pinned })}
                            onClick={() => updateSetting("pinned", !settings.pinned)}
                            aria-label={settings.pinned
                                ? t("main_page.desktop_overlay.unpin")
                                : t("main_page.desktop_overlay.pin")}
                        >
                            <ForegroundSvg className={styles.icon} />
                        </button>
                        <button
                            className={clsx(styles.icon_button, { [styles.is_active]: isSettingsOpen })}
                            onClick={() => setIsSettingsOpen(!isSettingsOpen)}
                            aria-label={t("main_page.desktop_overlay.settings")}
                        >
                            <ConfigurationSvg className={styles.icon} />
                        </button>
                        <button
                            className={styles.icon_button}
                            onClick={closeOverlay}
                            aria-label={t("main_page.desktop_overlay.close")}
                        >
                            <XMarkSvg className={styles.icon} />
                        </button>
                    </div>
                </header>

                <StatusStrip statuses={payload?.statuses} />

                <div className={styles.log_stack}>
                    {visibleLogs.length > 0 ? (
                        visibleLogs.map((log) => (
                            <OverlayMessage
                                key={log.id ?? `${log.category}-${log.created_at}`}
                                log={log}
                                translationsOnly={settings.translationsOnly}
                            />
                        ))
                    ) : (
                        <div className={styles.empty_state}>
                            {t("main_page.desktop_overlay.waiting")}
                        </div>
                    )}
                </div>

                {isSettingsOpen && (
                    <div className={styles.settings_panel}>
                        <p className={styles.settings_title}>{t("main_page.desktop_overlay.settings_title")}</p>
                        <RangeSetting
                            label={t("config_page.vr.opacity")}
                            value={settings.opacity}
                            min={45}
                            max={100}
                            step={5}
                            suffix="%"
                            onChange={(value) => updateSetting("opacity", value)}
                        />
                        <RangeSetting
                            label={t("config_page.vr.ui_scaling")}
                            value={settings.scale}
                            min={80}
                            max={130}
                            step={5}
                            suffix="%"
                            onChange={(value) => updateSetting("scale", value)}
                        />
                        <ToggleSetting
                            label={t("main_page.desktop_overlay.translations_only")}
                            checked={settings.translationsOnly}
                            onChange={(checked) => updateSetting("translationsOnly", checked)}
                        />
                        <ToggleSetting
                            label={t("main_page.desktop_overlay.expanded_view")}
                            checked={settings.expanded}
                            onChange={(checked) => updateSetting("expanded", checked)}
                        />
                    </div>
                )}
            </section>
        </div>
    );
};

const StatusStrip = ({ statuses = {} }) => {
    const { t } = useI18n();
    const statusItems = [
        ["translationEnabled", t("main_page.translation")],
        ["speakingEnabled", t("main_page.transcription_send")],
        ["listeningEnabled", t("main_page.transcription_receive")],
    ];

    return (
        <div className={styles.status_strip}>
            {statusItems.map(([key, label]) => (
                <div
                    key={key}
                    className={clsx(styles.status_pill, {
                        [styles.is_active]: statuses[key] === true,
                    })}
                >
                    <span className={styles.status_dot}></span>
                    <span>{label}</span>
                </div>
            ))}
        </div>
    );
};

const OverlayMessage = ({ log, translationsOnly }) => {
    const { t } = useI18n();
    const originalMessage = log?.messages?.original?.message ?? "";
    const translations = (log?.messages?.translations ?? [])
        .map((translation) => translation.message)
        .filter(Boolean);
    const shouldShowOriginal = translationsOnly !== true && originalMessage;

    return (
        <article className={clsx(styles.message_card, styles[log.category] ?? styles.system)}>
            <div className={styles.message_meta}>
                <span>{t(`main_page.message_log.${log.category}`, { defaultValue: log.category })}</span>
                {log.created_at && <span>{log.created_at}</span>}
            </div>
            {shouldShowOriginal && (
                <p className={styles.original_message}>{originalMessage}</p>
            )}
            {translations.length > 0 ? (
                translations.map((message, index) => (
                    <p key={`${log.id}-translation-${index}`} className={styles.translated_message}>{message}</p>
                ))
            ) : (
                <p className={styles.no_translation}>{t("main_page.desktop_overlay.no_translation")}</p>
            )}
        </article>
    );
};

const RangeSetting = ({ label, value, min, max, step, suffix, onChange }) => (
    <label className={styles.range_setting}>
        <span className={styles.setting_label}>{label}</span>
        <input
            className={styles.range_input}
            type="range"
            value={value}
            min={min}
            max={max}
            step={step}
            onChange={(event) => onChange(Number(event.target.value))}
        />
        <span className={styles.setting_value}>{value}{suffix}</span>
    </label>
);

const ToggleSetting = ({ label, checked, onChange }) => (
    <label className={styles.toggle_setting}>
        <span className={styles.setting_label}>{label}</span>
        <input
            className={styles.checkbox}
            type="checkbox"
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
        />
        <span className={styles.toggle_visual}></span>
    </label>
);
