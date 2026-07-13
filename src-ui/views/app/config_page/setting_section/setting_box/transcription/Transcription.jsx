import { useI18n } from "@useI18n";
import styles from "./Transcription.module.scss";
import { genNumObjArray } from "@utils";

import {
    useTranscription,
} from "@logics_configs";

import {
    WordFilterContainer,
    DownloadModelsContainer,
    RadioButtonContainer,
    DropdownMenuContainer,
    SliderContainer,
} from "../_templates/Templates";

import {
    SectionLabelComponent,
} from "../_components";

import { ComputeDevice } from "../_components/compute_device/ComputeDevice";
import {
    filterDeviceMapByEngine,
    getAllowedTranscriptionComputeTypes,
} from "../../../../main_page/sidebar_section/language_settings/transcriptionRuntimeUtils.js";

export const Transcription = () => {
    return (
        <div className={styles.container}>
            <Mic_Container />
            <Speaker_Container />
            <TranscriptionEngine_Container />
            <Advanced_Container />
        </div>
    );
};


const Mic_Container = () => {
    const { t } = useI18n();
    return (
        <div>
            <SectionLabelComponent label={t("config_page.transcription.section_label_mic")} />
            <MicRecordTimeout_Box />
            <MicPhraseTimeout_Box />
            <MicMaxWords_Box />
            <MicWordFilter_Box />
        </div>
    );
};

const MicRecordTimeout_Box = () => {
    const { t } = useI18n();
    const { currentMicRecordTimeout, setMicRecordTimeout } = useTranscription();

    const selectFunction = (selected_data) => {
        setMicRecordTimeout(selected_data.selected_id);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="mic_record_timeout"
            label={t("config_page.transcription.mic_record_timeout.label")}
            desc={t("config_page.transcription.mic_record_timeout.desc")}
            selected_id={currentMicRecordTimeout.data}
            list={genNumObjArray(31)}
            selectFunction={selectFunction}
            state={currentMicRecordTimeout.state}
        />
    );
};
const MicPhraseTimeout_Box = () => {
    const { t } = useI18n();
    const { currentMicPhraseTimeout, setMicPhraseTimeout } = useTranscription();

    const selectFunction = (selected_data) => {
        setMicPhraseTimeout(selected_data.selected_id);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="mic_phrase_timeout"
            label={t("config_page.transcription.mic_phrase_timeout.label")}
            desc={t("config_page.transcription.mic_phrase_timeout.desc")}
            selected_id={currentMicPhraseTimeout.data}
            list={genNumObjArray(31)}
            selectFunction={selectFunction}
            state={currentMicPhraseTimeout.state}
        />
    );
};
const MicMaxWords_Box = () => {
    const { t } = useI18n();
    const { currentMicMaxWords, setMicMaxWords } = useTranscription();

    const selectFunction = (selected_data) => {
        setMicMaxWords(selected_data.selected_id);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="mic_max_phrase"
            label={t("config_page.transcription.mic_max_phrase.label")}
            desc={t("config_page.transcription.mic_max_phrase.desc")}
            selected_id={currentMicMaxWords.data}
            list={genNumObjArray(31)}
            selectFunction={selectFunction}
            state={currentMicMaxWords.state}
        />
    );
};

const MicWordFilter_Box = () => {
    const { t } = useI18n();

    return (
        <WordFilterContainer
            label={t("config_page.transcription.mic_word_filter.label")}
            desc={t("config_page.transcription.mic_word_filter.desc")}
        />
    );
};




const Speaker_Container = () => {
    const { t } = useI18n();
    return (
        <div>
            <SectionLabelComponent label={t("config_page.transcription.section_label_speaker")} />
            <SpeakerRecordTimeout_Box />
            <SpeakerPhraseTimeout_Box />
            <SpeakerMaxWords_Box />
        </div>
    );
};

