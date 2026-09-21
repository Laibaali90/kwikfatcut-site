# Kwik Fat Cut, Phase 1

Current status, 21 September, r4. This supersedes every earlier version.

Built on a fresh pull of GitHub `main`, which is byte-identical to the original
package. Nothing has been pushed.

**Before any push, run all five:**

    python3 build/migrate.py
    python3 build/build-hubs.py
    python3 build/verify.py        # static: JS syntax, structure, links, payment honesty
    node build/layout-test.js      # rendered: Chromium, 8 viewports
    node build/smoke-test.js       # behaviour: locale detection, switching

Each exits non-zero on failure. The build steps are idempotent and
order-independent.

`layout-test.js` is new and it is the one that matters most. It renders pages
in real Chromium and measures them. Everything else reads the DOM or the text,
which is how three separate bugs got past review.

---

## 1. Five bugs found in review, all now resolved in code

### 1a. I broke the inline JavaScript on 47 pages, and have fixed it

The step in `migrate.py` that strips the duplicated scroll-reveal snippet
matched the opening statement and then scanned forward for the end with a
non-greedy `.*?}\s*\)\s*;`. That terminated early, at the `}});` inside the
observer's own callback, and left this behind:

    },{threshold:0.07});

A stray fragment is a syntax error, and a syntax error anywhere in a `<script>`
block kills **every function declared in it**. So `shareKFC`, `copyURL`,
`selectAmount` and the donation page's `handleDonate` all silently stopped
working on 47 of 50 pages.

The smoke test did not catch it because jsdom runs with
`runScripts: 'outside-only'`, which evaluates `kfc.js` and `kfc-i18n.js` but
never executes page-level inline script. The suite was structurally blind to
this whole class of fault.

Fixed two ways. The pattern is now anchored at **both** ends, on the closing
`els.forEach(el => io.observe(el));`, so an early stop is impossible: either the
whole block matches or nothing is removed and the build says so. And
`build/verify.py` now parses every inline script on every page with Node's
`vm.Script` and refuses the push on a single syntax error.

Current state: **0 pages with broken inline JS.** GitHub `main` has 1, see 1c.

### 1b. The donate button takes no money, so the payment claims are gated off

`handleDonate()` is a placeholder. It disables the button, shows "processing"
for three seconds, re-enables it, and carries the comment "Replace this
function with your WordPress form submit handler or payment gateway redirect."
There is no form action, no POST, no gateway redirect.

The client asked for copy saying donations are processed by WiPay and PayPal
plus a "Secured by WiPay & PayPal" trust line. Shipping present-tense payment
claims and a security badge beside a button that takes no money is misleading,
whoever asked for the wording, so the copy is held behind a switch:

    PAYMENTS_LIVE = False        # build/migrate.py

While it is `False` the page carries the client's wording in the future tense,
says plainly that the form is being connected, gives a working route
(kwik@kwikfatcut.com), disables the button, and replaces the fake three second
handler with a no-op. The trust strip reads "Donation form coming soon".

Flip the flag to `True` once a gateway is wired and re-run migrate. The client's
exact requested wording is already in the file as `SUPPORT_NOTE_LIVE`, so going
live is a one-line change.

`verify.py` enforces this independently: it looks for a real form action, a
WiPay or PayPal domain, or a redirect, and fails the build if the page claims
live processing without one. The smoke test asserts the copy and the button
agree on the payment state rather than hardcoding either wording.

**To connect payments we need from the client:**

| For | What exactly |
|---|---|
| WiPay | Merchant account ID, API key, whether Hosted Payment Page or API integration, the developer or sandbox credentials, and the account currency (JMD or TTD or USD) |
| PayPal | The PayPal.me link or hosted Donate button ID, or for a full integration the business email, client ID and secret |
| Both | The return URL after a successful gift, which is presumably `thank-you.html`, and the cancel URL |
| Both | Whether recurring giving is needed. The page already has a monthly toggle in the markup, and recurring changes the integration substantially |
| Legal | Non-profit registration status, since it affects receipts and what the page may claim about tax treatment |

### 1c. The duplicate navbar was real. I was wrong to call it unreproducible.

The client reported it, I said it was not reproducible, and the screenshot
proved me wrong. The cause is one unscoped selector.

