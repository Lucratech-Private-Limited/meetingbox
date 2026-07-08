/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
        },
        app: {
          page: '#0a1628',
          navbar: '#0d213d',
          deep: '#050d18',
          surface: '#112240',
          'surface-soft': '#152a48',
          raised: '#1a3558',
          border: '#1e3d5c',
          'border-light': '#2a5080',
          ink: '#e8f0fc',
          'ink-muted': '#b3c5df',
          'ink-subtle': '#8aa3c4',
          'ink-faint': '#6b86a8',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'app-glow': '0 25px 50px -12px rgba(2, 12, 28, 0.55)',
      },
    },
  },
  plugins: [],
}
