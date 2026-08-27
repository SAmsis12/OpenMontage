import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const ARABIC_FONT_FAMILY = "OpenMontage Noto Sans Arabic";
export const ARABIC_UNICODE_RANGE =
  "U+0600-06FF, U+0750-077F, U+0870-089F, U+08A0-08FF, " +
  "U+FB50-FDFF, U+FE70-FEFF, U+1EE00-1EEFF";

void loadFont({
  family: ARABIC_FONT_FAMILY,
  url: staticFile("fonts/NotoSansArabic-Variable.woff2"),
  format: "woff2",
  weight: "100 900",
  display: "block",
  unicodeRange: ARABIC_UNICODE_RANGE,
});

// Keep the established Latin face first while making Arabic shaping independent
// of fonts installed on the render host.
export const arabicFontStack = (latinStack: string): string =>
  `"${ARABIC_FONT_FAMILY}", ${latinStack}`;

// Noto leads, but only for Arabic code points; Latin/theme faces remain intact.
export const ARABIC_FONT_STACK = arabicFontStack(
  "Inter, Tahoma, Arial, system-ui, sans-serif",
);

export const containsArabicScript = (text: string): boolean =>
  /[\u0600-\u06ff\u0750-\u077f\u0870-\u089f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]/u.test(text);
