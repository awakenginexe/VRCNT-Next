import clsx from "clsx";

import styles from "./LanguageFlag.module.scss";
import { getCountryFlagCode } from "./languageDisplayUtils.js";

export const LanguageFlag = ({ country, className }) => {
    const countryCode = getCountryFlagCode(country);

    if (countryCode === "") {
        return (
            <span className={clsx(styles.flag_shell, styles.fallback, className)} title={country}>
                <span className={styles.globe_mark} />
            </span>
        );
    }

    return (
        <span className={clsx(styles.flag_shell, className)} title={country}>
            <span className={clsx("fi", `fi-${countryCode}`, styles.flag_icon)} />
        </span>
    );
};
