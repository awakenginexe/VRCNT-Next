import { store } from "@store";
import { encode } from "js-base64";

export const useStdoutToPython = () => {
    const asyncStdoutToPython = async (path, value = undefined) => {
        let send_object = { endpoint: path };
        if (value !== undefined) send_object.data = encode(JSON.stringify(value));

        // send to python
        const backend_subprocess = store.backend_subprocess;
        if (!backend_subprocess) {
            const error = new Error("Backend subprocess is not found.");
            console.error(error, backend_subprocess);
            return { ok: false, error };
        }

        try {
            await backend_subprocess.write(JSON.stringify(send_object) + "\n");
            return { ok: true };
        } catch (cause) {
            const error = cause instanceof Error ? cause : new Error(String(cause));
            console.error(error);
            return { ok: false, error };
        }
    };
    return { asyncStdoutToPython };
};
