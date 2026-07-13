import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const componentRoot = (
    "src-ui/views/app/main_page/main_section/message_container/log_box/message_container"
);

const readSource = (relativePath) => (
    fs.readFileSync(path.join(repoRoot, relativePath), "utf8")
);

test("message rows always render original text and stable translation slots", () => {
    const source = readSource(`${componentRoot}/MessageContainer.jsx`);

    assert.match(source, /<MessageText\s+item=\{messages\.original\}\s*\/>/);
    assert.match(source, /messages\.translations\.map\(\(entry\)\s*=>/);
    assert.match(source, /<TranslationEntry\s+key=\{entry\.target_slot\}\s+entry=\{entry\}\s*\/>/);
    assert.doesNotMatch(source, /key=\{(?:idx|index)\}/);
});

test("pending translation slots render status without requiring translated text", () => {
    const relativePath = `${componentRoot}/translation_entry/TranslationEntry.jsx`;
    assert.equal(
        fs.existsSync(path.join(repoRoot, relativePath)),
        true,
        "TranslationEntry.jsx must render progressive translation states",
    );

    const source = readSource(relativePath);

    assert.match(source, /getTranslationPresentation\(entry,\s*nowMs\)/);
    assert.match(source, /TRANSLATION_ACTIVE_STATUSES\.has\(entry\?\.status\)/);
    assert.match(source, /setInterval\([\s\S]*?,\s*250\)/);
    assert.match(source, /clearInterval\(/);
    assert.match(source, /entry\?\.message\s*!=\s*null\s*&&[\s\S]*?<MessageText\s+item=\{entry\}/);
    assert.doesNotMatch(source, /if\s*\(\s*!entry\?\.message\s*\)\s*return null/);
    assert.doesNotMatch(source, /updateMessageLogs|useAtom|useSetAtom|jotai/i);
});

test("message text defensively preserves ruby and Hepburn rendering", () => {
    const relativePath = `${componentRoot}/MessageText.jsx`;
    assert.equal(
        fs.existsSync(path.join(repoRoot, relativePath)),
        true,
        "MessageText.jsx must own defensive transliteration rendering",
    );

    const source = readSource(relativePath);

    assert.match(source, /const transliteration = item\?\.transliteration \?\? \[\];/);
    assert.match(source, /const message = item\?\.message \?\? "";/);
    assert.match(source, /<ruby/);
    assert.match(source, /title=\{hepburn\}/);
});
