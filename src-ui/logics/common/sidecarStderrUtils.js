const ANSI_ESCAPE_PATTERN = /\x1b\[[0-?]*[ -/]*[@-~]/g;
const ERROR_PATTERN = /\b(traceback|exception|error|failed|fatal)\b/i;

export const normalizeSidecarStderr = (line) => {
    return String(line ?? "").replace(ANSI_ESCAPE_PATTERN, "").trim();
};

export const isBenignSidecarStderr = (line) => {
    const text = normalizeSidecarStderr(line);
    if (!text) return true;
    if (ERROR_PATTERN.test(text)) return false;

    const hasTqdmPercentBar = /\d+%\|/.test(text);
    const hasTqdmCounter = /\|\s*\d+\/\d+\s*\[/.test(text);
    const isHuggingFaceFileFetch = /^Fetching\s+\d+\s+files?:/i.test(text);

    return (hasTqdmPercentBar && hasTqdmCounter) || isHuggingFaceFileFetch;
};
