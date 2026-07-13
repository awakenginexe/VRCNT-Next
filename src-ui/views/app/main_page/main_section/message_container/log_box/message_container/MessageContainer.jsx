import { useState } from "react";
import { useI18n } from "@useI18n";
import clsx from "clsx";
import styles from "./MessageContainer.module.scss";
import { MessageSubMenuContainer } from "./message_sub_menu_container/MessageSubMenuContainer";
import { MessageText } from "./MessageText";
import { TranslationEntry } from "./translation_entry/TranslationEntry";
import { useMessage } from "@logics_common";
import { useAppearance } from "@logics_configs";

export const MessageContainer = ({ messages, status, category, created_at }) => {
    const { t } = useI18n();
    const {
        sendMessage,
        updateMessageInputValue,
    } = useMessage();
    const { currentShowResendButton } = useAppearance();
    const [is_hovered, setIsHovered] = useState(false);
    const [is_locked, setIsLocked] = useState(false);

    const resendFunction = () => {
        sendMessage(messages.original.message);
    };
    const editFunction = () => {
        updateMessageInputValue(messages.original.message);
    };

    const handleMouseEnter = () => {
        if (!is_locked) {
            setIsHovered(true);
        }
    };

    const handleMouseLeave = () => {
        setIsHovered(false);
        setIsLocked(false);
    };

    const lockHoverState = () => {
        setIsHovered(false);
        setIsLocked(true);
    };

    const has_translations = messages.translations.length > 0;
    const is_pending = status === "pending";
    const is_sent_message = category === "sent";
    const is_system_message = category === "system";
    const category_text = is_sent_message
        ? t("main_page.message_log.sent")
        : is_system_message
        ? t("main_page.message_log.system")
        : t("main_page.message_log.received");

    const message_type_class_name = clsx({
        [styles.sent_message]: is_sent_message,
        [styles.received_message]: !is_sent_message && !is_system_message,
        [styles.system_message]: is_system_message,
    });

    return (
        <div
            className={clsx(styles.container, message_type_class_name)}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
        >
            <div className={clsx(styles.message_wrapper, message_type_class_name)}>
                <div className={clsx(styles.info_box, message_type_class_name)}>
                    <p className={styles.time}>{created_at}</p>
                    <p className={clsx(styles.category, message_type_class_name)}>{category_text}</p>
                    {is_sent_message && is_pending && <span className={styles.loader}></span>}
                </div>
                <div className={clsx(styles.message_box, message_type_class_name)}>
                    {is_system_message ? (
                        <p className={styles.message_main_system}>{messages.original.message}</p>
                    ) : (
                        <>
                            <div
                                className={clsx(styles.original_message, {
                                    [styles.with_translations]: has_translations,
                                })}
                            >
                                <MessageText item={messages.original} />
                            </div>
                            {messages.translations.map((entry) => (
                                <TranslationEntry key={entry.target_slot} entry={entry} />
                            ))}
                        </>
                    )}
                </div>
            </div>
            {currentShowResendButton.data && is_sent_message && is_hovered ? (
                <MessageSubMenuContainer
                    setIsHovered={lockHoverState}
                    resendFunction={resendFunction}
                    editFunction={editFunction}
                />
            ) : null}
        </div>
    );
};
