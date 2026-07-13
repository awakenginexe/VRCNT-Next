import styles from "./MessageContainer.module.scss";

export const MessageText = ({ item }) => {
    const transliteration = item?.transliteration ?? [];
    const message = item?.message ?? "";

    const renderTokenNode = (token, key) => {
        const orig = token?.orig ?? "";
        const hira = token?.hira ?? "";
        const hepburn = token?.hepburn ?? "";

        if (hira && hira === orig && hepburn) {
            return (
                <span key={key} title={hepburn} className={styles.with_hepburn}>
                    {orig}
                </span>
            );
        }

        if (hira && hepburn) {
            return (
                <ruby key={key} title={hepburn} className={styles.with_hepburn}>
                    {orig}
                    <rt>{hira}</rt>
                </ruby>
            );
        }

        if (hepburn || hira) {
            const ruby = hepburn || hira;
            if (ruby !== orig) {
                return (
                    <ruby key={key} className={styles.ruby}>
                        {orig}
                        <rt>{ruby}</rt>
                    </ruby>
                );
            }
        }

        return (
            <span key={key} className={styles.original_only}>
                {orig}
            </span>
        );
    };

    if (!transliteration.length) {
        return <p className={styles.message_main}>{message}</p>;
    }

    return (
        <p className={styles.message_main}>
            {transliteration.map((token, index) => renderTokenNode(token, index))}
        </p>
    );
};
