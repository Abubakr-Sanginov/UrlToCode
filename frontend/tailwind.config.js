/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./app/**/*.{ts,tsx}",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      fontFamily: {
        sans: [
          '"IBM Plex Sans"',
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        // Headlines on the public page only. The same superfamily as the
        // body text, so the two do not argue, but a serif sitting next to
        // Plex Sans is unmistakably a different register — the page reads
        // like a document rather than a pitch. The app itself does not use
        // it: an editor is a tool, and a tool set in a serif is a brochure.
        display: ['"IBM Plex Serif"', '"IBM Plex Sans"', "Georgia", "serif"],
        mono: [
          '"JetBrains Mono"',
          '"Fira Code"',
          '"SF Mono"',
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        // Page background, one step behind `card`, so raised surfaces read
        // without needing a shadow.
        canvas: "hsl(var(--canvas))",
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Single accent for selection, focus and primary actions.
        brand: {
          DEFAULT: "hsl(var(--brand))",
          foreground: "hsl(var(--brand-foreground))",
          muted: "hsl(var(--brand-muted))",
          subtle: "hsl(var(--brand-subtle))",
          border: "hsl(var(--brand-border))",
        },
        // Status colors. Desaturated so they coexist in one list.
        success: {
          DEFAULT: "hsl(var(--success))",
          subtle: "hsl(var(--success-subtle))",
          border: "hsl(var(--success-border))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          subtle: "hsl(var(--warning-subtle))",
          border: "hsl(var(--warning-border))",
        },
        danger: {
          DEFAULT: "hsl(var(--danger))",
          subtle: "hsl(var(--danger-subtle))",
          border: "hsl(var(--danger-border))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "calc(var(--radius) + 4px)",
        "2xl": "calc(var(--radius) + 8px)",
      },
      boxShadow: {
        // Dark-first surfaces lean on borders for elevation; shadows stay
        // subtle enough to work on both the dark and the light canvas.
        card: "0 1px 2px 0 hsl(222 47% 5% / 0.12), 0 1px 3px 0 hsl(222 47% 5% / 0.1)",
        raised:
          "0 2px 4px -1px hsl(222 47% 5% / 0.15), 0 6px 16px -2px hsl(222 47% 5% / 0.18)",
        overlay:
          "0 4px 8px -2px hsl(222 47% 5% / 0.18), 0 16px 32px -6px hsl(222 47% 5% / 0.28)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: 0 },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: 0 },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
