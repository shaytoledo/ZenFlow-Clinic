import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f4ff",
          100: "#dce6ff",
          500: "#4f6ef7",
          600: "#3a56e8",
          700: "#2c42c9",
        },
      },
    },
  },
  plugins: [],
};

export default config;
