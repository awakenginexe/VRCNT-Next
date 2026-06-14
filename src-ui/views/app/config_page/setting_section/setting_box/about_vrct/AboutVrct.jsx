import styles from "./AboutVrct.module.scss";
import logoBadge from "@images/vrcnt_logo_badge.png";

export const AboutVrct = () => {
    return (
        <div className={styles.container}>
            <section className={styles.hero}>
                <img className={styles.logo_mark} src={logoBadge} alt="VRCNT-Next" />
                <div className={styles.hero_text}>
                    <p className={styles.kicker}>VRCNT-Next</p>
                    <h1 className={styles.title}>VRChat Next Gen Translation</h1>
                    <p className={styles.description}>
                        VRCNT-Next Unofficial next-generation translation tool for VRChat
                    </p>
                </div>
            </section>

            <section className={styles.notice}>
                <p className={styles.notice_label}>Project Lineage</p>
                <p className={styles.notice_text}>
                    This program is based from VRCT. VRCNT-Next is an unofficial customized continuation focused on a modern workflow, clearer language controls, and practical VRChat translation use.
                </p>
                <a className={styles.link_button} href="https://github.com/misyaguziya/VRCT" target="_blank" rel="noreferrer">
                    Original VRCT GitHub
                </a>
            </section>

            <section className={styles.info_grid}>
                <div className={styles.info_card}>
                    <p className={styles.card_label}>Identity</p>
                    <p className={styles.card_title}>VRCNT-Next</p>
                    <p className={styles.card_text}>A modern customizable interface for translation, transcription, and VRChat message flow.</p>
                </div>
                <div className={styles.info_card}>
                    <p className={styles.card_label}>Status</p>
                    <p className={styles.card_title}>Unofficial</p>
                    <p className={styles.card_text}>This build is not an official VRCT release and is not endorsed by VRChat.</p>
                </div>
                <div className={styles.info_card}>
                    <p className={styles.card_label}>Focus</p>
                    <p className={styles.card_title}>Fast Translation</p>
                    <p className={styles.card_text}>Designed around practical VRChat sessions, split language roles, and local AI engines.</p>
                </div>
            </section>

            <section className={styles.disclaimer}>
                <p>
                    VRCNT-Next is not endorsed by VRChat and does not reflect the views or opinions of VRChat or anyone officially involved in producing or managing VRChat properties. VRChat and all associated properties are trademarks or registered trademarks of VRChat Inc.
                </p>
            </section>
        </div>
    );
};
