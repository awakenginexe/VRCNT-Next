import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";


const repoRoot = path.resolve(import.meta.dirname, "../../../..");
const workflow = fs.readFileSync(
    path.join(repoRoot, ".github/workflows/release.yml"),
    "utf8",
);
const pythonInstaller = fs.readFileSync(
    path.join(repoRoot, "bat/install.bat"),
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


test("release workflow prefetches and verifies the pinned CUDA wheel through hf-xet", () => {
    assert.match(workflow, /Prefetch pinned CUDA wheel/);
    assert.match(workflow, /hf_hub_download/);
    assert.match(workflow, /HF_XET_HIGH_PERFORMANCE/);
    assert.match(
        workflow,
        /d05cffbd8df3d69f33b6665afcfefaf05d42e1c73038b2c6ba8118cfeac2a88e/,
    );
    assert.match(workflow, /VRCNT_CUDA_WHEEL_PATH=.*GITHUB_ENV/);
});


test("release tooling keeps huggingface_hub and hf-xet on a compatible pair", () => {
    assert.match(workflow, /huggingface_hub==0\.34\.4 hf-xet==1\.1\.8/);
    assert.equal(
        workflow.match(/hf-xet==1\.1\.8/g)?.length,
        2,
        "prefetch and upload tooling must install the same tested hf-xet version",
    );
    assert.doesNotMatch(workflow, /hf-xet==1\.1\.2/);
});


test("Python setup deterministically installs an explicitly prefetched CUDA wheel", () => {
    assert.match(pythonInstaller, /if defined VRCNT_CUDA_WHEEL_PATH/);
    assert.match(
        pythonInstaller,
        /pip install --no-cache-dir --force-reinstall "%VRCNT_CUDA_WHEEL_PATH%" -r requirements_cuda\.txt/,
    );
});
