import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";

interface TextCardProps {
  text: string;
  fontSize?: number;
  color?: string;
  backgroundColor?: string;
  cardBackgroundColor?: string;
  cardBorder?: string;
  cardShadow?: string;
  textShadow?: string;
  textDirection?: "auto" | "ltr" | "rtl";
}

export const TextCard: React.FC<TextCardProps> = ({
  text,
  fontSize = 64,
  color = "#FFFFFF",
  backgroundColor = "#1F2937",
  cardBackgroundColor = "transparent",
  cardBorder = "none",
  cardShadow = "none",
  textShadow,
  textDirection = "auto",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const opacity = spring({ frame, fps, config: { damping: 20 } });
  const scale = spring({
    frame,
    fps,
    config: { damping: 15, stiffness: 100 },
    from: 0.95,
    to: 1,
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        background: backgroundColor,
      }}
    >
      <div
        dir={textDirection}
        style={{
          opacity,
          transform: `scale(${scale})`,
          fontSize,
          color,
          background: cardBackgroundColor,
          border: cardBorder,
          borderRadius: cardBackgroundColor === "transparent" ? 0 : 24,
          padding: cardBackgroundColor === "transparent" ? "0 18px" : "34px 44px",
          unicodeBidi: "plaintext",
          fontFamily: "Inter, Tahoma, Arial, system-ui, sans-serif",
          fontWeight: 700,
          textAlign: "center",
          maxWidth: "86%",
          lineHeight: 1.35,
          whiteSpace: "pre-line",
          boxShadow: cardShadow,
          textShadow:
            textShadow ||
            (cardBackgroundColor === "transparent"
              ? "0 4px 18px rgba(0,0,0,0.78), 0 1px 2px rgba(0,0,0,0.9)"
              : "0 1px 0 rgba(255, 255, 255, 0.4)"),
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
