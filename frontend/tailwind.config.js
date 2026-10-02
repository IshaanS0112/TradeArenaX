/** @type {import('tailwindcss').Config} */
// Every colour resolves to a custom property declared in src/design/tokens.css.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Channel form, not `var(--x)` directly: Tailwind can only inject an alpha value into a colour.
      colors: {
        base: "rgb(var(--bg-base-rgb) / <alpha-value>)",
        panel: "rgb(var(--bg-panel-rgb) / <alpha-value>)",
        raised: "rgb(var(--bg-raised-rgb) / <alpha-value>)",
        edge: "rgb(var(--border-rgb) / <alpha-value>)",
        "edge-focus": "rgb(var(--border-focus-rgb) / <alpha-value>)",

        primary: "rgb(var(--fg-primary-rgb) / <alpha-value>)",
        secondary: "rgb(var(--fg-secondary-rgb) / <alpha-value>)",
        muted: "rgb(var(--fg-muted-rgb) / <alpha-value>)",

        bid: "rgb(var(--bid-rgb) / <alpha-value>)",
        ask: "rgb(var(--ask-rgb) / <alpha-value>)",
        accent: "rgb(var(--accent-rgb) / <alpha-value>)",
        warn: "rgb(var(--warn-rgb) / <alpha-value>)",
        info: "rgb(var(--info-rgb) / <alpha-value>)",

        // Aliases kept so PnL sign colouring reads as what it means rather than as bid/ask.
        positive: "rgb(var(--bid-rgb) / <alpha-value>)",
        negative: "rgb(var(--ask-rgb) / <alpha-value>)",
      },
      fontFamily: {
        ui: "var(--font-ui)",
        mono: "var(--font-mono)",
      },
      fontSize: {
        "2xs": ["11px", "16px"],
        xs: ["12px", "18px"],
        sm: ["13px", "20px"],
        base: ["15px", "24px"],
        lg: ["18px", "26px"],
        xl: ["24px", "32px"],
        "2xl": ["32px", "40px"],
      },
      borderRadius: {
        input: "2px",
        module: "6px",
      },
      transitionDuration: {
        fast: "var(--motion-fast)",
      },
    },
  },
  plugins: [],
};
