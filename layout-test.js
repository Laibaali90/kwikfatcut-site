/* Kwik Fat Cut rendered-layout test.
 *
 * Renders real pages in Chromium and measures them. Every other check in this
 * repo reads the DOM or the text; this one is the only thing that sees what a
 * visitor actually sees.
 *
 * It exists because two bugs got past everything else by being invisible to
 * non-rendering checks:
 *
 *   1. The duplicate navbar. A bare `nav` selector set position:fixed;top:0,
 *      and the footer nav and the article table of contents inherited it, so
 *      three navs stacked across the top of the viewport. Counting <nav>
 *      elements in the DOM found "one header nav, one footer nav" and called
 *      it normal. Only a render shows them on top of each other.
 *
 *   2. The team page text collapse. A px max-width plus vw padding under
 *      border-box shrank the text column as the viewport grew, down to 66px
 *      at 5120px. Invisible at every width anyone had tested.
 *
 * Both are now asserted here, at the widths where they actually appear.
 *
 *     npm install playwright
 *     node build/layout-test.js
 *     node build/layout-test.js --shot     also write a homepage screenshot
 *
 * Exits non-zero on any failure.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const SITE = path.resolve(__dirname, '..');
const SHOT = process.argv.includes('--shot');

// The reported bug was at 1536. The wide widths catch the collapse class of
// fault; 390 catches the mobile menu.
const WIDTHS = [390, 768, 1280, 1536, 1920, 2560, 3840, 5120];

// Pages that exercise every layout shape on the site.
const SAMPLE = [
  'index.html',        // hero, full nav
  'team.html',         // .team-intro, the collapse bug
  'thank-you.html',    // .letter, same shape
  'learn-water.html',  // .article-wrap plus a .toc sidebar nav
  'why-free.html',     // card grids
  'support.html',      // donation form
  'foods-hub.html',    // generated index grid
  'our-roots.html',    // the 5 up roots grid
];

// Internal reference documents, noindexed and out of the sitemap. They are
// standalone tables, not site pages, and deliberately carry no site nav.
const NOT_SITE_PAGES = new Set([
  'kwik-fatcut-block-map.html',
]);

const results = [];
const seen = new Set();
let checks = 0;
function ok(name, cond, detail = '') {
  checks++;
  // Collapse identical passes so the log stays readable, but keep every
  // distinct failure, and count every check for the summary.
  const key = (cond ? 'P' : 'F') + name + '|' + detail;
  if (seen.has(key)) return;
  seen.add(key);
  results.push([cond ? 'PASS' : 'FAIL', name, detail]);
}

function allPages() {
  return fs.readdirSync(SITE).filter(f => f.endsWith('.html')).sort();
}

/* What the browser can tell us about a rendered page. */
const MEASURE = () => {
  const vw = window.innerWidth;
  const navs = [...document.querySelectorAll('nav')].map(n => {
    const cs = getComputedStyle(n);
    const r = n.getBoundingClientRect();
    return {
      cls: n.className || '',
      position: cs.position,
      top: Math.round(r.top),
      width: Math.round(r.width),
      height: Math.round(r.height),
      visible: cs.display !== 'none' && cs.visibility !== 'hidden' && r.height > 0,
    };
  });

  // Anything pinned across the top of the viewport, which is what a visitor
  // reads as "a navbar". The grain overlay is full-screen and not a bar.
  const pinned = [...document.querySelectorAll('body *')].filter(el => {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed') return false;
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (el.classList.contains('skip-link')) return false;
    const r = el.getBoundingClientRect();
    if (r.top > 8 || r.height < 20 || r.height > 200) return false;
    return r.width > vw * 0.5;           // spans the page, so it reads as a bar
  }).map(el => (el.tagName + '.' + (el.className || '(none)')).toLowerCase());

  // Is the first piece of hero content hidden behind the fixed nav? The nav
  // is position:fixed so it takes no space in flow, and a hero with
  // padding-top:0 slides straight under it on short viewports.
  let occluded = null;
  const bar = [...document.querySelectorAll('body *')].find(el => {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    return r.top <= 8 && r.height >= 20 && r.height <= 200 && r.width > vw * 0.5;
  });
  if (bar) {
    const first = document.querySelector(
      '.hero-eyebrow, .breadcrumb, .article-tag, .page-header h1, .hero h1, main h1');
    if (first) {
      const fr = first.getBoundingClientRect();
      const br = bar.getBoundingClientRect();
      occluded = fr.top < br.bottom
        ? { el: first.className || first.tagName, top: Math.round(fr.top),
            navBottom: Math.round(br.bottom) }
        : false;
    }
  }

  const textCols = {};
  for (const sel of ['.team-intro', '.letter', '.article-wrap']) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const cs = getComputedStyle(el);
    textCols[sel] = Math.round(
      el.getBoundingClientRect().width -
      parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
    );
  }

  return {
    navs,
    pinned,
    occluded,
    textCols,
    scrollW: document.documentElement.scrollWidth,
    clientW: document.documentElement.clientWidth,
    navToggleShown: (() => {
      const t = document.querySelector('.nav-toggle');
      return t ? getComputedStyle(t).display !== 'none' : null;
    })(),
    navLinksShown: (() => {
      const l = document.querySelector('.nav-links');
      if (!l) return null;
      const cs = getComputedStyle(l);
      return cs.visibility !== 'hidden' && cs.display !== 'none';
    })(),
  };
};

