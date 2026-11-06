/* Appearance: mode, three-colour presets, and customisation.
 *
 * The interface is built on three accent colours, one per layer of the scent
 * pyramid, and every theme sets all three:
 *
 *   main   (--color-primary)  heart notes, buttons, the score, "Love it"
 *   accent (--color-accent)   top notes, "Maybe"
 *   third  (--color-success)  base notes, "Like it"
 *
 * On top of the colours: background tone, font pairing, corners, spacing density
 * and text size. The panel's preview uses the page's real classes, so it shows
 * exactly what the page will look like.
 *
 * Loaded in <head> so the saved theme is applied before first paint — no flash
 * of the default when you have picked something else.
 *
 * Colours are derived here in plain sRGB hex rather than with CSS color-mix(), so
 * the readable text colour on top of a custom colour (white or near-black) can be
 * computed from the colour that actually ends up on screen.
 *
 * Saved per browser in localStorage: a display preference, not part of the log,
 * so it is deliberately not in the CSV backup.
 *
 * Exposed as window.Sillage.Theme.
 */
(function (root, document) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};
  var KEY = 'sillage.theme.v1';

  /* ── options ─────────────────────────────────────────────────────────────── */

  /* Background tones: full surface + text palettes per mode. Warm is the original. */
  var TONES = {
    warm: {
      label: 'Warm',
      light: { bg: '#f7f5f2', surface: '#faf9f6', surface2: '#fdfcfa', offset: '#f0ede8',
        border: '#dedad3', divider: '#e8e4de', text: '#2a251e', muted: '#7a7469', faint: '#b8b3ac' },
      dark: { bg: '#171412', surface: '#1d1b18', surface2: '#222019', offset: '#2a2720',
        border: '#3a3730', divider: '#2e2b24', text: '#d5d0c8', muted: '#857f76', faint: '#5c5853' }
    },
    neutral: {
      label: 'Neutral',
      light: { bg: '#f4f4f4', surface: '#fafafa', surface2: '#ffffff', offset: '#ececec',
        border: '#dadada', divider: '#e5e5e5', text: '#212121', muted: '#6b6b6b', faint: '#ababab' },
      dark: { bg: '#121212', surface: '#1a1a1a', surface2: '#202020', offset: '#272727',
        border: '#373737', divider: '#2b2b2b', text: '#d8d8d8', muted: '#8c8c8c', faint: '#5c5c5c' }
    },
    cool: {
      label: 'Cool',
      light: { bg: '#f1f4f8', surface: '#f8fafc', surface2: '#fcfdfe', offset: '#e8edf3',
        border: '#d4dce5', divider: '#e1e7ee', text: '#1d2732', muted: '#64707d', faint: '#a6b1bd' },
      dark: { bg: '#0f141a', surface: '#151b22', surface2: '#1a2129', offset: '#212a33',
        border: '#303b47', divider: '#26303a', text: '#cfd8e1', muted: '#7d8a97', faint: '#4f5b67' }
    }
  };

  /* Three colours per mode. Dark-mode colours are lighter so they keep contrast
   * on dark surfaces. Each preset also suggests a background tone. */
  var PRESETS = [
    { id: 'amethyst', name: 'Amethyst', tone: 'warm',
      light: { primary: '#7c4d8a', accent: '#c97d3a', third: '#437a22' },
      dark: { primary: '#b87ec8', accent: '#e0a050', third: '#6daa45' } },
    { id: 'rose', name: 'Rose', tone: 'warm',
      light: { primary: '#a63d5c', accent: '#b9773a', third: '#55803a' },
      dark: { primary: '#e88aa6', accent: '#e6a65c', third: '#93c46d' } },
    { id: 'sage', name: 'Sage', tone: 'neutral',
      light: { primary: '#4a7a4f', accent: '#b0733a', third: '#7a5a9a' },
      dark: { primary: '#8fc493', accent: '#dba25e', third: '#b79ad6' } },
    { id: 'ocean', name: 'Ocean', tone: 'cool',
      light: { primary: '#2d6a92', accent: '#c26d3a', third: '#2f8068' },
      dark: { primary: '#7cb8de', accent: '#e59a60', third: '#6fc7a5' } },
    { id: 'saffron', name: 'Saffron', tone: 'warm',
      light: { primary: '#a35a12', accent: '#7c4d8a', third: '#3f7a5a' },
      dark: { primary: '#eba65a', accent: '#c79ad6', third: '#7cc49c' } },
    { id: 'noir', name: 'Noir', tone: 'neutral',
      light: { primary: '#2f2b27', accent: '#a5621c', third: '#5d6b3a' },
      dark: { primary: '#e2dcd2', accent: '#e0a050', third: '#a9c27a' } },
    { id: 'orchard', name: 'Orchard', tone: 'warm',
      light: { primary: '#b0463a', accent: '#b5841c', third: '#4f7d32' },
      dark: { primary: '#ef8a7c', accent: '#e8c060', third: '#9acb72' } },
    { id: 'lagoon', name: 'Lagoon', tone: 'cool',
      light: { primary: '#5b4fb3', accent: '#1f8a9a', third: '#b24f86' },
      dark: { primary: '#a79cf0', accent: '#6ccfdc', third: '#ec92c4' } }
  ];

  var FONTS = {
    classic: { label: 'Classic', hint: 'Serif headings',
      display: "'Instrument Serif', Georgia, serif", body: "'DM Sans', 'Helvetica Neue', sans-serif" },
    clean: { label: 'Clean', hint: 'All sans',
      display: "'DM Sans', 'Helvetica Neue', sans-serif", body: "'DM Sans', 'Helvetica Neue', sans-serif" },
    bookish: { label: 'Bookish', hint: 'All serif',
      display: "Georgia, 'Times New Roman', serif", body: "Georgia, 'Times New Roman', serif" },
    system: { label: 'System', hint: 'Device font',
      display: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      body: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif" }
  };

  var RADIUS = { sharp: 0.35, soft: 1, round: 1.7 };
  var SCALE = { small: 0.92, medium: 1, large: 1.1 };
  var DENSITY = { compact: 0.8, cozy: 1, airy: 1.2 };

  var DEFAULTS = {
    mode: 'system', preset: 'amethyst', tone: 'warm', font: 'classic',
    custom: { primary: '#7c4d8a', accent: '#c97d3a', third: '#437a22' },
    radius: 'soft', scale: 'medium', density: 'cozy'
  };

  var state = load();
  var media = root.matchMedia ? root.matchMedia('(prefers-color-scheme: dark)') : null;
  var listeners = [];

  /* ── storage ─────────────────────────────────────────────────────────────── */

  function clone(v) { return JSON.parse(JSON.stringify(v)); }

  function load() {
    var saved = null;
    try { saved = JSON.parse(root.localStorage.getItem(KEY) || 'null'); } catch (e) { saved = null; }
    var s = clone(DEFAULTS);
    if (saved && typeof saved === 'object') {
      if (['light', 'dark', 'system'].indexOf(saved.mode) !== -1) s.mode = saved.mode;
      if (saved.preset === 'custom' || presetById(saved.preset)) s.preset = saved.preset;
      if (saved.custom && typeof saved.custom === 'object') {
        /* Older saves had two colours; the third falls back to the default. */
        ['primary', 'accent', 'third'].forEach(function (k) {
          if (isHex(saved.custom[k])) s.custom[k] = saved.custom[k];
        });
      }
      if (TONES[saved.tone]) s.tone = saved.tone;
      if (FONTS[saved.font]) s.font = saved.font;
      if (RADIUS[saved.radius]) s.radius = saved.radius;
      if (SCALE[saved.scale]) s.scale = saved.scale;
      if (DENSITY[saved.density]) s.density = saved.density;
    }
    return s;
  }

  function persist() {
    try { root.localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }

  /* ── colour maths (sRGB hex) ─────────────────────────────────────────────── */

  function isHex(v) { return typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v); }

  function toRgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function toHex(rgb) {
    return '#' + rgb.map(function (c) {
      return Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, '0');
    }).join('');
  }

  /* t = how much of b: mix(a, b, 0.2) is 80% a, 20% b. */
  function mix(a, b, t) {
    var x = toRgb(a), y = toRgb(b);
    return toHex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * t; }));
  }

  function luminance(hex) {
    var c = toRgb(hex).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function contrast(a, b) {
    var x = luminance(a), y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  }

  /* White or near-black text, whichever reads better on this background. */
  function onColor(bg) {
    return contrast(bg, '#ffffff') >= contrast(bg, '#1d1b18') ? '#ffffff' : '#1d1b18';
  }

  /* Nudge a colour lighter or darker until it reaches 3:1 against the surface —
   * enough for icons, borders and the bold labels these colours are used for. */
  function ensureContrast(hex, surface) {
    var towards = luminance(surface) > 0.5 ? '#000000' : '#ffffff';
    var out = hex;
    for (var i = 0; i < 12 && contrast(out, surface) < 3; i++) out = mix(out, towards, 0.12);
    return out;
  }

  /* ── resolve & apply ─────────────────────────────────────────────────────── */

  function presetById(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i];
    return null;
  }

  function resolvedMode() {
    if (state.mode !== 'system') return state.mode;
    return media && media.matches ? 'dark' : 'light';
  }

  function surfaces(mode) { return TONES[state.tone][mode]; }

  function colours(mode) {
    var preset = presetById(state.preset);
    if (preset) return preset[mode];
    /* Custom colours are picked once; each mode gets a contrast-safe version. */
    var surface = surfaces(mode).surface;
    return {
      primary: ensureContrast(state.custom.primary, surface),
      accent: ensureContrast(state.custom.accent, surface),
      third: ensureContrast(state.custom.third, surface)
    };
  }

  function tokens(mode) {
    var c = colours(mode), s = surfaces(mode), dark = mode === 'dark';
    var tint = dark ? 0.24 : 0.14;
    return {
      '--color-bg': s.bg,
      '--color-surface': s.surface,
      '--color-surface-2': s.surface2,
      '--color-surface-offset': s.offset,
      '--color-border': s.border,
      '--color-divider': s.divider,
      '--color-text': s.text,
      '--color-text-muted': s.muted,
      '--color-text-faint': s.faint,
      '--color-primary': c.primary,
      '--color-primary-hover': mix(c.primary, dark ? '#ffffff' : '#000000', 0.15),
      '--color-primary-active': mix(c.primary, dark ? '#ffffff' : '#000000', 0.28),
      '--color-primary-highlight': mix(s.surface, c.primary, tint),
      '--color-on-primary': onColor(c.primary),
      '--color-accent': c.accent,
      '--color-accent-highlight': mix(s.surface, c.accent, tint),
      '--color-success': c.third,
      '--color-success-highlight': mix(s.surface, c.third, tint)
    };
  }

  function css(mode) {
    var t = tokens(mode);
    return ':root[data-theme="' + mode + '"]{' + Object.keys(t).map(function (k) {
      return k + ':' + t[k];
    }).join(';') + '}';
  }

  function apply() {
    var html = document.documentElement;
    var mode = resolvedMode();
    html.setAttribute('data-theme', mode);
    html.style.setProperty('--radius-scale', String(RADIUS[state.radius]));
    html.style.setProperty('--font-scale', String(SCALE[state.scale]));
    html.style.setProperty('--density', String(DENSITY[state.density]));
    html.style.setProperty('--font-display', FONTS[state.font].display);
    html.style.setProperty('--font-body', FONTS[state.font].body);

    var tag = document.getElementById('sillageThemeVars');
    if (!tag) {
      tag = document.createElement('style');
      tag.id = 'sillageThemeVars';
      (document.head || html).appendChild(tag);
    }
    tag.textContent = css('light') + css('dark');

    updateFavicon(mode);

    /* Browser chrome (mobile address bar) follows the surface. */
    document.querySelectorAll('meta[name="theme-color"]').forEach(function (m) {
      m.setAttribute('content', surfaces(mode).surface);
      m.removeAttribute('media');
    });
    listeners.forEach(function (fn) { fn(mode, state); });
  }

  /* The tab icon is the header logo, redrawn in the active theme's colours on a
   * surface-coloured disc so it stays visible on light and dark browser tabs. */
  function faviconSvg(mode) {
    var c = colours(mode), surface = surfaces(mode).surface;
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" fill="none">' +
      '<circle cx="14" cy="14" r="12.5" fill="' + surface + '" stroke="' + c.primary + '" stroke-width="2.2"/>' +
      '<ellipse cx="14" cy="14" rx="5.5" ry="8.6" stroke="' + c.accent + '" stroke-width="1.9" transform="rotate(30 14 14)"/>' +
      '<circle cx="14" cy="14" r="3" fill="' + c.primary + '"/>' +
      '<circle cx="14" cy="4.6" r="1.6" fill="' + c.accent + '"/></svg>';
  }

  function updateFavicon(mode) {
    var link = document.getElementById('favicon');
    if (!link) {
      link = document.createElement('link');
      link.id = 'favicon';
      link.rel = 'icon';
      link.type = 'image/svg+xml';
      (document.head || document.documentElement).appendChild(link);
    }
    link.href = 'data:image/svg+xml,' + encodeURIComponent(faviconSvg(mode));
  }

  function set(patch) {
    Object.keys(patch).forEach(function (k) { state[k] = patch[k]; });
    persist();
    apply();
  }

  if (media) {
    var onSystemChange = function () { if (state.mode === 'system') apply(); };
    if (media.addEventListener) media.addEventListener('change', onSystemChange);
    else if (media.addListener) media.addListener(onSystemChange);
  }

  apply(); /* before first paint */

  /* ── panel ───────────────────────────────────────────────────────────────── */

  function $(id) { return document.getElementById(id); }

  function swatches(c) {
    return '<span class="theme-swatches" aria-hidden="true">' +
      '<span class="theme-swatch" style="background:' + c.primary + '"></span>' +
      '<span class="theme-swatch" style="background:' + c.accent + '"></span>' +
      '<span class="theme-swatch" style="background:' + c.third + '"></span></span>';
  }

  /* Segmented radio groups generated from an options table. */
  function segmented(name, table, current) {
    return Object.keys(table).map(function (key) {
      var id = name + '-' + key;
      var opt = table[key];
      return '<input type="radio" name="' + name + '" id="' + id + '" value="' + key + '"' +
        (key === current ? ' checked' : '') + '>' +
        '<label for="' + id + '"' + (opt.style ? ' style="' + opt.style + '"' : '') +
        (opt.hint ? ' title="' + opt.hint + '"' : '') + '>' + opt.label + '</label>';
    }).join('');
  }

  var MODE_OPTS = { light: { label: '☀ Light' }, dark: { label: '☾ Dark' }, system: { label: 'Auto' } };
  var RADIUS_OPTS = { sharp: { label: 'Sharp' }, soft: { label: 'Soft' }, round: { label: 'Round' } };
  var DENSITY_OPTS = { compact: { label: 'Compact' }, cozy: { label: 'Cozy' }, airy: { label: 'Airy' } };
  var SCALE_OPTS = {
    small: { label: 'Aa', style: 'font-size:0.85em', hint: 'Small' },
    medium: { label: 'Aa', hint: 'Medium' },
    large: { label: 'Aa', style: 'font-size:1.15em', hint: 'Large' }
  };

  function renderPanel() {
    var panel = $('themePanel');
    if (!panel) return;
    var mode = resolvedMode();

    $('themeModeSeg').innerHTML = segmented('themeMode', MODE_OPTS, state.mode);
    $('themeToneSeg').innerHTML = segmented('themeTone', TONES, state.tone);
    $('themeFontSeg').innerHTML = segmented('themeFont', FONTS, state.font);
    $('themeRadiusSeg').innerHTML = segmented('themeRadius', RADIUS_OPTS, state.radius);
    $('themeDensitySeg').innerHTML = segmented('themeDensity', DENSITY_OPTS, state.density);
    $('themeScaleSeg').innerHTML = segmented('themeScale', SCALE_OPTS, state.scale);

    $('themePresets').innerHTML = PRESETS.map(function (p) {
      var on = state.preset === p.id;
      return '<button type="button" class="theme-preset' + (on ? ' active' : '') +
        '" data-preset="' + p.id + '" aria-pressed="' + on + '">' + swatches(p[mode]) +
        '<span class="theme-preset-name">' + p.name + '</span></button>';
    }).join('') +
      '<button type="button" class="theme-preset' + (state.preset === 'custom' ? ' active' : '') +
      '" data-preset="custom" aria-pressed="' + (state.preset === 'custom') + '">' +
      swatches(colours(mode)) + '<span class="theme-preset-name">Custom</span></button>';

    /* Pickers show the colours in use, so tweaking starts from what you see. */
    var live = colours('light');
    if (state.preset === 'custom') live = state.custom;
    [['themePrimary', 'primary'], ['themeAccent', 'accent'], ['themeThird', 'third']].forEach(function (pair) {
      $(pair[0]).value = live[pair[1]];
      $(pair[0] + 'Hex').textContent = live[pair[1]].toUpperCase();
    });
  }

  function initPanel() {
    var panel = $('themePanel'), open = $('themeBtn');
    if (!panel || !open) return;

    open.addEventListener('click', function () {
      renderPanel();
      if (panel.showModal) panel.showModal(); else panel.setAttribute('open', '');
    });
    $('themeClose').addEventListener('click', function () { panel.close(); });
    panel.addEventListener('click', function (event) {
      if (event.target === panel) panel.close(); /* backdrop */
    });

    var FIELDS = {
      themeMode: 'mode', themeTone: 'tone', themeFont: 'font',
      themeRadius: 'radius', themeDensity: 'density', themeScale: 'scale'
    };
    panel.addEventListener('change', function (event) {
      var field = FIELDS[event.target.name];
      if (!field) return;
      var patch = {};
      patch[field] = event.target.value;
      set(patch);
      renderPanel();
      var again = panel.querySelector('input[name="' + event.target.name + '"]:checked');
      if (again) again.focus(); /* keep keyboard focus after the re-render */
    });

    /* Colour pickers update live while dragging. Choosing one switches to Custom. */
    ['themePrimary', 'themeAccent', 'themeThird'].forEach(function (id) {
      $(id).addEventListener('input', function () {
        set({
          preset: 'custom',
          custom: { primary: $('themePrimary').value, accent: $('themeAccent').value, third: $('themeThird').value }
        });
        renderPanel();
      });
    });

    $('themePresets').addEventListener('click', function (event) {
      var btn = event.target.closest('[data-preset]');
      if (!btn) return;
      var p = presetById(btn.dataset.preset);
      /* A preset brings its suggested background tone and seeds the pickers. */
      set(p ? { preset: p.id, tone: p.tone, custom: clone(p.light) } : { preset: 'custom' });
      renderPanel();
    });

    $('themeReset').addEventListener('click', function () {
      set(clone(DEFAULTS));
      renderPanel();
    });

    onChange(function () { if (panel.open) renderPanel(); });
  }

  function onChange(fn) { listeners.push(fn); }

  /* Header quick toggle: flips to the opposite of what is showing now. */
  function toggleMode() {
    set({ mode: resolvedMode() === 'dark' ? 'light' : 'dark' });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPanel);
  } else {
    initPanel();
  }

  Sillage.Theme = {
    PRESETS: PRESETS,
    TONES: TONES,
    FONTS: FONTS,
    get: function () { return clone(state); },
    set: set,
    mode: resolvedMode,
    toggleMode: toggleMode,
    onChange: onChange,
    /* exposed for tests */
    _mix: mix, _contrast: contrast, _onColor: onColor, _ensureContrast: ensureContrast, _tokens: tokens
  };
})(window, document);