const SpeakerRecordTimeout_Box = () => {
    const { t } = useI18n();
    const { currentSpeakerRecordTimeout, setSpeakerRecordTimeout } = useTranscription();

    const selectFunction = (selected_data) => {
        setSpeakerRecordTimeout(selected_data.selected_id);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="speaker_record_timeout"
            desc={t("config_page.transcription.speaker_record_timeout.desc")}
            label={t("config_page.transcription.speaker_record_timeout.label")}
            selected_id={currentSpeakerRecordTimeout.data}
            list={genNumObjArray(31)}
            selectFunction={selectFunction}
            state={currentSpeakerRecordTimeout.state}
        />
    );
};
const SpeakerPhraseTimeout_Box = () => {
    const { t } = useI18n();
    const { currentSpeakerPhraseTimeout, setSpeakerPhraseTimeout } = useTranscription();

    const selectFunction = (selected_data) => {
        setSpeakerPhraseTimeout(selected_data.selected_id);
    };
    return (
        <DropdownMenuContainer
            dropdown_id="speaker_phrase_timeout"
            label={t("config_page.transcription.speaker_phrase_timeout.label")}
            desc={t("config_page.transcription.speaker_phrase_timeout.desc")}
            selected_id={currentSpeakerPhraseTimeout.data}
            list={genNumObjArray(31)}
            selectFunction={selectFunction}
            state={currentSpeakerPhraseTimeout.state}
        />
    );
};
const SpeakerMaxWords_Box = () => {
    const { t } = useI18n();
    const { currentSpeakerMaxWords, setSpeakerMaxWords } = useTranscription();

    const selectFunction = (selected_data) => {
        setSpeakerMaxWords(selected_data.selected_id);
    };

    return (
        <DropdownMenuContainer
            dropdown_id="speaker_max_phrase"
            label={t("config_page.transcription.speaker_max_phrase.label")}
            desc={t("config_page.transcription.speaker_max_phrase.desc")}
            selected_id={currentSpeakerMaxWords.data}
            list={genNumObjArray(61)}
            selectFunction={selectFunction}
            state={currentSpeakerMaxWords.state}
        />
    );
};



const TranscriptionEngine_Container = () => {
    const { t } = useI18n();
    const { currentSelectedTranscriptionEngine } = useTranscription();
    const engine = currentSelectedTranscriptionEngine?.data ?? currentSelectedTranscriptionEngine;
    return (
        <div>
            <SectionLabelComponent label={t("config_page.transcription.section_label_transcription_engines")} />
            <TranscriptionEngine_Box />
            {engine === "Whisper" && <WhisperWeightType_Box />}
            {engine === "Vosk" && <VoskWeightType_Box />}
            {engine === "Parakeet" && <ParakeetWeightType_Box />}
            {engine === "SenseVoice" && <SenseVoiceWeightType_Box />}
            <TranscriptionComputeDevice_Box />
            {engine === "Whisper" && <WhisperDecodingProfile_Box />}
        </div>
    );
};

const TranscriptionEngine_Box = () => {
    const { t } = useI18n();
    const { currentSelectedTranscriptionEngine, setSelectedTranscriptionEngine } = useTranscription();

    return (
        <RadioButtonContainer
            label={t("config_page.transcription.select_transcription_engine.label")}
            selectFunction={setSelectedTranscriptionEngine}
            name="select_transcription_engine"
            options={[
                { id: "Google", label: "Google (Cloud, 0 GB VRAM)" },
                { id: "Whisper", label: "Whisper / faster-whisper (CPU or GPU)" },
                { id: "Parakeet", label: "NVIDIA Parakeet TDT v3 (GPU, ~3 GB VRAM)" },
                { id: "Vosk", label: "Vosk (CPU, 0 GB VRAM)" },
                { id: "SenseVoice", label: "SenseVoice-Small (CPU, zh/en/ja/ko/yue)" },
            ]}
            checked_variable={currentSelectedTranscriptionEngine}
        />
    );
};

const VoskWeightType_Box = () => {
    const { t } = useI18n();
    const {
        currentVoskWeightTypeStatus,
        pendingVoskWeightTypeStatus,
        downloadVoskWeightTypeStatus,
        currentSelectedVoskWeightType,
        setSelectedVoskWeightType,
    } = useTranscription();

    if (!currentVoskWeightTypeStatus) return null;

    const selectFunction = (id) => setSelectedVoskWeightType(id);
    const downloadStartFunction = (id) => {
        pendingVoskWeightTypeStatus(id);
        downloadVoskWeightTypeStatus(id);
    };

    const items = (currentVoskWeightTypeStatus.data || []).map(item => ({
        ...item,
        label: `${item.id} (${item.capacity ?? ""})`,
    }));

    return (
        <DownloadModelsContainer
            label="Vosk Model"
            desc="CPU-only offline STT. One model = one language."
            name="vosk_weight_type"
            options={items}
            checked_variable={currentSelectedVoskWeightType}
            selectFunction={selectFunction}
            downloadStartFunction={downloadStartFunction}
        />
    );
};

const ParakeetWeightType_Box = () => {
    const { t } = useI18n();
    const {
        currentParakeetWeightTypeStatus,
        pendingParakeetWeightTypeStatus,
        downloadParakeetWeightTypeStatus,
        currentSelectedParakeetWeightType,
        setSelectedParakeetWeightType,
    } = useTranscription();

    if (!currentParakeetWeightTypeStatus) return null;

    const selectFunction = (id) => setSelectedParakeetWeightType(id);
    const downloadStartFunction = (id) => {
        pendingParakeetWeightTypeStatus(id);
        downloadParakeetWeightTypeStatus(id);
    };

    const items = (currentParakeetWeightTypeStatus.data || []).map(item => ({
        ...item,
        label: `${item.id} (${item.capacity ?? ""})`,
    }));

    return (
        <DownloadModelsContainer
            label="NVIDIA Parakeet Model"
            desc="GPU-accelerated STT via ONNX Runtime. Use parakeet-tdt-0.6b-v3 for the runnable multilingual model."
            name="parakeet_weight_type"
            options={items}
            checked_variable={currentSelectedParakeetWeightType}
            selectFunction={selectFunction}
            downloadStartFunction={downloadStartFunction}
        />
    );
};

