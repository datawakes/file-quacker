import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'

// Bundle fonts into the app so the portable .exe works offline.
// Only the weights we actually use are imported (keep the bundle leanish).
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'

import './style.css'

// Apply theme BEFORE Vue mounts to avoid flicker on dark OS.
// useTheme() in the app keeps this in sync afterwards.
;(() => {
  try {
    const stored = localStorage.getItem('fq.theme')
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    const effective = stored === 'light' ? 'light' : stored === 'dark' ? 'dark' : prefersDark ? 'dark' : 'light'
    const root = document.documentElement
    root.classList.toggle('dark', effective === 'dark')
    root.classList.toggle('light', effective === 'light')
    root.style.colorScheme = effective
  } catch { /* ignore */ }
})()

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
