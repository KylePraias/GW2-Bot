/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}", "./public/index.html"],
  theme: {
    extend: {
      fontFamily: {
        display: ["'Cormorant Garamond'", "serif"],
        cinzel: ["Cinzel", "serif"],
        body: ["Outfit", "sans-serif"],
        mono: ["'JetBrains Mono'", "monospace"],
      },
      colors: {
        ember: {
          DEFAULT: "#7f1d1d",
          glow: "#991b1b",
        },
        gold: {
          DEFAULT: "#f59e0b",
          soft: "#fbbf24",
        },
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(20px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slow-zoom": {
          "0%": { transform: "scale(1)" },
          "100%": { transform: "scale(1.12)" },
        },
        flicker: {
          "0%, 100%": { opacity: "0.85" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.7s ease-out forwards",
        "slow-zoom": "slow-zoom 24s ease-in-out infinite alternate",
        flicker: "flicker 4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
