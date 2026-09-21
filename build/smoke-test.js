/* Kwik Fat Cut localisation and chrome smoke test.
 *
 * Exercises region detection, the en-US spelling transform, proper noun
 * protection, Spanish fallback, the language switcher, the mobile nav, the
 * day/night shield, the reveal fallback and the September client changes,
 * all against the real pages.
 *
 * Run it after any change to kfc-i18n.js, kfc.js or the i18n folder:
 *
 *     npm install jsdom
 *     node build/smoke-test.js
 *
 * Exits non-zero on any failure, so it drops straight into CI.
 */
const fs = require('fs'), path = require('path'), { JSDOM } = require('jsdom');
const SITE = path.resolve(__dirname, '..');
const results = [];
function ok(name, cond, detail = '') { results.push([cond ? 'PASS' : 'FAIL', name, detail]); }

async function run(page, tz) {
  const html = fs.readFileSync(path.join(SITE, page), 'utf8');
  const dom = new JSDOM(html, {
    url: 'https://kwikfatcut.com/' + page,
    runScripts: 'outside-only',
    pretendToBeVisual: true
  });
  const w = dom.window;

  // Serve the i18n files from disk.
  w.fetch = (u) => {
    const p = path.join(SITE, u.split('?')[0]);
    if (!fs.existsSync(p)) {
      return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject() });
    }
    return Promise.resolve({
      ok: true, status: 200,
      json: () => Promise.resolve(JSON.parse(fs.readFileSync(p, 'utf8')))
    });
  };

  // Force the region by pinning the reported timezone.
  const realDTF = w.Intl.DateTimeFormat;
  w.Intl.DateTimeFormat = function (...a) {
    const i = new realDTF(...a);
    const ro = i.resolvedOptions.bind(i);
    i.resolvedOptions = () => Object.assign(ro(), { timeZone: tz });
    return i;
  };
  Object.defineProperty(w.navigator, 'languages', { value: ['en'], configurable: true });

  w.eval(fs.readFileSync(path.join(SITE, 'kfc.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(SITE, 'kfc-i18n.js'), 'utf8'));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  await new Promise(r => setTimeout(r, 400));
  return w;
}

(async () => {
  // --- locale detection and the spelling transform -----------------------
  let w = await run('learn-water.html', 'Europe/London');
  let t = w.document.body.textContent;
  ok('en-GB detected from Europe/London',
     w.document.documentElement.getAttribute('data-locale') === 'en-GB',
     w.document.documentElement.getAttribute('data-locale'));
  ok('en-GB keeps British spelling', /\bfibre\b/i.test(t) && !/\bfiber\b/i.test(t));

  w = await run('learn-water.html', 'America/New_York');
  t = w.document.body.textContent;
  ok('en-US detected from America/New_York',
     w.document.documentElement.getAttribute('data-locale') === 'en-US',
     w.document.documentElement.getAttribute('data-locale'));
  ok('en-US transforms fibre to fiber', /\bfiber\b/i.test(t) && !/\bfibre\b/i.test(t));
  ok('lang attribute set', w.document.documentElement.getAttribute('lang') === 'en-US');

  w = await run('learn-blood.html', 'America/New_York');
  t = w.document.body.textContent;
  ok('proper noun survives en-US transform', t.includes('World Health Organization'));
  ok('haemoglobin becomes hemoglobin', /hemoglobin/i.test(t) && !/haemoglobin/i.test(t));

  w = await run('learn-water.html', 'America/Havana');
  t = w.document.body.textContent;
  ok('es detected from America/Havana',
     w.document.documentElement.getAttribute('data-locale') === 'es',
     w.document.documentElement.getAttribute('data-locale'));
  ok('shared chrome translated',
     t.includes('Nosotros remamos') || t.includes('Aprende') || t.includes('Donar'));
  ok('untranslated page copy falls back to English', t.length > 2000);

  w = await run('index.html', 'America/Jamaica');
  ok('Jamaica maps to en-GB',
     w.document.documentElement.getAttribute('data-locale') === 'en-GB',
     w.document.documentElement.getAttribute('data-locale'));

  // --- chrome -------------------------------------------------------------
  w = await run('index.html', 'Europe/London');
  let d = w.document;
  ok('language switcher built', d.querySelectorAll('.lang-option').length === 3,
     d.querySelectorAll('.lang-option').length + ' options');
  ok('active locale marked in switcher', !!d.querySelector('.lang-option.active'));
  ok('mobile nav toggle present', !!d.querySelector('.nav-toggle'));
  ok('shield mode set', ['day', 'night'].includes(d.documentElement.getAttribute('data-shield')));
  ok('reveal blocks revealed',
     d.querySelectorAll('.reveal').length === 0 || d.querySelectorAll('.reveal.in').length > 0);
  ok('js class added', d.documentElement.classList.contains('js'));
  ok('exactly one top nav', d.querySelectorAll('nav:not(.footer-nav)').length === 1,
     d.querySelectorAll('nav:not(.footer-nav)').length + ' found');

  const w2 = await run('about.html', 'Europe/London');
  const marked = w2.document.querySelector('.nav-links a[aria-current="page"]');
  ok('current page marked in nav', marked && marked.getAttribute('href') === 'about.html',
     marked ? marked.getAttribute('href') : 'none');

  // --- September client changes -------------------------------------------
  ok('Donate CTA in the nav',
     (d.querySelector('.nav-btn') || {}).textContent === 'Donate',
     (d.querySelector('.nav-btn') || {}).textContent);
  ok('Donate points at the support page',
     (d.querySelector('.nav-btn') || {}).getAttribute &&
     d.querySelector('.nav-btn').getAttribute('href') === 'support.html');
  ok('no App Store or Google Play button on the homepage',
     !/App Store|Google Play/.test(d.body.textContent));
  ok('no #download links anywhere on the homepage',
     d.querySelectorAll('a[href*="#download"]').length === 0);

  const wf = await run('why-free.html', 'Europe/London');
  ok('premium card removed',
     !/Deeper, if you choose|Premium features/.test(wf.document.body.textContent));
  ok('three journey cards remain',
     wf.document.querySelectorAll('.journey-step').length === 3,
     wf.document.querySelectorAll('.journey-step').length + ' cards');
  ok('premium hook removed from the journey intro',
     !/go deeper with us/.test(wf.document.body.textContent));

  const sp = await run('support.html', 'Europe/London');
  const st = sp.document.body.textContent;
  ok('donation copy names WiPay and PayPal',
     st.includes('WiPay') && st.includes('PayPal'));

  // The donation page must stay internally consistent with whether payments
  // actually work. PAYMENTS_LIVE in build/migrate.py drives both the copy and
  // the button, so assert they agree rather than hardcoding one wording.
  // build/verify.py enforces the same rule against a real gateway check.
  const btn = sp.document.querySelector('.donate-btn');
  const buttonLive = btn && !btn.hasAttribute('disabled');
  const copyLive = st.includes('Secured by WiPay & PayPal') ||
                   st.includes('Donations are processed securely by');
  ok('donation copy and button agree on payment state',
     buttonLive === copyLive,
     'button ' + (buttonLive ? 'live' : 'disabled') +
     ', copy ' + (copyLive ? 'live' : 'pending'));
  if (!buttonLive) {
    ok('disabled donate button offers a working alternative',
       st.includes('kwik@kwikfatcut.com'));
  }

  // --- runtime switching, must not compound -------------------------------
  w = await run('learn-water.html', 'Europe/London');
  await w.kfcI18n.set('en-US');
  await new Promise(r => setTimeout(r, 250));
  ok('runtime switch GB to US', /\bfiber\b/i.test(w.document.body.textContent));
  await w.kfcI18n.set('en-GB');
  await new Promise(r => setTimeout(r, 250));
  ok('switch back is not cumulative',
     /\bfibre\b/i.test(w.document.body.textContent) &&
     !/\bfiber\b/i.test(w.document.body.textContent));

  let fails = 0;
  for (const [s, n, det] of results) {
    if (s === 'FAIL') fails++;
    console.log(`${s}  ${n}${det ? '  (' + det + ')' : ''}`);
  }
  console.log(`\n${results.length - fails}/${results.length} passed`);
  process.exit(fails ? 1 : 0);
})();