const SenseVoiceWeightType_Box = () => {
    const { t } = useI18n();
    const {
        currentSenseVoiceWeightTypeStatus,
        pendingSenseVoiceWeightTypeStatus,
        downloadSenseVoiceWeightTypeStatus,
        currentSelectedSenseVoiceWeightType,
        setSelectedSenseVoiceWeightType,
    } = useTranscription();

    if (!currentSenseVoiceWeightTypeStatus) return null;

    const selectFunction = (id) => setSelectedSenseVoiceWeightType(id);
    const downloadStartFunction = (id) => {
        pendingSenseVoiceWeightTypeStatus(id);
        downloadSenseVoiceWeightTypeStatus(id);
    };

    const items = (currentSenseVoiceWeightTypeStatus.data || []).map(item => ({
        ...item,
        label: `${item.id} (${item.capacity ?? ""})`,
    }));

    return (
            <DownloadModelsContainer
            label="SenseVoice-Small Model"
            desc="CPU-only multi-lingual STT (zh, yue, en, ja, ko) via sherpa-onnx. INT8 is recommended for lower RAM usage."
            name="sensevoice_weight_type"
            options={items}
            checked_variable={currentSelectedSenseVoiceWeightType}
            selectFunction={selectFunction}
            downloadStartFunction={downloadStartFunction}
        />
    );
};

const WhisperWeightType_Box = () => {
    const { t } = useI18n();
    const {
        currentWhisperWeightTypeStatus,
        pendingWhisperWeightTypeStatus,
        downloadWhisperWeightTypeStatus,
    } = useTranscription();
    const { currentSelectedWhisperWeightType, setSelectedWhisperWeightType } = useTranscription();

    const selectFunction = (id) => {
        setSelectedWhisperWeightType(id);
    };

    const downloadStartFunction = (id) => {
        pendingWhisperWeightTypeStatus(id);
        downloadWhisperWeightTypeStatus(id);
    };

    const WHISPER_VRAM = {
        "tiny": "~0.6 GB VRAM (FP16) / ~0.4 GB (INT8)",
        "base": "~0.8 GB VRAM (FP16) / ~0.5 GB (INT8)",
        "small": "~1.8 GB VRAM (FP16) / ~1.1 GB (INT8)",
        "medium": "~3.5 GB VRAM (FP16) / ~2 GB (INT8)",
        "large-v1": "~5.5 GB VRAM (FP16) / ~3.2 GB (INT8)",
        "large-v2": "~5.5 GB VRAM (FP16) / ~3.2 GB (INT8)",
        "large-v3": "~5.5 GB VRAM (FP16) / ~3.2 GB (INT8)",
        "large-v3-turbo": "~2.8 GB VRAM (FP16) / ~1.6 GB (INT8)",
        "large-v3-turbo-int8": "~1.8 GB VRAM (INT8)",
    };

    const whisper_weight_types = currentWhisperWeightTypeStatus.data.map(item => {
        const vram = WHISPER_VRAM[item.id] ? ` — ${WHISPER_VRAM[item.id]}` : "";
        return {
            ...item,
            label: `${item.id} (${item.capacity})${vram}`,
        };
    });

    return (
        <>
            <DownloadModelsContainer
                label={t("config_page.transcription.whisper_weight_type.label")}
                desc={t(
                    "config_page.transcription.whisper_weight_type.desc",
                    {translator: t("main_page.translator")}
                )}
                name="whisper_weight_type"
                options={whisper_weight_types}
                checked_variable={currentSelectedWhisperWeightType}
                selectFunction={selectFunction}
                downloadStartFunction={downloadStartFunction}
            />
        </>
    );
};

const WHISPER_DECODING_PROFILE_IDS = Object.freeze(["fast", "balanced", "accurate"]);

