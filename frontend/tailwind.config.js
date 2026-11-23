/** @type {import('tailwindcss').Config} */
module.exports = {
    // `overline` is a Tailwind utility; without this an app's own eyebrow-label class draws a line above the text.
    blocklist: ["overline"],
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)'
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        // ---- Vantage terminal palette (graphite + brand gold; green/red only for P&L and direction) ----
        base: 'rgb(var(--c-base) / <alpha-value>)',
        chrome: 'rgb(var(--c-chrome) / <alpha-value>)',
        panel: 'rgb(var(--c-panel) / <alpha-value>)',
        panel2: 'rgb(var(--c-panel2) / <alpha-value>)',
        raised: 'rgb(var(--c-raised) / <alpha-value>)',
        line: 'rgb(var(--c-line) / <alpha-value>)',
        line2: 'rgb(var(--c-line2) / <alpha-value>)',
        slate: { 50: '#f4f6f8', 100: '#e8ebef', 200: '#d0d5dc', 300: '#b4bbc5', 400: '#9099a5', 500: '#6c7581', 600: '#4d555f', 700: '#373d46', 800: '#242930', 900: '#161a20', 950: '#0c0f13' },
        teal: { 50: '#e9fbf2', 100: '#c9f5df', 200: '#96ebc2', 300: '#5ddda1', 400: '#2fcb84', 500: '#19b36f', 600: '#12915a', 700: '#10724a', 800: '#0f5a3c', 900: '#0c4430', 950: '#062a1d' },
        red: { 50: '#fff1f2', 100: '#ffe0e3', 200: '#ffc2c8', 300: '#ff8f99', 400: '#ff5f6d', 500: '#f43f50', 600: '#d92b3c', 700: '#b31f2f', 800: '#8c1a27', 900: '#6b1722', 950: '#3d0a11' },
        amber: { 50: '#fff8e6', 100: '#feedbf', 200: '#fdd985', 300: '#fcc44a', 400: '#f7b21f', 500: '#f0a000', 600: '#c98200', 700: '#9e6500', 800: '#774d06', 900: '#5c3c08', 950: '#352103' },
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))'
        }
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0'
          },
          to: {
            height: 'var(--radix-accordion-content-height)'
          }
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)'
          },
          to: {
            height: '0'
          }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};