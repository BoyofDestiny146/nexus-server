import type { Config } from "tailwindcss";

// careconnect color palette — clinical, calm, slightly warm.
// Bone background, slate text, single teal accent, desaturated risk colors.
// Ported from the original Vue 3 dashboard.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bone: { DEFAULT: "#F7F4EE", soft: "#FBF9F4" },
        slate: {
          DEFAULT: "#384351",
          deep: "#1F2A37",
          muted: "#6B7785",
          line: "#E3DED4",
        },
        teal: { DEFAULT: "#1F8A7E", deep: "#176B62", tint: "#E2F1EE" },
        risk: {
          low: "#88A89C",
          moderate: "#D2A14B",
          elevated: "#C26446",
          urgent: "#A8392F",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ['"Source Serif 4"', "Georgia", "serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      letterSpacing: { tight: "-0.01em", display: "-0.015em" },
      boxShadow: {
        card: "0 1px 2px rgba(31,42,55,0.04), 0 1px 1px rgba(31,42,55,0.03)",
        cardHover: "0 4px 14px rgba(31,42,55,0.06), 0 1px 1px rgba(31,42,55,0.04)",
      },
      borderRadius: { card: "14px", chip: "999px" },
    },
  },
  plugins: [],
};

export default config;
