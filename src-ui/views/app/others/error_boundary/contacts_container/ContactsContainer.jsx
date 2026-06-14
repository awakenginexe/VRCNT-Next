import dev_github_icon from "@images/about_vrct/dev_github_icon.png";
import styles from "./ContactsContainer.module.scss";

export const ContactsContainer = () => {
    return (
        <div className={styles.container}>
            <a className={styles.github_issues} href="https://github.com/awakenginexe/VRCNT-Next/issues" target="_blank" rel="noreferrer" >
                <img className={styles.contact_button_icon} src={dev_github_icon} alt="" />
                <p className={styles.contact_button_label}>GitHub Issues</p>
            </a>
        </div>
    );
};
