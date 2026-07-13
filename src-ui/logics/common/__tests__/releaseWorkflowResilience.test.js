import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";


const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const workflow = fs.readFileSync(
    path.join(repoRoot, ".github/workflows/release.yml"),
    "utf8",
);


test("release workflow can republish an existing tag without moving it", () => {
    assert.match(workflow, /^\s{2}workflow_dispatch:\s*$/m);
    assert.match(workflow, /^\s{6}release_tag:\s*$/m);
    assert.match(workflow, /INPUT_RELEASE_TAG/);
    assert.match(
        workflow,
        /RELEASE_TAG=\$tag.*GITHUB_ENV/,
        "resolved manual tags must use the same release pipeline",
    );
});


test("release workflow retries external Python dependency failures", () => {
    assert.match(workflow, /\$maxAttempts\s*=\s*4/);
    assert.match(
        workflow,
        /for \(\$attempt = 1; \$attempt -le \$maxAttempts; \$attempt\+\+\)/,
    );
    assert.match(workflow, /npm run setup-python/);
    assert.match(workflow, /30 \* \[Math\]::Pow\(2, \$attempt - 1\)/);
    assert.match(workflow, /Start-Sleep -Seconds \$delaySeconds/);
    assert.match(workflow, /failed after \$maxAttempts attempts/);
});


test("release workflow retries transient Hugging Face upload failures", () => {
    assert.match(workflow, /\$uploadMaxAttempts\s*=\s*4/);
    assert.match(
        workflow,
        /for \(\$uploadAttempt = 1; \$uploadAttempt -le \$uploadMaxAttempts; \$uploadAttempt\+\+\)/,
    );
    assert.match(workflow, /\$script \| python -/);
    assert.match(workflow, /Hugging Face upload attempt \$uploadAttempt failed/);
    assert.match(workflow, /Hugging Face upload failed after \$uploadMaxAttempts attempts/);
});
