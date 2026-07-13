import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const selectorPath = path.join(
    repoRoot,
    "src-ui/views/app/main_page/sidebar_section/language_settings/translator_selector_open_button/translator_selector/TranslatorSelector.jsx",
);

test("online primary providers cannot select CTranslate2 as secondary", () => {
    const source = fs.readFileSync(selectorPath, "utf8");

    assert.match(
        source,
        /const\s+canBeSecondary\s*=\s*\(engine,\s*primary_id\)\s*=>/,
    );
    assert.match(
        source,
        /primary_id\s*===\s*["']CTranslate2["']\s*\|\|\s*engine\.id\s*!==\s*["']CTranslate2["']/,
    );
    assert.match(
        source,
        /translation_engines\.filter\(\s*engine\s*=>\s*canBeSecondary\(engine,\s*primary_id\)\s*\)/,
    );
    assert.ok(
        (source.match(/canBeSecondary\(/g) ?? []).length >= 3,
        "automatic and explicit secondary choices must share one policy",
    );
});