`kfc.css` set the fixed header with a bare `nav` element selector:

    nav{ position:fixed; top:0; left:0; right:0; justify-content:space-between; }

A page has more than one `<nav>`. Measured across the repo:

| Class | Pages | What it is |
|---|---|---|
| none | 50 | the real header nav |
| `toc` | 35 | article sidebar table of contents |
| `footer-nav` | 32 | footer nav |
| `fn` | 16 | a second footer nav variant |
| `page-nav` | 1 | internal reference doc index |

None of them reset `position`, so all of them inherited `position:fixed;top:0`
and `justify-content:space-between` and were pinned across the top of the
viewport, stacked on the real nav. The footer nav sits later in the DOM at the
same z-index, so it painted on top. That is exactly the screenshot: a wider row
of links spread edge to edge carrying **Support** and **Privacy**, which only
the footer nav has.

Measured in Chromium at 1536px, before the fix:

    nav.(none)       position:fixed top:0  1536x82   About/Why Free/.../Donate
    nav.footer-nav   position:fixed top:0  1536x65   About/.../Support/Privacy

**GitHub main does the same thing.** Same measurement, same result. This is not
something the migration introduced; it shipped in the original and is live now.

Fixed by giving the header nav `class="site-nav"` and scoping the fixed-header
rules to it, plus a guard rule `nav:not(.site-nav){position:static}` so a nav
added later can never be pinned by accident. The two footer variants are also
normalised onto one class, so `fn` and `footer-nav` are now styled by one rule.

Why my checks missed it: I counted `<nav>` elements in the DOM, found "one
header, one footer", and called that normal. Only a render shows them on top of
each other. `build/layout-test.js` now renders every page and asserts exactly
one fixed bar at the top.

### 1d. The homepage hero sat underneath the fixed nav

Found while verifying 1c. `.hero` is `min-height:100vh` with
`justify-content:flex-end` and `padding: 0 6vw 8vh`. Top padding is zero, and
the nav is `position:fixed` so it takes no space in flow. On any viewport
shorter than roughly 1000px the hero content slides up under it:

| Viewport height | Eyebrow top | Nav bottom | |
|---|---|---|---|
| 700px | 0 | 82 | hidden |
| 800px | 0 | 82 | hidden |
| 900px | 10 | 82 | hidden |
| 1080px | 176 | 82 | fine |

So it looks correct on a tall monitor and clips on most laptops. Same class of
fault as `.page-header`, which FIX 8 already covered; `.hero` and its five
siblings were never in scope. Also pre-existing in `main`.

Fixed with a top padding in `kfc.css` that clears the nav. Only `padding-top`
is set, so each hero keeps its own alignment. `layout-test.js` now checks short
viewports (640, 720, 800) specifically, since that is where this hides.

### 1e. One pre-existing break in `main`, also fixed

