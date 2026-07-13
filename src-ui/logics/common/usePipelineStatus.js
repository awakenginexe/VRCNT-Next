import { useCallback } from "react";

import { useStore_PipelineStatus } from "@store";
import { mergePipelineStatusEvent } from "./pipelineStatusUtils.js";

export const usePipelineStatus = () => {
    const {
        currentPipelineStatus,
        updatePipelineStatus: updateStorePipelineStatus,
    } = useStore_PipelineStatus();

    const updatePipelineStatus = useCallback((payload) => {
        updateStorePipelineStatus((currentValue) => (
            mergePipelineStatusEvent(currentValue.data, payload)
        ));
    }, [updateStorePipelineStatus]);

    return {
        currentPipelineStatus,
        updatePipelineStatus,
    };
};