async function measure(page, file, width, height = 900) {
  await page.setViewportSize({ width, height });
  await page.goto('file://' + path.join(SITE, file), { waitUntil: 'load' });
  await page.waitForTimeout(160);
  return page.evaluate(MEASURE);
}

function assertPage(file, width, m) {
  const at = `${file} @${width}px`;
  const sitePage = !NOT_SITE_PAGES.has(file);

  // 1. Exactly one bar pinned across the top. Reference docs have none.
  const wantPinned = sitePage ? 1 : 0;
  ok('one fixed top bar', m.pinned.length === wantPinned,
     m.pinned.length === wantPinned ? ''
       : `${at}: ${m.pinned.length} -> ${m.pinned.join(', ')}`);

  // 2. Only the site nav may be fixed. Footer navs and the article table of
  //    contents must stay in flow.
  for (const n of m.navs) {
    const isSite = n.cls.split(/\s+/).includes('site-nav');
    if (!isSite && n.position === 'fixed') {
      ok('only the site nav is fixed', false,
         `${at}: nav.${n.cls || '(none)'} is position:fixed`);
    }
  }
  ok('only the site nav is fixed', true);

  // 3. No horizontal overflow. 1px of rounding slack.
  ok('no horizontal overflow', m.scrollW <= m.clientW + 1,
     m.scrollW <= m.clientW + 1 ? '' : `${at}: ${m.scrollW} > ${m.clientW}`);

  // 4. Text columns stay readable at every width. This is the team page bug.
  for (const [sel, w] of Object.entries(m.textCols)) {
    ok('text column stays readable', w >= 260,
       w >= 260 ? '' : `${at}: ${sel} is ${w}px wide`);
  }

  // 5. Hero content must never sit behind the fixed nav.
  if (m.occluded) {
    ok('hero clears the fixed nav', false,
       `${at}: ${m.occluded.el} at y=${m.occluded.top}, nav ends ${m.occluded.navBottom}`);
  } else if (m.occluded === false) {
    ok('hero clears the fixed nav', true);
  }

  // 6. Mobile gets a menu button; desktop gets the links.
  if (!sitePage) return;
  if (width <= 960) {
    ok('mobile shows the menu button', m.navToggleShown === true,
       m.navToggleShown === true ? '' : at);
  } else {
    ok('desktop shows the nav links', m.navLinksShown === true,
       m.navLinksShown === true ? '' : at);
    ok('desktop hides the menu button', m.navToggleShown === false,
       m.navToggleShown === false ? '' : at);
  }
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  // Every page at the width the client reported.
  for (const f of allPages()) {
    assertPage(f, 1536, await measure(page, f, 1536));
  }

  // The layout-representative sample at every width.
  for (const f of SAMPLE) {
    for (const w of WIDTHS) {
      if (w === 1536) continue;                 // already covered above
      assertPage(f, w, await measure(page, f, w));
    }
  }

  // Short viewports. This is where a hero with no top padding hides under the
  // fixed nav; it looks perfect on a tall monitor and broken on a laptop.
  for (const f of SAMPLE) {
    for (const h of [640, 720, 800]) {
      assertPage(f, 1536, await measure(page, f, 1536, h));
    }
  }

  if (SHOT) {
    await page.setViewportSize({ width: 1536, height: 900 });
    await page.goto('file://' + path.join(SITE, 'index.html'), { waitUntil: 'load' });
    await page.waitForTimeout(400);
    const out = path.join(SITE, 'build', 'homepage-1536.png');
    await page.screenshot({ path: out });
    console.log('screenshot: ' + path.relative(SITE, out));
  }

  await browser.close();

  const fails = results.filter(r => r[0] === 'FAIL');
  for (const [, name, detail] of results.filter(r => r[0] === 'PASS')) {
    console.log(`PASS  ${name}${detail ? '  (' + detail + ')' : ''}`);
  }
  for (const [, name, detail] of fails) {
    console.log(`FAIL  ${name}  ${detail}`);
  }
  console.log(`\n${checks - fails.length}/${checks} layout assertions passed ` +
              `across ${allPages().length} pages and ${WIDTHS.length} viewports`);
  if (fails.length) console.log('\nDO NOT PUSH.');
  process.exit(fails.length ? 1 : 0);
})();
