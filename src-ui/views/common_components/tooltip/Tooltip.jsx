import React, { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import styles from "./Tooltip.module.scss";
import clsx from "clsx";

export const Tooltip = ({
    title,
    detail,
    children,
    placement = "top",
    className,
    contentClassName,
    slotProps,
    disabled = false,
    usePortal = false,
}) => {
    const [isVisible, setIsVisible] = useState(false);
    const [portalStyle, setPortalStyle] = useState(null);
    const wrapperRef = useRef(null);

    let marginBottom = "0.8rem"; // Default offset
    try {
        if (slotProps?.popper?.sx) {
            // Crude extraction if they passed MUI style sx
            const sxStr = JSON.stringify(slotProps.popper.sx);
            if (sxStr.includes("marginBottom")) {
                marginBottom = "0.2em"; // Using what was hardcoded in project
            }
        }
    } catch(e) {}

    useLayoutEffect(() => {
        if (disabled || !isVisible || !usePortal || !wrapperRef.current) return;

        const updatePortalStyle = () => {
            const rect = wrapperRef.current.getBoundingClientRect();
            const offset = 8;
            const nextStyle = placement === "right"
                ? {
                    position: "fixed",
                    top: `${rect.top + rect.height / 2}px`,
                    left: `${rect.right + offset}px`,
                    "--tooltip-portal-transform": "translate(0.3rem, -50%)",
                    "--tooltip-portal-transform-visible": "translate(0, -50%)",
                }
                : {
                    position: "fixed",
                    top: `${rect.top - offset}px`,
                    left: `${rect.left + rect.width / 2}px`,
                    "--tooltip-portal-transform": "translate(-50%, -0.3rem)",
                    "--tooltip-portal-transform-visible": "translate(-50%, -100%)",
                };
            setPortalStyle(nextStyle);
        };

        updatePortalStyle();
        window.addEventListener("resize", updatePortalStyle);
        window.addEventListener("scroll", updatePortalStyle, true);
        return () => {
            window.removeEventListener("resize", updatePortalStyle);
            window.removeEventListener("scroll", updatePortalStyle, true);
        };
    }, [disabled, isVisible, placement, usePortal]);

    const tooltipBox = (
        <div
            className={clsx(
                styles.tooltipBox,
                styles[`placement-${placement}`],
                {
                    [styles.is_portal]: usePortal,
                },
                contentClassName,
            )}
            style={{
                "--margin-bottom": marginBottom,
                ...(usePortal && portalStyle ? portalStyle : {}),
            }}
        >
            {detail ? (
                <div className={styles.tooltipContent}>
                    <p className={styles.tooltipTitle}>{title}</p>
                    <p className={styles.tooltipDetail}>{detail}</p>
                </div>
            ) : title}
        </div>
    );

    return (
        <div
            ref={wrapperRef}
            className={clsx(styles.tooltipWrapper, className)}
            onMouseEnter={() => setIsVisible(true)}
            onMouseLeave={() => setIsVisible(false)}
            onFocus={() => setIsVisible(true)}
            onBlur={() => setIsVisible(false)}
        >
            {children}
            {!disabled && isVisible && (usePortal ? createPortal(tooltipBox, document.body) : tooltipBox)}
        </div>
    );
};
