import clsx from "clsx";
import styles from "./TranslatorSelector.module.scss";
import { useI18n } from "@useI18n";

import { chunkArray } from "@utils";
import { useStore_IsOpenedTranslatorSelector } from "@store";
import { useLanguageSettings } from "@logics_main";

const normalizeSelectedIds = (selected_ids) => (
    Array.isArray(selected_ids)
        ? selected_ids.filter(Boolean)
        : [selected_ids].filter(Boolean)
);

const findFallbackSecondaryId = (translation_engines, primary_id, current_secondary_id) => {
    const isAvailableSecondary = (engine) => (
        engine?.is_available === true &&
        engine.id !== primary_id
    );
    const current_secondary = translation_engines.find(engine => engine.id === current_secondary_id);
    if (isAvailableSecondary(current_secondary)) return current_secondary.id;

    const cloud_secondary = translation_engines.find(
        engine => isAvailableSecondary(engine) && engine.is_default !== true
    );
    if (cloud_secondary) return cloud_secondary.id;

    return translation_engines.find(isAvailableSecondary)?.id;
};

export const TranslatorSelector = ({selected_ids, translation_engines, is_selected_same_language}) => {
    const { t } = useI18n();
    const columns = chunkArray(translation_engines, 2);
    const selectedIds = normalizeSelectedIds(selected_ids);
    const primary_id = selectedIds[0] ?? "CTranslate2";
    const secondary_id = selectedIds[1];
    const parallel_enabled = selectedIds.length > 1;

    return (
        <div className={styles.container}>
            <div className={styles.relative_container}>
                <ParallelTranslationControls
                    primary_id={primary_id}
                    secondary_id={secondary_id}
                    selected_ids={selectedIds}
                    translation_engines={translation_engines}
                />
                <div className={styles.wrapper}>
                    {columns.map((column, column_index) => (
                        <div className={styles.column_wrapper} key={`column_${column_index}`}>
                            {column.map(({ id, label, is_available, is_default }) => (
                                <TranslatorBox
                                    key={id}
                                    id={id}
                                    label={label}
                                    is_available={is_available}
                                    is_default={is_default}
                                    is_primary_selected={(id === primary_id)}
                                    is_secondary_selected={parallel_enabled && (id === secondary_id)}
                                    selected_ids={selectedIds}
                                    translation_engines={translation_engines}
                                />
                            ))}
                        </div>
                    ))}
                </div>
                {is_selected_same_language ?
                    <div className={styles.is_selected_same_language_wrapper}>
                        <p className={styles.is_selected_same_language_text}>
                            {t("main_page.translator_selector.is_selected_same_language", {
                                your_language: t("main_page.your_language"),
                                target_language: t("main_page.target_language"),
                                ctranslate2: "CTranslate2",
                            })}
                        </p>
                    </div>
                : null
                }
            </div>
        </div>
    );
};

const ParallelTranslationControls = ({primary_id, secondary_id, selected_ids, translation_engines}) => {
    const { t } = useI18n();
    const { setSelectedTranslationEngines} = useLanguageSettings();
    const parallel_enabled = selected_ids.length > 1;
    const fallback_secondary_id = findFallbackSecondaryId(translation_engines, primary_id, secondary_id);
    const can_use_parallel = Boolean(fallback_secondary_id);
    const selected_secondary_id = fallback_secondary_id ?? "";
    const secondary_options = translation_engines.filter(
        engine => engine.is_available === true && engine.id !== primary_id
    );

    const toggleParallelService = (event) => {
        if (event.target.checked && fallback_secondary_id) {
            setSelectedTranslationEngines([primary_id, fallback_secondary_id]);
        } else {
            setSelectedTranslationEngines(primary_id);
        }
    };

    const selectSecondaryTranslator = (event) => {
        const next_secondary_id = event.target.value;
        if (next_secondary_id) {
            setSelectedTranslationEngines([primary_id, next_secondary_id]);
        }
    };

    return (
        <div className={styles.parallel_controls}>
            <label className={styles.parallel_toggle}>
                <div className={styles.parallel_checkbox_wrapper}>
                    <input
                        className={styles.parallel_checkbox}
                        type="checkbox"
                        checked={parallel_enabled && can_use_parallel}
                        disabled={!can_use_parallel}
                        onChange={toggleParallelService}
                    />
                    <span className={styles.checkbox_slider} />
                </div>
                <span>{t("main_page.translator_selector.use_parallel_service")}</span>
            </label>
            {parallel_enabled && can_use_parallel ? (
                <div className={styles.second_selector_wrapper}>
                    <label className={styles.second_selector_label}>
                        {t("main_page.translator_selector.second_translator")}
                    </label>
                    <div className={styles.second_selector_container}>
                        <select
                            className={styles.second_selector}
                            value={selected_secondary_id}
                            onChange={selectSecondaryTranslator}
                        >
                            {secondary_options.map(engine => (
                                <option key={engine.id} value={engine.id}>{engine.label.replace("\n", " ")}</option>
                            ))}
                        </select>
                        <div className={styles.dropdown_arrow}>
                            <svg viewBox="0 0 24 24">
                                <path d="M7 10l5 5 5-5H7z" />
                            </svg>
                        </div>
                    </div>
                </div>
            ) : null}
        </div>
    );
};

const TranslatorBox = (props) => {
    const { t } = useI18n();
    const { setSelectedTranslationEngines} = useLanguageSettings();
    const { updateIsOpenedTranslatorSelector} = useStore_IsOpenedTranslatorSelector();
    const parallel_enabled = props.selected_ids.length > 1;

    const box_class_name = clsx(
        styles.box,
        { [styles.is_primary]: props.is_primary_selected },
        { [styles.is_secondary]: props.is_secondary_selected },
        { [styles.is_available]: props.is_available }
    );
    const label_default_class_name = clsx(
        styles.label_default,
        { [styles.is_primary]: props.is_primary_selected },
        { [styles.is_secondary]: props.is_secondary_selected },
    );

    const selectTranslator = () => {
        const parallel_enabled = props.selected_ids.length > 1;
        if (parallel_enabled) {
            const secondary_id = findFallbackSecondaryId(
                props.translation_engines,
                props.id,
                props.selected_ids[1],
            );
            setSelectedTranslationEngines(
                secondary_id ? [props.id, secondary_id] : props.id
            );
            return;
        }
        if (props.is_primary_selected === false) {
            setSelectedTranslationEngines(props.id);
        }
        updateIsOpenedTranslatorSelector(false);
    };

    return (
        <div className={box_class_name} onClick={selectTranslator}>
            {parallel_enabled && props.is_primary_selected && (
                <span className={clsx(styles.badge, styles.primary_badge)}>
                    {t("main_page.translator_selector.primary_badge")}
                </span>
            )}
            {parallel_enabled && props.is_secondary_selected && (
                <span className={clsx(styles.badge, styles.secondary_badge)}>
                    {t("main_page.translator_selector.secondary_badge")}
                </span>
            )}
            <p className={styles.translator_name}>{props.label}</p>
            {props.is_default && <p className={label_default_class_name}>{t("main_page.translator_label_default")}</p>}
        </div>
    );
};
