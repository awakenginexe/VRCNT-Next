import { useState } from "react";
import { ErrorBoundary } from "react-error-boundary";

import CopySvg from "@images/copy.svg?react";
import CheckMarkSvg from "@images/check_mark.svg?react";

import { ContactsContainer } from "./contacts_container/ContactsContainer";

import {
    useWindow,
    useSoftwareVersion,
} from "@logics_common";
import { CloseButton } from "@common_components";

import styles from "./AppErrorBoundary.module.scss";

export const AppErrorBoundary = ({children}) => {
    const [errorInfo, setErrorInfo] = useState(null);

    return (
        <ErrorBoundary
            onError={(error, info) => setErrorInfo(info)}
            fallbackRender={({ error }) => (
                <ErrorContainer error={error} errorInfo={errorInfo} />
            )
        }>
            {children}
        </ErrorBoundary>
    );
};

const ErrorContainer = ({error, errorInfo}) => {
    const { asyncCloseApp } = useWindow();
    const { currentSoftwareVersion } = useSoftwareVersion();
    const [is_copied, setIsCopied] = useState(false);

    const formatted_stack = error ? formatStackTrace(error.stack) : "Unknown error";
    const app_version = currentSoftwareVersion?.data || "Unknown";

    const error_log_text = [
        `Version: ${app_version}`,
        `Date: ${new Date().toISOString().replace('T', ' ').split('.')[0]}`,
        "",
        "=== Error Stack ===",
        formatted_stack,
        "",
        "=== Component Stack ===",
        errorInfo?.componentStack ? formatStackTrace(errorInfo.componentStack) : "Not available",
    ].join("\n");

    const copyToClipboard = async () => {
        if (is_copied) return;

        await navigator.clipboard.writeText(error_log_text);
        setIsCopied(true);

        setTimeout(() => {
            setIsCopied(false);
        }, 1000);
    };

    return (
        <div className={styles.container}>
            <div className={styles.drag_able_area} data-tauri-drag-region></div>
            <CloseButton variant="active_error" onClick={asyncCloseApp} />
            <div className={styles.wrapper}>
                <p className={styles.error_message}>An error occurred. Please restart VRCNT-Next or contact the developers.</p>
                <SafeActionButtons />
                {error ?
                    <div className={styles.error_detail_container}>
                        <div className={styles.error_stack_container}>
                            <p className={styles.error_stack}>
                                {error_log_text}
                            </p>
                        </div>
                        <button className={styles.copy_error_message_button} onClick={copyToClipboard}>
                            <p className={styles.copy_text}>Copy</p>
                            {is_copied
                                ? <CheckMarkSvg className={styles.check_mark_svg}/>
                                : <CopySvg className={styles.copy_svg}/>
                            }
                        </button>
                    </div>
                : null}
                <ContactsContainer />
            </div>
        </div>
    );
};


const SafeActionButtons = () => {
    return null;
};

const formatStackTrace = (stack) => {
    if (!stack) return "";
    // フルパスの除去（例として window.location.origin や絶対パス部分を削除）
    const formatted = stack.replace(new RegExp(window.location.origin, "g"), "");

    return formatted;
};
