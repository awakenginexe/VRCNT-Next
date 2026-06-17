export const THAI_UI_LANGUAGE_ID = "th";
export const THAI_PREFERRED_FONT_FAMILY = "Itim";

export const findFontFamily = (fontFamilyList = {}, preferredFont = THAI_PREFERRED_FONT_FAMILY) => {
    if (!fontFamilyList || typeof fontFamilyList !== "object") return null;

    const normalizedPreferredFont = preferredFont.toLowerCase();
    return Object.keys(fontFamilyList).find(
        (fontFamily) => fontFamily.toLowerCase() === normalizedPreferredFont
    ) ?? null;
};

export const getThaiPreferredFontFamily = (fontFamilyList = {}) => (
    findFontFamily(fontFamilyList, THAI_PREFERRED_FONT_FAMILY)
);

export const shouldApplyThaiPreferredFont = ({ uiLanguage, selectedFontFamily, fontFamilyList }) => {
    if (uiLanguage !== THAI_UI_LANGUAGE_ID) return false;
    const availableFont = getThaiPreferredFontFamily(fontFamilyList);
    return Boolean(availableFont && selectedFontFamily !== availableFont);
};
