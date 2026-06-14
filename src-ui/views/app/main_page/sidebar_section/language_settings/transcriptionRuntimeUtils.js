const DEFAULT_COMPUTE_TYPE_ORDER = [
    "auto",
    "int8",
    "int8_bfloat16",
    "int8_float16",
    "int8_float32",
    "bfloat16",
    "float16",
    "int16",
    "float32",
];

const ENGINE_DEVICE_RULES = {
    "Google": ["cpu"],
    "Whisper": ["cpu", "cuda"],
    "Parakeet": ["cuda"],
    "Vosk": ["cpu"],
    "SenseVoice": ["cpu"],
};

const AUTO_ONLY_ENGINES = new Set(["Google", "Parakeet", "Vosk", "SenseVoice"]);

export const getAllowedTranscriptionDeviceModes = (engine) => {
    return ENGINE_DEVICE_RULES[engine] ?? ["cpu"];
};

export const filterDeviceMapByEngine = (deviceMap = {}, engine) => {
    const allowedModes = new Set(getAllowedTranscriptionDeviceModes(engine));

    return Object.entries(deviceMap).reduce((acc, [key, value]) => {
        if (allowedModes.has(value.device)) {
            acc[key] = value;
        }
        return acc;
    }, {});
};

export const getSelectedDeviceMode = (selectedDevice) => {
    return selectedDevice?.device ?? "cpu";
};

export const findFirstDeviceForMode = (deviceMap = {}, mode) => {
    return Object.values(deviceMap).find((device) => device.device === mode) ?? null;
};

export const sortTranscriptionComputeTypes = (computeTypes = []) => {
    const existingTypes = new Set(computeTypes);
    return DEFAULT_COMPUTE_TYPE_ORDER.filter((id) => existingTypes.has(id));
};

export const getAllowedTranscriptionComputeTypes = ({ engine, device }) => {
    if (AUTO_ONLY_ENGINES.has(engine)) {
        return ["auto"];
    }

    return sortTranscriptionComputeTypes(device?.compute_types ?? ["auto"]);
};

export const isAutoOnlyTranscriptionEngine = (engine) => {
    return AUTO_ONLY_ENGINES.has(engine);
};

export const getQuickDeviceOptions = (deviceMap = {}, engine) => {
    return getAllowedTranscriptionDeviceModes(engine).map((mode) => ({
        id: mode,
        label: mode === "cuda" ? "GPU" : "CPU",
        device: findFirstDeviceForMode(deviceMap, mode),
        disabled: findFirstDeviceForMode(deviceMap, mode) == null,
    }));
};
