import test from "node:test";
import assert from "node:assert/strict";
import { isBenignSidecarStderr } from "../sidecarStderrUtils.js";

test("isBenignSidecarStderr ignores Hugging Face download progress", () => {
    assert.equal(
        isBenignSidecarStderr("Fetching 2 files:  50%|#####     | 1/2 [00:11<00:11, 11.38s/it]\r"),
        true,
    );
});

test("isBenignSidecarStderr reports real tracebacks", () => {
    assert.equal(
        isBenignSidecarStderr("Traceback (most recent call last):\nRuntimeError: failed"),
        false,
    );
});
