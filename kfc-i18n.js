/* ==========================================================================
   Kwik Fat Cut localisation engine  (FIX 3)

   Detects the visitor's region, picks a locale, and rewrites the page text.

   Design notes, because the client wants to add languages and regions later:

   1. Nothing is hardcoded to three locales. i18n/locales.json is the only
      place a locale or a region is declared. Adding Portuguese means adding
      one entry there and one folder under i18n/. No change to this file.

   2. Two locale modes, so adding a variant is cheap and adding a language is
      honest about needing a translator:
        transform   an en-GB source rewritten by a word dictionary. Covers
                    spelling variants such as en-US. No translator needed.
        dictionary  a real translation, keyed by a hash of the source text.
                    Any string without a translation falls back to English,
                    so a half-translated locale still renders a whole page.

   3. Source text is keyed by content hash, not by markup. There are no
      data-i18n attributes anywhere in the 50 pages, and adding a locale
      never requires touching a page. The extractor in
      build/extract-strings.py produces the same hashes as this file.

   4. Detection is local. No geo-IP service, no third-party request, no
      cookie. The site's own principle is no tracking, and a paid lookup on
      every page view would also undo the Phase 1 speed work.
   ========================================================================== */
(function () {
  'use strict';

  var CONFIG_URL = 'i18n/locales.json';
  var STORE_KEY = 'kfc-locale';

  var state = { config: null, locale: null, nodes: null };

  /* ------------------------------------------------------------------
     FNV-1a 32 bit. Must stay byte-for-byte identical to the Python
     implementation in build/extract-strings.py or every key misses.
     ------------------------------------------------------------------ */
  function hash(str) {
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) >>> 0;
    }
    return h.toString(16).padStart(8, '0');
  }

  /* Collapse runs of whitespace so a key does not depend on indentation. */
  function normalise(text) {
    return text.replace(/\s+/g, ' ').trim();
  }

  /* ------------------------------------------------------------------
     Storage. Wrapped because private browsing throws on access.
     ------------------------------------------------------------------ */
  function stored(value) {
    try {
      if (value === undefined) return window.localStorage.getItem(STORE_KEY);
      window.localStorage.setItem(STORE_KEY, value);
    } catch (e) { /* no storage, detection runs every visit instead */ }
    return null;
  }

  /* ------------------------------------------------------------------
     Region detection.

     The timezone gives a region without a network call and without asking
     permission. Intl exposes it on every browser this site supports.
     ------------------------------------------------------------------ */
  var TZ_REGION = {
    'Europe/London': 'GB', 'Europe/Dublin': 'IE', 'Europe/Paris': 'FR',
    'Europe/Berlin': 'DE', 'Europe/Amsterdam': 'NL', 'Europe/Brussels': 'BE',
    'Europe/Rome': 'IT', 'Europe/Lisbon': 'PT', 'Europe/Stockholm': 'SE',
    'Europe/Oslo': 'NO', 'Europe/Copenhagen': 'DK', 'Europe/Helsinki': 'FI',
    'Europe/Warsaw': 'PL', 'Europe/Zurich': 'CH', 'Europe/Vienna': 'AT',
    'Europe/Athens': 'GR', 'Europe/Prague': 'CZ', 'Europe/Madrid': 'ES',
    'America/Jamaica': 'JM', 'America/Havana': 'CU', 'America/Nassau': 'BS',
    'America/Port_of_Spain': 'TT', 'America/Barbados': 'BB',
    'America/Guyana': 'GY', 'America/Belize': 'BZ',
    'America/Santo_Domingo': 'DO', 'America/Puerto_Rico': 'PR',
    'America/Mexico_City': 'MX', 'America/Bogota': 'CO', 'America/Lima': 'PE',
    'America/Caracas': 'VE', 'America/Santiago': 'CL',
    'America/Argentina/Buenos_Aires': 'AR', 'America/Guayaquil': 'EC',
    'America/Guatemala': 'GT', 'America/La_Paz': 'BO',
    'America/Tegucigalpa': 'HN', 'America/Asuncion': 'PY',
    'America/El_Salvador': 'SV', 'America/Managua': 'NI',
    'America/Costa_Rica': 'CR', 'America/Panama': 'PA',
    'America/Montevideo': 'UY',
    'Africa/Johannesburg': 'ZA', 'Africa/Lagos': 'NG', 'Africa/Accra': 'GH',
    'Africa/Nairobi': 'KE',
    'Australia/Sydney': 'AU', 'Pacific/Auckland': 'NZ',
    'Asia/Kolkata': 'IN', 'Asia/Calcutta': 'IN'
  };

  function detectRegion() {
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (TZ_REGION[tz]) return TZ_REGION[tz];
      /* Anything else in America/ that is not listed is almost always the
         continental US or Canada. The region map decides, not this line. */
      if (tz && tz.indexOf('America/') === 0) return 'US';
    } catch (e) { /* Intl unavailable, fall through to language */ }
    return null;
  }

  function detectLocale(config) {
    /* 1. An explicit choice always wins, and is remembered. */
    var saved = stored();
    if (saved && config.locales[saved]) return saved;

    /* 2. ?lang= for testing and for shareable localised links. */
    var param = new URLSearchParams(window.location.search).get('lang');
    if (param && config.locales[param]) return param;

    /* 3. Region. */
    var region = detectRegion();
    if (region && config.regions[region]) return config.regions[region];

    /* 4. Browser language, exact match then prefix. */
    var langs = navigator.languages || [navigator.language || ''];
    for (var i = 0; i < langs.length; i++) {
      var l = langs[i];
      if (config.languages[l]) return config.languages[l];
      var base = l.split('-')[0];
      if (config.languages[base]) return config.languages[base];
    }

    return config.fallback;
  }

  /* ------------------------------------------------------------------
     Text node collection.

     Script, style, code and pre are skipped: rewriting a CSS keyword or a
     code sample would be a bug, not a translation. The original text is
     cached on first walk so switching locale never compounds edits.
     ------------------------------------------------------------------ */
  var SKIP = { SCRIPT: 1, STYLE: 1, CODE: 1, PRE: 1, NOSCRIPT: 1, SVG: 1 };

  function collect() {
    if (state.nodes) return state.nodes;

    var walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT,
      {
        acceptNode: function (node) {
          var p = node.parentNode;
          while (p && p !== document.body) {
            if (SKIP[p.nodeName.toUpperCase()]) return NodeFilter.FILTER_REJECT;
            p = p.parentNode;
          }
          return node.nodeValue && node.nodeValue.trim()
            ? NodeFilter.FILTER_ACCEPT
            : NodeFilter.FILTER_REJECT;
        }
      }
    );

    var nodes = [];
    var n;
    while ((n = walker.nextNode())) {
      nodes.push({ node: n, source: n.nodeValue });
    }
    state.nodes = nodes;
    return nodes;
  }

  /* ------------------------------------------------------------------
     Transform mode: whole-word substitution, case preserved.
     ------------------------------------------------------------------ */
  function matchCase(source, replacement) {
    if (source === source.toUpperCase() && source !== source.toLowerCase()) {
      return replacement.toUpperCase();
    }
    if (source[0] === source[0].toUpperCase()) {
      return replacement.charAt(0).toUpperCase() + replacement.slice(1);
    }
    return replacement;
  }

  function escapeRe(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function applyTransform(words, protectedPhrases) {
    var pattern = new RegExp(
      '\\b(' + Object.keys(words).sort(function (a, b) {
        return b.length - a.length;      /* longest first, so non-haem beats haem */
      }).join('|') + ')\\b', 'gi'
    );

    /* Proper nouns are masked before substitution. The World Health
       Organization is called that in every locale, and a future entry such
       as Labour Party has to survive the trip in the other direction. */
    var guard = protectedPhrases && protectedPhrases.length
      ? new RegExp(protectedPhrases.slice().sort(function (a, b) {
          return b.length - a.length;
        }).map(escapeRe).join('|'), 'g')
      : null;

    collect().forEach(function (item) {
      var text = item.source;
      var shelf = [];

      if (guard) {
        text = text.replace(guard, function (m) {
          shelf.push(m);
          return '\u0000' + (shelf.length - 1) + '\u0000';
        });
      }

      text = text.replace(pattern, function (m) {
        var hit = words[m.toLowerCase()];
        return hit ? matchCase(m, hit) : m;
      });

      if (guard) {
        text = text.replace(/\u0000(\d+)\u0000/g, function (m, i) {
          return shelf[Number(i)];
        });
      }

      item.node.nodeValue = text;
    });
  }

  /* ------------------------------------------------------------------
     Dictionary mode: hash lookup with fallback to the English source.
     ------------------------------------------------------------------ */
  function applyDictionary(strings) {
    var hit = 0, miss = 0;

    collect().forEach(function (item) {
      var key = hash(normalise(item.source));
      var entry = strings[key];
      if (entry && entry.t) {
        /* Keep the original leading and trailing whitespace so inline
           elements do not run into each other. */
        var lead = item.source.match(/^\s*/)[0];
        var tail = item.source.match(/\s*$/)[0];
        item.node.nodeValue = lead + entry.t + tail;
        hit++;
      } else {
        item.node.nodeValue = item.source;
        miss++;
      }
    });

    if (window.console && miss) {
      console.info('[kfc-i18n] ' + hit + ' translated, ' + miss +
        ' falling back to English on this page.');
    }
  }

  /* ------------------------------------------------------------------
     Applying a locale.
     ------------------------------------------------------------------ */
  function reset() {
    collect().forEach(function (item) { item.node.nodeValue = item.source; });
  }

  function load(url) {
    return fetch(url, { cache: 'force-cache' }).then(function (r) {
      if (!r.ok) throw new Error(url + ' ' + r.status);
      return r.json();
    });
  }

  function apply(code) {
    var config = state.config;
    var def = config.locales[code];
    if (!def) return Promise.resolve();

    document.documentElement.setAttribute('lang', def.lang);
    document.documentElement.setAttribute('dir', def.dir || 'ltr');
    document.documentElement.setAttribute('data-locale', code);
    state.locale = code;
    markSwitcher(code);

    if (def.mode === 'source') {
      reset();
      return Promise.resolve();
    }

    if (def.mode === 'transform') {
      return Promise.all([
        load('i18n/' + def.transform + '.json'),
        load('i18n/protected.json').catch(function () { return { phrases: [] }; })
      ]).then(function (parts) {
        reset();
        applyTransform(parts[0].words, parts[1].phrases);
      });
    }

    /* dictionary: shared chrome plus this page's own strings. Two small
       files rather than one large one, so a visitor never downloads the
       translation of 49 pages they are not reading. */
    var page = (window.location.pathname.split('/').pop() || 'index.html')
      .replace(/\.html$/, '');
    var dir = 'i18n/' + def.dictionary + '/';

    return Promise.all([
      load(dir + '_common.json').catch(function () { return { strings: {} }; }),
      load(dir + page + '.json').catch(function () { return { strings: {} }; })
    ]).then(function (parts) {
      var merged = {};
      parts.forEach(function (p) {
        Object.keys(p.strings || {}).forEach(function (k) { merged[k] = p.strings[k]; });
      });
      applyDictionary(merged);
    });
  }

  function setLocale(code) {
    stored(code);
    return apply(code).catch(function (e) {
      if (window.console) console.warn('[kfc-i18n]', e);
    });
  }

  /* ------------------------------------------------------------------
     Switcher UI. Built from the config, so a new locale appears here
     automatically.
     ------------------------------------------------------------------ */
  function markSwitcher(code) {
    var buttons = document.querySelectorAll('.lang-option');
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].getAttribute('data-locale') === code;
      buttons[i].classList.toggle('active', on);
      buttons[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    var label = document.querySelector('.lang-current');
    if (label && state.config.locales[code]) {
      label.textContent = state.config.locales[code].short;
    }
  }

  function buildSwitcher() {
    var mount = document.querySelector('.lang-switch');
    if (!mount) return;

    var codes = Object.keys(state.config.locales);
    var html = '<button class="lang-toggle" type="button" aria-expanded="false" ' +
      'aria-label="Language"><span class="lang-current">EN</span></button>' +
      '<div class="lang-menu" role="group" aria-label="Choose a language">';

    codes.forEach(function (code) {
      html += '<button class="lang-option" type="button" data-locale="' + code +
        '" aria-pressed="false">' + state.config.locales[code].label + '</button>';
    });
    mount.innerHTML = html + '</div>';

    var toggle = mount.querySelector('.lang-toggle');
    var menu = mount.querySelector('.lang-menu');

    toggle.addEventListener('click', function () {
      var open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      menu.classList.toggle('open', open);
    });

    mount.addEventListener('click', function (e) {
      var btn = e.target.closest('.lang-option');
      if (!btn) return;
      setLocale(btn.getAttribute('data-locale'));
      toggle.setAttribute('aria-expanded', 'false');
      menu.classList.remove('open');
    });

    document.addEventListener('click', function (e) {
      if (!mount.contains(e.target)) {
        toggle.setAttribute('aria-expanded', 'false');
        menu.classList.remove('open');
      }
    });
  }

  /* ------------------------------------------------------------------
     Boot
     ------------------------------------------------------------------ */
  function init() {
    load(CONFIG_URL).then(function (config) {
      state.config = config;
      buildSwitcher();
      return apply(detectLocale(config));
    }).catch(function (e) {
      /* A failed config leaves the English source exactly as served.
         Localisation is an enhancement; it must never blank the page. */
      if (window.console) console.warn('[kfc-i18n] disabled:', e);
    });
  }

  window.kfcI18n = {
    set: setLocale,
    get: function () { return state.locale; },
    hash: hash
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
