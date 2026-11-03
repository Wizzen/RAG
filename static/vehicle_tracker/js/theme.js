/* Run before styles: light by default; dark only when a visitor has chosen it. */
(() => {
  'use strict';
  const key = 'fos-theme';
  const root = document.documentElement;
  const normalize = value => value === 'dark' ? 'dark' : 'light';
  let initial = window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  try { const saved = localStorage.getItem(key); if (saved) initial = normalize(saved); } catch { /* Storage may be disabled. */ }
  // The same-origin Apex embed follows its host even when storage is unavailable.
  try {
    if (window.parent !== window && window.parent.ToolboxTheme) initial = window.parent.ToolboxTheme.current;
  } catch { /* Standalone/cross-origin embedding keeps its own preference. */ }

  function syncControls() {
    const light = root.dataset.theme === 'light';
    const label = light ? '切换至深色模式 · Switch to dark mode' : '切换至浅色模式 · Switch to light mode';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.hidden = false;
      button.setAttribute('aria-label', label);
      button.title = label;
      const text = button.querySelector('[data-theme-label]');
      if (text) text.textContent = light ? 'Dark mode' : 'Light mode';
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = light ? '#ffffff' : '#070d1a';
  }

  function setTheme(value, persist = true) {
    const theme = normalize(value);
    const changed = root.dataset.theme !== theme;
    root.dataset.theme = theme;
    if (persist) {
      try { localStorage.setItem(key, theme); } catch { /* The toggle still works in memory. */ }
    }
    syncControls();
    if (changed) window.dispatchEvent(new CustomEvent('toolbox:themechange', { detail: { theme } }));
  }

  root.dataset.theme = initial;
  window.ToolboxTheme = {
    get current() { return root.dataset.theme; },
    set: setTheme,
  };
  document.addEventListener('DOMContentLoaded', () => {
    syncControls();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => setTheme(root.dataset.theme === 'light' ? 'dark' : 'light'));
    });
  }, { once: true });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) setTheme(event.newValue, false);
  });
  try {
    if (window.parent !== window && window.parent.ToolboxTheme) {
      const followParent = event => setTheme(event.detail.theme, false);
      window.parent.addEventListener('toolbox:themechange', followParent);
      window.addEventListener('pagehide', () => window.parent.removeEventListener('toolbox:themechange', followParent));
      window.addEventListener('pageshow', () => {
        window.parent.addEventListener('toolbox:themechange', followParent);
        setTheme(window.parent.ToolboxTheme.current, false);
      });
    }
  } catch { /* A cross-origin parent cannot control this page. */ }
})();
