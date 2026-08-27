import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

export const ARABIC_FONT_FAMILY = "OpenMontage Noto Sans Arabic";

void loadFont({
  family: ARABIC_FONT_FAMILY,
  url: staticFile("fonts/NotoSansArabic-Variable.woff2"),
  format: "woff2",
  weight: "100 900",
  display: "block",
});

// Keep the established Latin face first while making Arabic shaping independent
// of fonts installed on the render host.
export const ARABIC_FONT_STACK = `Inter, "${ARABIC_FONT_FAMILY}", Tahoma, Arial, system-ui, sans-serif`;
