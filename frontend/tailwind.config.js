/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#080a10",
        panel: "#10141f",
        edge: "#1f2637",
        accent: "#38bdf8",
        bid: "#22c55e",
        ask: "#ef4444",
        positive: "#4ade80",
        negative: "#f87171",
        caution: "#fbbf24",
        muted: "#8794ad",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
