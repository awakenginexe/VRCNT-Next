import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const componentPath = (
    "src-ui/views/app/others/blocking_operation_overlay/"
    + "BlockingOperationOverlay.jsx"
);
const stylesheetPath = (
    "src-ui/views/app/others/blocking_operation_overlay/"
    + "BlockingOperationOverlay.module.scss"
);
const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

test("the blocking overlay is exported and absent from the tree while closed", () => {
    assert.equal(fs.existsSync(path.join(repoRoot, componentPath)), true);
    assert.equal(fs.existsSync(path.join(repoRoot, stylesheetPath)), true);

    const source = readSource(componentPath);
    const othersIndex = readSource("src-ui/views/app/others/index.js");
    assert.match(source, /export const BlockingOperationOverlay/);
    assert.match(source, /if \(!open\) return null;/);
    assert.match(
        othersIndex,
        /export \{ BlockingOperationOverlay \} from "\.\/blocking_operation_overlay\/BlockingOperationOverlay\.jsx";/,
    );
});

test("the dialog owns its accessible name, description, and focus lifecycle", () => {
    const source = readSource(componentPath);

    assert.match(source, /role="dialog"/);
    assert.match(source, /aria-modal="true"/);
    assert.match(source, /aria-labelledby=\{titleId\}/);
    assert.match(source, /aria-describedby=\{descriptionId\}/);
    assert.match(source, /id=\{titleId\}/);
    assert.match(source, /id=\{descriptionId\}/);
    assert.match(source, /ref=\{cardRef\}/);
    assert.match(source, /tabIndex=\{-1\}/);
    assert.match(source, /previousFocusRef\.current = document\.activeElement/);
    assert.match(source, /cardRef\.current\?\.focus\(\)/);
    assert.match(source, /previous\?\.isConnected/);
    assert.match(source, /previous\.focus\(\)/);

    const statusRegion = source.match(
        /<div\s+id=\{descriptionId\}[\s\S]*?<\/div>/,
    )?.[0] ?? "";
    assert.match(statusRegion, /role="status"/);
    assert.match(statusRegion, /aria-live="polite"/);
    assert.match(statusRegion, /aria-atomic="true"/);
    assert.match(statusRegion, /\{phase\}/);
    assert.match(statusRegion, /\{detail \?/);
    assert.doesNotMatch(statusRegion, /elapsedText/);
    assert.match(source, /className=\{styles\.elapsed\}>\{elapsedText\}/);
});

test("determinate and indeterminate progress expose distinct ARIA contracts", () => {
    const source = readSource(componentPath);

    assert.match(source, /progress\.kind === "determinate"/);
    assert.match(source, /role="progressbar"/);
    assert.match(source, /aria-label=\{progressLabel\}/);
    assert.match(source, /"aria-valuemin": 0/);
    assert.match(source, /"aria-valuemax": progress\.max/);
    assert.match(source, /"aria-valuenow": progress\.value/);
    assert.match(source, /"aria-valuetext": progressText/);
    assert.match(source, /\{\.\.\.progressAria\}/);
    assert.equal((source.match(/aria-valuenow/g) ?? []).length, 1);
    assert.match(source, /--progress-percent/);
});

test("the overlay has no user dismissal path", () => {
    const source = readSource(componentPath);

    assert.doesNotMatch(source, /CloseButton|close_button|aria-label=.*close/i);
    assert.doesNotMatch(source, /onClick|onKeyDown|onKeyUp|Escape|setOpen/);
    assert.doesNotMatch(source, /<button\b/);
});

test("responsive styling keeps reduced-motion and performance fallbacks", () => {
    const styles = readSource(stylesheetPath);

    for (const declaration of [
        /position:\s*absolute/,
        /inset:\s*0/,
        /z-index:\s*100/,
        /display:\s*grid/,
        /place-items:\s*center/,
        /background:\s*rgb\(4 8 16 \/ 72%\)/,
        /backdrop-filter:\s*blur\(18px\) saturate\(0\.8\)/,
        /width:\s*min\(42rem, calc\(100% - 3\.2rem\)\)/,
        /max-height:\s*calc\(100% - 3\.2rem\)/,
        /overflow:\s*auto/,
        /font-variant-numeric:\s*tabular-nums/,
        /width:\s*var\(--progress-percent, 100%\)/,
    ]) {
        assert.match(styles, declaration);
    }

    assert.match(styles, /@media\s*\(max-width:/);
    assert.match(styles, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    assert.match(styles, /:global\(\.performance_mode\)\s+\.overlay/);
    assert.match(styles, /:global\(\.performance_mode\)[\s\S]*?backdrop-filter:\s*none/);
    assert.match(styles, /:global\(\.performance_mode\)[\s\S]*?animation:\s*none/);
    assert.match(styles, /\.is_indeterminate/);
});
