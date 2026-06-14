import styles from "./Supporters.module.scss";
import { _OpenWebpageButton } from "../_components/_atoms/_open_webpage_button/_OpenWebpageButton";
import vrctLogo from "@images/about_vrct/vrct_logo_for_about_vrct.png";

export const Supporters = () => {
    return (
        <div className={styles.container}>
            <section className={styles.hero}>
                <img className={styles.logo_mark} src={vrctLogo} alt="VRCT" />
                <div className={styles.hero_text}>
                    <p className={styles.kicker}>Credit</p>
                    <h2 className={styles.title}>Built on top of VRCT</h2>
                    <p className={styles.description}>
                        VRCNT-Next keeps growing from the original VRCT project and pushes the experience toward a more modern VRChat translation workflow.
                    </p>
                </div>
            </section>

            <section className={styles.notice}>
                <p className={styles.notice_label}>Original Project</p>
                <p className={styles.notice_text}>
                    This software is based on VRCT by misyaguziya. The original repository is the foundation this unofficial VRCNT-Next build continues from.
                </p>
                <_OpenWebpageButton
                    webpage_url="https://github.com/misyaguziya/VRCT"
                    open_webpage_label="Open VRCT GitHub Repository"
                />
            </section>

            <div className={styles.info_grid}>
                <section className={styles.info_card}>
                    <p className={styles.card_label}>VRCNT-Next</p>
                    <h3 className={styles.card_title}>Unofficial next-generation direction</h3>
                    <p className={styles.card_text}>
                        Reworked for faster engine switching, clearer language handling, and a UI that fits your own version instead of feeling like a straight copy.
                    </p>
                </section>

                <section className={styles.info_card}>
                    <p className={styles.card_label}>Acknowledgement</p>
                    <h3 className={styles.card_title}>Credit stays visible</h3>
                    <p className={styles.card_text}>
                        The app keeps a direct path back to VRCT so users can understand where the base came from and explore the upstream project themselves.
                    </p>
                </section>

                <section className={styles.info_card}>
                    <p className={styles.card_label}>Design Goal</p>
                    <h3 className={styles.card_title}>A cleaner control surface</h3>
                    <p className={styles.card_text}>
                        VRCNT-Next is moving toward a darker aqua interface with better scanning, less friction, and a layout that can keep evolving from here.
                    </p>
                </section>
            </div>

            <section className={styles.disclaimer}>
                VRCNT-Next is an unofficial fork for VRChat translation workflows. VRCT remains the original project and deserves the source credit.
            </section>
        </div>
    );
};
