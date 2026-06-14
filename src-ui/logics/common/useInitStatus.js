import { useStore_InitStatus } from "@store";

export const useInitStatus = () => {
    const { currentInitStatus, updateInitStatus } = useStore_InitStatus();

    return {
        currentInitStatus,
        updateInitStatus,
    };
};
