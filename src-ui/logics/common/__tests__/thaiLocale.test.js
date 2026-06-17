import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import yaml from "js-yaml";
import { ui_configs } from "../../ui_configs.js";
import {
    getThaiPreferredFontFamily,
    shouldApplyThaiPreferredFont,
    THAI_PREFERRED_FONT_FAMILY,
    THAI_UI_LANGUAGE_ID,
} from "../thaiFontPreference.js";

const rootDir = path.resolve(import.meta.dirname, "../../../..");

const flattenKeys = (value, prefix = "") => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        return Object.entries(value).flatMap(([key, childValue]) => (
            flattenKeys(childValue, prefix ? `${prefix}.${key}` : key)
        ));
    }
    return [prefix];
};

test("Thai locale has the same translation keys as English", () => {
    const englishLocale = yaml.load(fs.readFileSync(path.join(rootDir, "locales/en.yml"), "utf8"));
    const thaiLocale = yaml.load(fs.readFileSync(path.join(rootDir, "locales/th.yml"), "utf8"));

    assert.deepEqual(
        flattenKeys(thaiLocale).sort(),
        flattenKeys(englishLocale).sort(),
    );
});

test("Thai is selectable as a UI language", () => {
    assert.deepEqual(
        ui_configs.selectable_ui_languages.find((language) => language.id === THAI_UI_LANGUAGE_ID),
        { id: THAI_UI_LANGUAGE_ID, label: "ไทย" },
    );
});

test("Thai UI prefers Itim when the font is installed", () => {
    const fontFamilyList = {
        "Segoe UI": "Segoe UI",
        Itim: THAI_PREFERRED_FONT_FAMILY,
    };

    assert.equal(getThaiPreferredFontFamily(fontFamilyList), THAI_PREFERRED_FONT_FAMILY);
    assert.equal(shouldApplyThaiPreferredFont({
        uiLanguage: THAI_UI_LANGUAGE_ID,
        selectedFontFamily: "Yu Gothic UI",
        fontFamilyList,
    }), true);
});

test("Thai UI keeps the current font when Itim is missing", () => {
    assert.equal(getThaiPreferredFontFamily({ "Yu Gothic UI": "Yu Gothic UI" }), null);
    assert.equal(shouldApplyThaiPreferredFont({
        uiLanguage: THAI_UI_LANGUAGE_ID,
        selectedFontFamily: "Yu Gothic UI",
        fontFamilyList: { "Yu Gothic UI": "Yu Gothic UI" },
    }), false);
});

test("Thai UI handles an unloaded font list", () => {
    assert.equal(getThaiPreferredFontFamily(null), null);
    assert.equal(shouldApplyThaiPreferredFont({
        uiLanguage: THAI_UI_LANGUAGE_ID,
        selectedFontFamily: "Yu Gothic UI",
        fontFamilyList: null,
    }), false);
});