const WhisperDecodingProfile_Box = () => {
    const { t } = useI18n();
    const {
        currentWhisperDecodingProfile,
        setWhisperDecodingProfile,
    } = useTranscription();

    const selectFunction = (selected_data) => {
        if (WHISPER_DECODING_PROFILE_IDS.includes(selected_data.selected_id)) {
            setWhisperDecodingProfile(selected_data.selected_id);
        }
    };

    return (
        <DropdownMenuContainer
            dropdown_id="whisper_decoding_profile"
            label={t("config_page.transcription.whisper_decoding_profile.label")}
            desc={t("config_page.transcription.whisper_decoding_profile.desc")}
            selected_id={currentWhisperDecodingProfile.data}
            list={{
                fast: t("config_page.transcription.whisper_decoding_profile.fast"),
                balanced: t("config_page.transcription.whisper_decoding_profile.balanced"),
                accurate: t("config_page.transcription.whisper_decoding_profile.accurate"),
            }}
            selectFunction={selectFunction}
            state={currentWhisperDecodingProfile.state}
        />
    );
};

const TranscriptionComputeDevice_Box = () => {
    const { t } = useI18n();
    const {
        currentSelectedTranscriptionEngine,
        currentSelectableTranscriptionComputeDeviceList,
        currentSelectedTranscriptionComputeDevice,
        setSelectedTranscriptionComputeDevice,
        currentSelectedTranscriptionComputeType,
        setSelectedTranscriptionComputeType,
    } = useTranscription();

    const engine = currentSelectedTranscriptionEngine?.data ?? "Whisper";
    const filteredDeviceList = filterDeviceMapByEngine(
        currentSelectableTranscriptionComputeDeviceList.data ?? {},
        engine,
    );
    const effectiveDevice =
        Object.values(filteredDeviceList).find((device) =>
            device.device === currentSelectedTranscriptionComputeDevice.data?.device &&
            device.device_index === currentSelectedTranscriptionComputeDevice.data?.device_index
        ) ?? Object.values(filteredDeviceList)[0] ?? currentSelectedTranscriptionComputeDevice.data;
    const computeTypesOverride = getAllowedTranscriptionComputeTypes({
        engine,
        device: effectiveDevice,
    });

    return (
        <ComputeDevice
            label={t("config_page.transcription.transcription_compute_device.label")}
            dropdownIdPrefix="transcription"
            currentDeviceList={{
                ...currentSelectableTranscriptionComputeDeviceList,
                data: filteredDeviceList,
            }}
            currentSelectedDevice={currentSelectedTranscriptionComputeDevice}
            setSelectedDevice={setSelectedTranscriptionComputeDevice}
            currentSelectedComputeType={currentSelectedTranscriptionComputeType}
            setSelectedComputeType={setSelectedTranscriptionComputeType}
            computeTypesOverride={computeTypesOverride}
        />
    );
};






const Advanced_Container = () => {
    const { t } = useI18n();
    return (
        <div>
            <SectionLabelComponent label="Advanced Settings (Whisper Model)" />
            <SectionLabelComponent label={t("config_page.transcription.section_label_transcription_engines")} />
            <MicAvgLogprobContainer />
            <MicNoSpeechProbContainer />
            <SpeakerAvgLogprobContainer />
            <SpeakerNoSpeechProbContainer />
        </div>
    );
};

export const MicAvgLogprobContainer = () => {
    const { t } = useI18n();
    const { currentMicAvgLogprob, setMicAvgLogprob } = useTranscription();
    return (
        <SliderContainer
            label="Mic Avg Logprob"
            desc="Default: -0.8"
            variable={currentMicAvgLogprob.data}
            setterFunction={setMicAvgLogprob}
            min={-2}
            max={0}
            step={0.1}
            marks_step={0.2}
        />
    );
};

export const MicNoSpeechProbContainer = () => {
    const { t } = useI18n();
    const { currentMicNoSpeechProb, setMicNoSpeechProb } = useTranscription();

    return (
        <SliderContainer
            label="Mic No Speech Prob"
            desc="Default: 0.6"
            variable={currentMicNoSpeechProb.data}
            setterFunction={setMicNoSpeechProb}
            min={0}
            max={1}
            step={0.1}
        />
    );
};

export const SpeakerAvgLogprobContainer = () => {
    const { t } = useI18n();
    const { currentSpeakerAvgLogprob, setSpeakerAvgLogprob } = useTranscription();

    return (
        <SliderContainer
            label="Speaker Avg Logprob"
            desc="Default: -0.8"
            variable={currentSpeakerAvgLogprob.data}
            setterFunction={setSpeakerAvgLogprob}
            min={-2}
            max={0}
            step={0.1}
            marks_step={0.2}
        />
    );
};

export const SpeakerNoSpeechProbContainer = () => {
    const { t } = useI18n();
    const { currentSpeakerNoSpeechProb, setSpeakerNoSpeechProb } = useTranscription();

    return (
        <SliderContainer
            label="Speaker No Speech Prob"
            desc="Default: 0.6"
            variable={currentSpeakerNoSpeechProb.data}
            setterFunction={setSpeakerNoSpeechProb}
            min={0}
            max={1}
            step={0.1}
        />
    );
};
