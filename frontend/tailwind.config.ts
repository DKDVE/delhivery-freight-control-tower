import type { Config } from "tailwindcss";

// Design tokens from the design brief (section 5). Text uses the darker amber/red variants so small text meets WCAG AA;
// the brand amber/red stay for fills, bars and icons.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#F7F8FA",
        surface: "#FFFFFF",
        line: "#E3E6EA",
        grid: "#EDEFF2",
        ink: "#14202B",
        ink2: "#5B6470",
        red: { DEFAULT: "#EF4136", dark: "#C21F26", tint: "#FDECEA" },
        teal: { DEFAULT: "#1F6F78", tint: "#E6F1F2" },
        amber: { DEFAULT: "#B7791F", text: "#8A5A12", tint: "#FBF3E2" },
        navy: { DEFAULT: "#1F3A5F", tint: "#E9EDF3" },
      },
      fontFamily: { sans: ['"DM Sans Variable"', "DM Sans", "system-ui", "sans-serif"] },
      fontSize: { base: ["14px", "1.45"], xs: ["12px", "1.4"] },
      borderRadius: { DEFAULT: "8px", md: "6px" },
      boxShadow: {},
    },
  },
  plugins: [],
} satisfies Config;