`learn-coconut-water.html` had an unescaped apostrophe inside a single-quoted
JS string:

    navigator.share({title:'Coconut Water — Nature's Sports Drink ...'

That closes the string early and breaks the whole inline script. It is the only
page in GitHub `main` with broken inline JS, it predates our work, and it is on
the same page as the "ownsports" typo we already fixed.

## 2. The Donate nav CTA was explicitly requested

The review flagged this as possibly our decision rather than the client's. It
was the client's. From their message:

> "One more item to add to the list: could you add a donation CTA button to the
> navbar as well?"

That request is in the chat thread, not in the Client Update Summary docx, which
is why a docx-only read missed it.

One honest nuance: the client said **add**, and we **replaced**. The nav button
slot previously held "Start Free", which pointed at the homepage `#download`
section the client asked us to delete, so it had no destination left. Putting
Donate in that slot fills a vacancy rather than displacing a working control.

If the client wants "Start Free" kept as well, it is two lines in
`build/migrate.py`: add it back to `NAV_ITEMS` pointing at `learn.html`. Worth
mentioning to them either way, since "Start Free. Learn First." is a brand
phrase.

## 3. September client round

| Request | State |
|---|---|
| Remove why-free card 04 | Done, plus the intro paragraph that set it up |
| Remove homepage Download section | Done, both `index.html` and its duplicate |
| Donation CTA in the navbar | Done, all 50 pages, see section 2 |
| Support page WiPay and PayPal | Copy written, gated off until payments work, see 1b |
| Team page "Built with Purpose" | Done, root cause found, see section 5 |
| Homepage duplicate navbar | **Real, fixed.** See 1c |
| Spanish, final 13 | 11 extracted, 2 unresolved, see section 7 |
| Restful Sleep sixth root | Approved but copy pending, not invented |
| Privacy policy | Our draft stands, client has no wording to preserve |

### The download removal had a 63 link cascade

`#download` was linked 63 times across 49 of 50 pages. 49 were the nav button,
now Donate. The other 14 were "Download Free" buttons, relabelled **Start
Learning** and pointed at `learn.html`.

### Premium language audit

One real offender beyond the card, and it is the paragraph introducing it:
"Later, if you decide to go deeper with us..." Trimmed to the client's own
surviving phrases. Everything else that matched is **anti**-paywall brand copy
and was left alone: "no paywall on knowledge", "The griot never charged for
knowledge", "You do not need a subscription".

## 4. Phase 1 scope status

| # | Item | State |
|---|---|---|
| 1 | On-page SEO | Done. Canonicals, sitemap 39 to 47 URLs, robots.txt, `<main>`, noindex on reference docs, 10 orphans to 0, 5 meta descriptions rewritten, heading skips fixed |
| 2 | Logo shields | Done, placeholder art. Real PNGs still missing from the repo |
| 3 | Localisation | Framework done, 3 locales, 31 passing tests. Spanish copy outstanding |
| 4 | Media integration | Blocked, nothing new in the repo |
| 5 | Navigation bar | Done, one nav across 50 pages, current page indicator, working mobile menu |
| 6 | Chatbot UX | Blocked, no chatbot code in the repo |
| 7 | Critical bug fixes | Done. Coconut water typo and team page collapse both fixed |
| 8 | Blank space before content | Done |
| 9 | Our Roots grid | Done. Sixth card pending copy |
| 10 | Text contrast | Done, all text clears WCAG AA |
| 11 | Privacy policy | Built, needs legal review |
| 12 | Page speed | Substantially done |

## 5. The team page bug, root cause

`.team-intro` pairs `max-width:680px` with `padding:6rem 6vw 4rem`, and
`box-sizing` is `border-box` globally, so the vw padding is subtracted from the
px width rather than added to it. The text column shrinks as the display widens:

| Viewport | Before | After |
|---|---|---|
| 1920px | 450px | 680px |
| 3440px ultrawide | 267px | 680px |
| 3840px 4K | 219px | 680px |
| 5120px | 66px | 680px |

At 66px nearly every word takes its own line and long words overflow, which is
the stretching. It only shows on very wide displays, which is why it never
reproduced at normal test widths and why it read as a CMS artifact.

Fixed in `kfc.css` section 4b by growing the box by its own padding. Two other
containers had the same fault and are fixed with it: `.letter` on thank-you,
which is in the Spanish 13, and `.article-wrap` on four articles. A scan
confirms zero unguarded rules of this shape remain.

## 7. Spanish

Eleven of the client's 13 map to a repo file and are extracted: **1,396 strings,
about 12,957 words.** Heaviest are `learn-taino-diet` (184), `learn-water`
(180) and `founder` (164).

Shared chrome is 45 strings, 31 translated and working, so the nav, footer and
share block are done. The other 14 are numerals, platform names, punctuation,
the emoji and the brand name, which stay in English by design.

Two were deliberately not guessed:

- **Library.** No page of that name. Candidates are `foods-hub.html`,
  `learn-ancient-archive.html`, `learn-jamaican-foods-az.html`.
- **Tash's LiquidFood.** New page, client sending URL and content.

Skeletons are at `i18n/es/<page>.json`. Filling the `t` fields is the whole
remaining task; pages render correctly at any level of completeness, so it can
go page by page. Progress: `python3 build/extract-strings.py status`.

This is health education making clinical claims about haemoglobin, glycaemic
response and anaemia for Cuban and Caribbean readers. A machine first pass is a
reasonable start but it should not ship without a native reviewer, and
`support.html` and `privacy.html` need the same legal review in Spanish that
they need in English.

## 8. Media and the location map

Nothing new arrived. The repo has `deidra-washing-locs.mp4`,
`honey-ras-family.jpeg` and `kwik-logo.jpeg` only. The Family Dinner video and
three Ackee images are not in `main`, so the Drive permission issue appears
still open. Two of six placements are also still unconfirmed.

`location-map.html` has not reached us either. When it does, the privacy policy
must change **in the same commit**: it currently states there are no iframes and
that Google Fonts is the only third-party origin, and an OpenStreetMap embed
makes both false and exposes the visitor's IP to OpenStreetMap on page load.

## 9. Fixes outside the scope document

- Coconut water `<em>` that should have been a `<br>`, rendering "ownsports
  drink". A markup bug, not a spelling mistake, which is why searching for the
  string found nothing. The same doubled-tag slip on six other pages, harmless,
  cleaned up anyway.
- `learn-oral-health.html` had two unclosed `<div>` tags, 77 against 75, pulling
  `<footer>` inside the article body.
- Below 960px the old CSS hid every nav link except the button, so phones had no
  navigation at all.
- `.reveal` had no fallback, so blocked JS or reduced motion left pages empty.
- No visible keyboard focus state anywhere.
- `prefers-reduced-motion` now honoured site wide.
- `kwik-fat-cut-homepage.html` canonicalised to the homepage and kept out of the
  sitemap, since it is byte-identical to `index.html`.
- Five boilerplate meta descriptions rewritten from each page's own H1 and
  opening line. Those five are also the thinnest pages on the site at 2 to 3
  minutes each, worth raising separately.

## 10. Measured results

Contrast against `#1A0E08`:

| Token | Before | After |
|---|---|---|
| `--c60` body copy | 6.73:1 | 6.94:1 |
| `--c30` labels | 2.54:1 fail | 4.56:1 pass |
| green as text | 2.96:1 fail | 4.54:1 pass |
| red as text | 3.69:1 fail | 4.67:1 pass |

Body copy also moved from 0.9rem weight 300 to 1rem weight 400. Brand hexes
unchanged; text-safe variants apply only where the colour carries words.

Weight: all HTML 1756 KB to 1475 KB, shared CSS and JS 40 KB cached once.
`about.html` went 151 KB to 24 KB.

Verification:

| Gate | Result |
|---|---|
| `verify.py` static | all checks pass, 0 broken inline scripts against main's 1 |
| `layout-test.js` rendered | 828 of 828 across 50 pages and 8 viewports |
| `smoke-test.js` behaviour | 31 of 31 |

Plus: 0 broken links, 0 orphaned articles, 0 heading skips, spelling audit clean
both directions.

## 11. Open, by owner

| Item | Needed from the client |
|---|---|
| **Payments** | Gateway credentials, see the table in 1b. Highest priority, blocks donations entirely |
| App copy | Decision, see below |
| Spanish | What "Library" is, Tash's LiquidFood content, then a translator and native reviewer |
| Media | Family Dinner video and 3 Ackee images into the repo, plus the last 2 placements |
| Chatbot | Dashboard and admin access |
| Shields | The three PNG assets MANIFEST.txt promised |
| Social links | Real URLs for Facebook, Instagram, TikTok, X |
| Our Roots | Copy for the sixth card, restful sleep |
| Location map | The `location-map.html` snippet |
| Privacy | Legal review |
| Scope | 13 pages priced against 50 built |

### The app copy decision

Removing the Download section did not remove every app claim. The homepage still
has an `#app-section` titled "Inside the App" listing hydration tracking and
progress trends, the hero still calls Kwik Fat Cut "a food and diet tracking
app", and why-free's six "What's Free" cards are the same feature list.

Only what was explicitly approved has been removed. Three options, confined to
`index.html`, `kwik-fat-cut-homepage.html` and `why-free.html`:

1. **Literal.** Leave them. The site still advertises an app that does not exist.
2. **Consistent.** Cut "Inside the App" and rewrite the six cards as planned
   features.
3. **Reframed.** Keep them, relabelled "Coming to the app".

Client decision, not ours.


---

# r4: media, location map, and the privacy knock-on

## Family Dinner video, homepage

`family-dinner.mp4` is **576x1024, portrait**, 25.6s, 5.2MB. That drove the
treatment. The site's existing `.video-frame` is `aspect-ratio:16/9` with
`object-fit:cover`, which crops a vertical clip to a thin horizontal band, so
the video gets its own two-column block instead: video beside the copy on
desktop, stacked and centred below 820px, capped at 70vh on phones.

Placed after the mission section and before The Bigger Picture, which is where
the human side of the homepage already sits.

Loading:

| | |
|---|---|
| `preload="none"` | nothing downloads until someone presses play |
| `poster="family-dinner-poster.jpg"` | 53KB frame, generated with ffmpeg at t=2s |
| `width`/`height` attributes | browser reserves the space, so no layout shift |
| `playsinline` | iOS plays in place instead of taking over the screen |

Net: a visitor who does not press play fetches **53KB instead of 5.2MB**.

The section tag "Our Community" and the caption are placeholders in the same
sense the shield art is. The heading uses "We are what we eat", which is an
official brand phrase from SITE-INDEX, so nothing is invented as a claim. Swap
them when the client sends final copy; they are two strings in `migrate.py`.

## The other video was worse

The new media check in `verify.py` immediately failed on
`learn-water-vs-blood.html`: `deidra-washing-locs.mp4` is 5.7MB with
`preload="metadata"` and `poster=""`. An empty poster attribute is invalid and
makes some browsers request the page itself as an image.

That video is also portrait, 480x848, and it was sitting in the 16:9
`object-fit:cover` frame, so the client's footage was being cropped to a
horizontal strip through the middle. Both fixed: poster generated, preload set
to none, and the frame now follows the video's own aspect ratio. It is the same
media scope item and a confirmed placement, so it is in scope rather than an
unrelated change.

## Kingston location card, About page

Adapted from the supplied `location-map.html`, not pasted. The supplied file
was a complete standalone light-theme document with its own `<h1>`, its own
font stack (Source Sans 3 and Montserrat, neither used on this site) and a
cream background.

Kept exactly as supplied: general Kingston only with no street address,
OpenStreetMap as the tile source, and the plain `geo:` directions link.

Changed: only the card is carried over, restyled to the dark palette and the
site's two typefaces; the heading is `<h2>` so About keeps one `<h1>`; the
iframe gains a `title` (an untitled iframe is unusable with a screen reader)
and `referrerpolicy="no-referrer"` so OpenStreetMap is not told which page
embedded it; and a fallback message sits behind the frame, because privacy
extensions and some corporate networks block third-party frames and an empty
grey box tells the visitor nothing.

Placed after the Roots teaser, which is the "where we come from" part of the
page.

`location-map.html` is **not published as a page**. Left in the web root it
would be a public URL with a second `<h1>` and none of the site chrome. It is
moved to `build/location-map.source.html` as a reference copy.

## Privacy policy, updated in the same change

Third-Party Services now names OpenStreetMap, states that an embedded frame
contacts them on page load whether or not the visitor uses the map, and says
the visitor's IP is visible to them in the same way it is to Google Fonts. The
"nothing is embedded in our pages" line is gone; the Links out paragraph now
distinguishes ordinary links from embeds.

`verify.py` enforces this from now on. `check_embeds_disclosed()` scans every
page for iframes, extracts the host, and fails the build if the privacy policy
does not name it. Add an embed anywhere and the push is blocked until the
policy catches up. It also fails any iframe without a `title`.

## Shield artwork

Unchanged and still placeholder. The status is now stated in `migrate.py`
directly above the two SVG constants, so nobody reading the source mistakes it
for final branding. `MANIFEST.txt` promises `favicon.png`, `shield.png` and
`shield-master.png`; none are in the repo. The switching logic lives in
`kfc.js` and does not change when the real art lands: replace the two
constants and `favicon.svg`.

## Payments

Untouched. `PAYMENTS_LIVE = False`.

## Verification

| Gate | Result |
|---|---|
| `verify.py` static | all pass, including the two new checks |
| `layout-test.js` rendered | 954 of 954, 50 pages, 8 viewports, About and Privacy added to the sample |
| `smoke-test.js` behaviour | 31 of 31 |

`migrate.py` and `build-hubs.py` verified idempotent over three consecutive
runs. Two idempotency bugs were found and fixed in the process, one of them
destructive: the privacy section removal regex was consuming the newline after
the final `</div>`, which welded the share strip onto it on every re-run.

## One thing to decide before pushing

Cloudflare Pages serves the whole repo, so `build/` is reachable as a URL. It
is not linked and not in the sitemap, but the Python tooling and
`location-map.source.html` are technically fetchable. If that matters, set a
build output directory in the Cloudflare Pages project or exclude `build/`.
