import { useEffect, useRef } from "react";

import styles from "./BlockingOperationOverlay.module.scss";

export const BlockingOperationOverlay = ({
    open,
    operationId,
    title,
    phase,
    detail,
    progress,
    progressLabel,
    progressText,
    elapsedText,
}) => {
    const cardRef = useRef(null);
    const previousFocusRef = useRef(null);

    useEffect(() => {
        if (!open) return undefined;

        previousFocusRef.current = document.activeElement;
        cardRef.current?.focus();

        return () => {
            const previous = previousFocusRef.current;
            if (previous?.isConnected) previous.focus();
        };
    }, [open]);

    if (!open) return null;

    const titleId = `blocking-operation-${operationId}-title`;
    const descriptionId = `blocking-operation-${operationId}-description`;
    const determinate = progress.kind === "determinate";
    const progressPercent = determinate
        ? Math.min(100, Math.max(0, progress.max > 0
            ? (progress.value / progress.max) * 100
            : 0))
        : 0;
    const progressAria = determinate
        ? {
            "aria-valuemin": 0,
            "aria-valuemax": progress.max,
            "aria-valuenow": progress.value,
        }
        : { "aria-valuetext": progressText };
    const progressClassName = determinate
        ? styles.progress
        : `${styles.progress} ${styles.is_indeterminate}`;

    return (
        <div
            className={styles.overlay}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
        >
            <section
                className={styles.card}
                ref={cardRef}
                tabIndex={-1}
            >
                <h2 className={styles.title} id={titleId}>{title}</h2>
                <div
                    id={descriptionId}
                    className={styles.description}
                    role="status"
                    aria-live="polite"
                    aria-atomic="true"
                >
                    <p className={styles.phase}>{phase}</p>
                    {detail ? <p className={styles.detail}>{detail}</p> : null}
                </div>
                <div
                    className={progressClassName}
                    role="progressbar"
                    aria-label={progressLabel}
                    {...progressAria}
                >
                    <span
                        className={styles.progress_fill}
                        style={determinate
                            ? { "--progress-percent": `${progressPercent}%` }
                            : undefined}
                    />
                </div>
                <p className={styles.progress_text}>{progressText}</p>
                <p className={styles.elapsed}>{elapsedText}</p>
            </section>
        </div>
    );
};
