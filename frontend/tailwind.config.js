/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Clean light theme — white surfaces on a cool off-white ground, blue accent.
        canvas: "#f4f7fb", // page ground — cool off-white with a faint blue tint
        surface: "#ffffff", // cards
        surface2: "#f1f5fb", // subtle nested / hover fill
        line: "#e4e9f2", // hairline border (cool)
        line2: "#d4dbe8", // stronger border (hover)
        ink: {
          DEFAULT: "#0f1b32", // primary text — deep navy-ink
          soft: "#3f4a60", // strong secondary
          muted: "#7a849a", // captions / labels — cool grey
        },
        // Accent: a clean, confident blue.
        brand: {
          DEFAULT: "#2563eb",
          hover: "#1d4ed8",
          soft: "#e8f0fe", // tint backgrounds for selected/badges
        },
        savings: {
          DEFAULT: "#0f9d58",
          soft: "#e7f6ee",
          ink: "#0a7c44",
        },
        sev: {
          high: "#dc2626",
          highSoft: "#fdeced",
          med: "#c2710c",
          medSoft: "#fdf2e3",
          low: "#2563eb",
          lowSoft: "#e8f0fe",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Inter",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        // Gentle depth on dark — soft, low, no harsh black drop.
        card: "0 1px 2px rgba(15,27,50,0.04), 0 1px 3px rgba(15,27,50,0.05)",
        cardHover: "0 2px 6px rgba(15,27,50,0.06), 0 8px 20px rgba(15,27,50,0.08)",
        pop: "0 8px 28px rgba(15,27,50,0.12)",
      },
      borderRadius: {
        xl2: "10px",
      },
    },
  },
  plugins: [],
};
