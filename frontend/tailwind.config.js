/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Dark theme palette
        ink: {
          900: "#0b0f17",
          800: "#111827",
          700: "#1f2937",
          600: "#374151",
        },
        accent: {
          DEFAULT: "#38bdf8",
          hover: "#0ea5e9",
        },
      },
    },
  },
  plugins: [],
};
