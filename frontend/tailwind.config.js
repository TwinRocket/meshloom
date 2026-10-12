/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
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
        "msg-outgoing": "hsl(var(--msg-outgoing))",
        "msg-incoming": "hsl(var(--msg-incoming))",
        "status-connected": "hsl(var(--status-connected))",
        "status-disconnected": "hsl(var(--status-disconnected))",
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        info: {
          DEFAULT: "hsl(var(--info))",
          foreground: "hsl(var(--info-foreground))",
        },
        favorite: "hsl(var(--favorite))",
        console: "hsl(var(--console))",
        "console-command": "hsl(var(--console-command))",
        "console-bg": "hsl(var(--console-bg))",
        "code-editor-bg": "hsl(var(--code-editor-bg))",
        overlay: "hsl(var(--overlay))",
        "badge-unread": {
          DEFAULT: "hsl(var(--badge-unread))",
          foreground: "hsl(var(--badge-unread-foreground))",
        },
        "badge-mention": {
          DEFAULT: "hsl(var(--badge-mention))",
          foreground: "hsl(var(--badge-mention-foreground))",
        },
        "toast-error": {
          DEFAULT: "hsl(var(--toast-error))",
          foreground: "hsl(var(--toast-error-foreground))",
          border: "hsl(var(--toast-error-border))",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "'Meshloom Marks'"],
        mono: ["var(--font-mono)", "'Meshloom Marks'"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
}
