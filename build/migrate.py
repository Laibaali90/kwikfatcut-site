#!/usr/bin/env python3
"""
Kwik Fat Cut Phase 1 migration.

Rewrites every page in the site root onto the shared kfc.css / kfc.js layer,
applies the structural fixes from the Phase 1 scope, and applies the content
changes the client confirmed in the September round.

Idempotent. Safe to run again after any content edit, and safe to run against
a fresh pull of GitHub main.

Usage:
    python3 build/migrate.py            rewrite in place
    python3 build/migrate.py --dry-run  report only, change nothing
"""

import os
import re
import sys
import json
import base64
import hashlib
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRY = '--dry-run' in sys.argv

SITE_URL = 'https://kwikfatcut.com/'
HOMEPAGE = 'index.html'

# Reference documents, never public pages.
NOINDEX = {
    'kwik-fatcut-block-map.html',
    'social-media-templates.html',
}

# Byte-identical duplicate of index.html. Kept because inbound links to it
# exist, but canonicalised to the homepage so it cannot split ranking.
ALIAS_HOME = 'kwik-fat-cut-homepage.html'

# --------------------------------------------------------------------------
# Canonical navigation. One definition applied to all pages.
# kfc.js marks the current item at runtime.
#
# The button slot holds Donate, not Start Free. Two reasons, both from the
# September client round: the client asked for a donation CTA in the navbar,
# and Start Free pointed at the homepage #download section, which is being
# removed because the app does not exist yet. That left it with no target.
# --------------------------------------------------------------------------
NAV_ITEMS = [
    ('About',     'about.html'),
    ('Why Free',  'why-free.html'),
    ('Our Roots', 'our-roots.html'),
    ('Foods Hub', 'foods-hub.html'),
    ('Learn',     'learn.html'),
    ('Team',      'team.html'),
]

FOOTER_ITEMS = NAV_ITEMS + [
    ('Support', 'support.html'),
    ('Privacy', 'privacy.html'),
]

NAV_CTA = ('Donate', 'support.html')

# PLACEHOLDER ARTWORK, NOT FINAL BRANDING.
#
# The client approved the day/night switching behaviour but has not supplied
# the final shield assets. MANIFEST.txt promises favicon.png, shield.png and
# shield-master.png; none of the three are in the repo. These two SVGs are
# built from the brand palette purely so the mechanism can be developed and
# tested. Replace both constants, and favicon.svg, when the real art arrives.
# Nothing else needs to change: the switching logic lives in kfc.js.
#
# Shields are inlined, not linked. Browsers block external file references
# from <use>, so a shared shield.svg would render nothing.
SHIELD_NIGHT = (
    '<svg class="logo-shield logo-shield--night" viewBox="0 0 32 40" '
    'aria-hidden="true" focusable="false">'
    '<path d="M16 1 2 6v16c0 8 6 14 14 17 8-3 14-9 14-17V6z" '
    'fill="#8E2F0A" stroke="#DAA520" stroke-width="1.6"/>'
    '<circle cx="16" cy="19" r="5.6" fill="#1A0E08" opacity="0.5"/>'
    '<circle cx="13.8" cy="17.2" r="4.4" fill="#8E2F0A"/></svg>'
)

SHIELD_DAY = (
    '<svg class="logo-shield logo-shield--day" viewBox="0 0 32 40" '
    'aria-hidden="true" focusable="false">'
    '<path d="M16 1 2 6v16c0 8 6 14 14 17 8-3 14-9 14-17V6z" '
    'fill="#DAA520" stroke="#2E6B3E" stroke-width="1.6"/>'
    '<circle cx="16" cy="19" r="4.4" fill="#1A0E08" opacity="0.26"/>'
    '<g stroke="#1A0E08" stroke-width="1.5" stroke-linecap="round" opacity="0.45">'
    '<path d="M16 11v2.4M16 24.6v2.4M9 19h2.4M20.6 19H23"/>'
    '<path d="m11.2 14.2 1.7 1.7M19.1 22.1l1.7 1.7M20.8 14.2l-1.7 1.7M12.9 22.1l-1.7 1.7"/>'
    '</g></svg>'
)


def nav_html():
    links = '\n'.join(
        '    <a href="%s">%s</a>' % (href, label)
        for label, href in NAV_ITEMS
    )
    return (
        '<nav class="site-nav">\n'
        '  <a href="%s" class="logo">\n'
        '    %s%s\n'
        '    <span>Kwik <em>Fat Cut</em></span>\n'
        '  </a>\n'
        '  <div class="nav-right">\n'
        '    <div class="lang-switch"></div>\n'
        '    <button class="nav-toggle" type="button" aria-expanded="false" '
        'aria-controls="nav-links" aria-label="Menu">\n'
        '      <span></span><span></span><span></span>\n'
        '    </button>\n'
        '  </div>\n'
        '  <div class="nav-links" id="nav-links">\n'
        '%s\n'
        '    <a href="%s" class="nav-btn">%s</a>\n'
        '  </div>\n'
        '</nav>'
    ) % (HOMEPAGE, SHIELD_NIGHT, SHIELD_DAY, links, NAV_CTA[1], NAV_CTA[0])


def footer_nav_html():
    links = '\n'.join(
        '    <a href="%s">%s</a>' % (href, label)
        for label, href in FOOTER_ITEMS
    )
    return '<nav class="footer-nav">\n%s\n  </nav>' % links


# --------------------------------------------------------------------------
# Selectors now owned by kfc.css. Removed from each page's inline block so
# the same declarations are not shipped 50 times over.
# --------------------------------------------------------------------------
STRIP = {
    ':root',
    '*,*::before,*::after',
    'html', 'body', 'body::after',
    'nav', '.logo', '.logo em',
    '.nav-links', '.nav-links a', '.nav-links a:hover',
    '.nav-links a:not(.nav-btn)', '.nav-btn',
    '.reveal', '.reveal.in',
    '.page-header',
    '.roots-grid',
    'footer',
    '.footer-nav', '.footer-nav a', '.footer-nav a:hover', '.footer-copy',
}

# Tokens kfc.css already defines. Anything else a page declares in :root is
# page-specific and is preserved.
KNOWN_TOKENS = {
    '--bg', '--gold', '--green', '--red', '--cream',
    '--c60', '--c30', '--c10', '--g20', '--g08', '--r08',
}

COMMENT_RE = re.compile(r'/\*.*?\*/', re.S)


def split_prelude(prelude):
    """Separate leading comments from the selector itself.

    A rule is often preceded by a section comment, for example

        /* HERO */
        .page-header{...}

    Without this the comment is glued to the selector and the rule never
    matches the strip list.
    """
    comments = ''.join(COMMENT_RE.findall(prelude))
    sel = COMMENT_RE.sub('', prelude)
    return comments, ' '.join(sel.split()).replace(', ', ',').rstrip()


def reduce_root(body):
    """Keep only tokens kfc.css does not define."""
    keep = []
    for decl in body.split(';'):
        name = decl.split(':')[0].strip()
        if name.startswith('--') and name not in KNOWN_TOKENS:
            keep.append(decl.strip())
    return ';'.join(keep) + ';' if keep else ''


def strip_css(css):
    """Remove rules owned by kfc.css. Walks into @media blocks."""
    out = []
    i, n = 0, len(css)
    while i < n:
        if css.startswith('/*', i):
            j = css.find('*/', i + 2)
            j = n if j == -1 else j + 2
            out.append(css[i:j])
            i = j
            continue

        brace = css.find('{', i)
        if brace == -1:
            out.append(css[i:])
            break

        prelude = css[i:brace]
        comments, sel = split_prelude(prelude)

        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == '{':
                depth += 1
            elif css[j] == '}':
                depth -= 1
            j += 1
        body = css[brace + 1:j - 1]

        if sel.startswith('@media'):
            inner = strip_css(body)
            if inner.strip():
                out.append(prelude + '{' + inner + '}')
        elif sel.startswith('@'):
            out.append(css[i:j])                  # keyframes, font-face, supports
        elif sel == ':root':
            kept = reduce_root(body)
            if kept:
                out.append(prelude + '{' + kept + '}')
        elif sel in STRIP:
            if comments:
                out.append('\n  ' + comments)
        else:
            out.append(css[i:j])
        i = j

    return ''.join(out)


# --------------------------------------------------------------------------
# Head
# --------------------------------------------------------------------------
PRECONNECT = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="icon" href="favicon.svg" type="image/svg+xml">'
)
SHARED_CSS = '<link rel="stylesheet" href="kfc.css">'


def inject_head(html, filename):
    # Clear anything a previous run added, so the pass stays idempotent.
    for pat in (r'\n?<link rel="preconnect"[^>]*>',
                r'\n?<link rel="icon"[^>]*>',
                r'\n?<link rel="stylesheet" href="kfc\.css">',
                r'\n?<link rel="canonical"[^>]*>',
                r'\n?<meta name="robots"[^>]*>'):
        html = re.sub(pat, '', html)

    font = re.search(r'<link[^>]*fonts\.googleapis\.com[^>]*>', html)
    if font:
        html = html[:font.start()] + PRECONNECT + '\n' + html[font.start():]
    else:
        html = html.replace('</head>', PRECONNECT + '\n</head>', 1)

    # kfc.css goes LAST in the head, after the page's own <style>, so the
    # Phase 1 fixes are authoritative and cannot be undone by page drift.
    canon = SITE_URL if filename in (HOMEPAGE, ALIAS_HOME) else SITE_URL + filename
    tail = SHARED_CSS + '\n<link rel="canonical" href="%s">' % canon
    if filename in NOINDEX:
        tail += '\n<meta name="robots" content="noindex,nofollow">'
    return html.replace('</head>', tail + '\n</head>', 1)


# --------------------------------------------------------------------------
# Pre-existing markup repairs found during the audit. Each is a real defect in
# the source, not something the migration introduced.
# --------------------------------------------------------------------------
MARKUP_REPAIRS = {
    # learn-coconut-water.html: the second <em> should have been a <br>, so
    # the H1 renders as "Nature's ownsports drink". This is the typo in the
    # Phase 1 scope, item 7. It is a markup bug, not a spelling mistake.
    'learn-coconut-water.html': [
        ("<h1>Coconut water.<br><em>Nature's own<em>sports drink.</em></em></h1>",
         "<h1>Coconut water.<br><em>Nature's own<br>sports drink.</em></h1>"),
        # Pre-existing in GitHub main, not introduced here. An unescaped
        # apostrophe in Nature's closes the single-quoted JS string early, so
        # the whole inline script on this page is a syntax error and shareKFC
        # never runs. This is the only page in main with broken inline JS.
        ("navigator.share({title:'Coconut Water — Nature's Sports Drink",
         "navigator.share({title:'Coconut Water — Nature\\u2019s Sports Drink"),
    ],
    # learn-oral-health.html leaves two .article-section divs unclosed, which
    # pulls <footer> inside the article body in the parsed DOM. 77 opening
    # <div> tags against 75 closing ones.
    'learn-oral-health.html': [
        ('    </div>\n\n  </article>',
         '    </div>\n    </div>\n    </div>\n\n  </article>'),
    ],
}

# <em><em>Text</em></em> with nothing between the tags. Invalid nesting, and
# the same authoring slip that produced the coconut water typo.
DOUBLE_EM_RE = re.compile(r'<(em|strong)>(<\1>)')


def repair_markup(html, filename, report):
    fixed, n = DOUBLE_EM_RE.subn(r'<\1>', html)
    if n:
        html = fixed
        # The stray closing tag left behind by the removed opener.
        html = re.sub(r'</(em|strong)></\1>', r'</\1>', html)
        report.append('removed %d redundant nested emphasis tag(s)' % n)

    for old, new in MARKUP_REPAIRS.get(filename, []):
        if new in html:
            continue
        if old not in html:
            report.append('WARNING: repair anchor not found, check manually')
            continue
        html = html.replace(old, new, 1)
        report.append('applied markup repair')
    return html


# --------------------------------------------------------------------------
# Stray American spellings in what is meant to be a British source. Found by
# build/extract-strings.py audit. They make the en-GB reading inconsistent and
# they survive the en-US transform untouched, so both locales look sloppy.
# Whole word, case preserving, unambiguous pairs only.
# --------------------------------------------------------------------------
SOURCE_SPELLING = {
    'organization': 'organisation', 'organizations': 'organisations',
    'organized': 'organised', 'organizing': 'organising',
    'aging': 'ageing',
    'practiced': 'practised', 'practicing': 'practising',
    'formalized': 'formalised',
    'center': 'centre', 'centers': 'centres',
    'programs': 'programmes',
}

SPELLING_RE = re.compile(
    r'\b(' + '|'.join(sorted(SOURCE_SPELLING, key=len, reverse=True)) + r')\b',
    re.IGNORECASE)


def load_protected():
    """Proper nouns that are never respelled. Shared with the audit and the
    runtime transform via i18n/protected.json."""
    path = os.path.join(ROOT, 'i18n', 'protected.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh).get('phrases', [])
    except (IOError, ValueError):
        return []


PROTECTED = load_protected()
PROTECTED_RE = (re.compile('|'.join(re.escape(p) for p in
                sorted(PROTECTED, key=len, reverse=True)))
                if PROTECTED else None)


def normalise_spelling(html, report):
    """Fix American spellings in the body text only, never in markup.

    Proper nouns are masked first. The World Health Organization keeps its z
    in British copy, and Duke University Medical Center is not a Centre.
    """
    # Script and style blocks must be matched BEFORE the generic tag pattern,
    # or <style> matches as a plain tag and the CSS inside becomes "text".
    # text-align:center would then become text-align:centre, which is not a
    # spelling variant, it is broken CSS.
    parts = re.split(
        r'(<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->|<[^>]+>)',
        html, flags=re.S)
    hits = [0]

    def sub(m):
        hits[0] += 1
        word = m.group(1)
        out = SOURCE_SPELLING[word.lower()]
        return out.capitalize() if word[0].isupper() else out

    for i, part in enumerate(parts):
        if part.startswith('<'):
            continue

        if PROTECTED_RE:
            shelf = []

            def stash(m):
                shelf.append(m.group(0))
                return '\x00%d\x00' % (len(shelf) - 1)

            part = PROTECTED_RE.sub(stash, part)
            part = SPELLING_RE.sub(sub, part)
            part = re.sub(r'\x00(\d+)\x00',
                          lambda m: shelf[int(m.group(1))], part)
            parts[i] = part
        else:
            parts[i] = SPELLING_RE.sub(sub, part)

    if hits[0]:
        report.append('normalised %d American spelling(s) to en-GB' % hits[0])
    return ''.join(parts)


# --------------------------------------------------------------------------
# Five articles shipped a meta description that is really the page title plus
# a stock brand sentence. It describes nothing and renders a useless search
# result. Each replacement below is built from that page's own H1 and opening
# line, so it is existing approved copy reused rather than new claims, cut
# only at sentence boundaries and kept under 158 characters.
# --------------------------------------------------------------------------
META_DESCRIPTIONS = {
    'learn-coconut-water.html':
        'Coconut water. Used across the Caribbean for centuries. Matched to '
        'commercial sports drinks in clinical trials. Extraordinarily rich in '
        'electrolytes.',
    'learn-fruits-nuts-guide.html':
        'Fruits and nuts. Guinep, naseberry, otaheiti apple, jackfruit, '
        'soursop, june plum, star apple, breadnut, cashew. These are not just '
        'fruits.',
    'learn-ground-provisions.html':
        'Ground provisions. Yam, dasheen, coco, breadfruit, cassava, sweet '
        'potato, green banana. These are not side dishes.',
    'learn-jamaican-cuisine.html':
        'Jamaican cuisine. Born from three continents, refined across four '
        'centuries, grown in this soil. Jamaican food is not a trend.',
    'learn-jamaican-seafood.html':
        'Jamaican seafood. Jamaica is an island. The sea has always been a '
        'source of food, medicine, and livelihood.',
}


def fix_meta_description(html, filename, report):
    new = META_DESCRIPTIONS.get(filename)
    if not new:
        return html
    if 'content="%s"' % new in html:
        return html
    out = re.sub(r'(<meta name="description" content=")[^"]*(")',
                 lambda m: m.group(1) + new + m.group(2), html, count=1)
    if out != html:
        report.append('rewrote the boilerplate meta description')
    return out


# Two pages jump from H2 straight to H4. In both, the H4s are sub-headings
# that should have been H3, which is a skipped level for screen readers and
# for search engines reading the outline.
HEADING_PROMOTE = {'learn-straight-mixes.html', 'opian-meals.html'}


def fix_heading_levels(html, filename, report):
    if filename not in HEADING_PROMOTE or '<h4' not in html:
        return html
    html = re.sub(r'<h4([^>]*)>', r'<h3\1>', html).replace('</h4>', '</h3>')
    report.append('promoted H4 sub-headings to H3, no skipped levels')
    return html


# ==========================================================================
# CLIENT CHANGES, September round
# ==========================================================================

def find_section(html, opening):
    """Return (start, end) of a <section> given its exact opening tag,
    counting nested sections so the close is the matching one."""
    start = html.find(opening)
    if start == -1:
        return None
    i = start + len(opening)
    depth = 1
    while depth and i < len(html):
        nxt_open = html.find('<section', i)
        nxt_close = html.find('</section>', i)
        if nxt_close == -1:
            return None
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 8
        else:
            depth -= 1
            i = nxt_close + 10
    return (start, i)


DOWNLOAD_OPEN = '<section class="download" id="download">'


def remove_download_section(html, filename, report):
    """Remove the homepage Download section.

    Client: the mobile app is still in planning, so the App Store and Google
    Play buttons, the "log your first meal" copy and the app-feature claims
    inside this section point at something that does not exist.
    """
    if filename not in (HOMEPAGE, ALIAS_HOME):
        return html
    span = find_section(html, DOWNLOAD_OPEN)
    if not span:
        return html
    html = html[:span[0]] + html[span[1]:]
    report.append('removed the Download section (App Store / Google Play)')
    return html


def repoint_download_links(html, report):
    """Give the orphaned CTAs a real destination.

    #download was linked 63 times across 49 of the 50 pages. 49 of those were
    the nav Start Free button, which the canonical nav replaces with Donate.
    The remaining 14 are "Download Free" hero and strip buttons, which are
    repointed at the Learn hub and relabelled, since there is nothing to
    download.
    """
    n = [0]

    def fix(m):
        n[0] += 1
        attrs = re.sub(r'href="[^"]*#download"', 'href="learn.html"', m.group(1))
        inner = re.sub(r'Download Free', 'Start Learning', m.group(2))
        return '<a' + attrs + '>' + inner + '</a>'

    html = re.sub(r'<a([^>]*href="[^"]*#download"[^>]*)>(.*?)</a>',
                  fix, html, flags=re.S)
    if n[0]:
        report.append('repointed %d Download CTA(s) to learn.html' % n[0])
    return html


PREMIUM_CARD_RE = re.compile(
    r'\s*<div class="journey-step reveal">\s*'
    r'<div class="js-marker">04</div>\s*'
    r'<h3>Deeper, if you choose</h3>.*?</div>',
    re.S)

# The paragraph that introduces the removed card. It sets up the premium tier
# with "if you decide to go deeper with us", so removing the card alone leaves
# it dangling. Trimmed to the client's own surviving phrases rather than
# rewritten, so the voice is unchanged.
JOURNEY_INTRO_OLD = ('Later, if you decide to go deeper with us, that is your '
                     'choice &mdash; and it will be a wise one, driven by '
                     'understanding, not desperation. But that comes later. '
                     'First, just begin.')
JOURNEY_INTRO_NEW = ('Understanding is what drives real change, not '
                     'desperation. First, just begin.')


def remove_premium_card(html, filename, report):
    """Remove the why-free "Deeper, if you choose" card.

    Client: Kwik Fat Cut is free and non-profit, with no paywalls and no
    premium tiers, so a card advertising premium features is off-brand.
    """
    if filename != 'why-free.html':
        return html
    html, n = PREMIUM_CARD_RE.subn('', html)
    if n:
        report.append('removed the premium "Deeper, if you choose" card')
    if JOURNEY_INTRO_OLD in html:
        html = html.replace(JOURNEY_INTRO_OLD, JOURNEY_INTRO_NEW, 1)
        report.append('trimmed the premium hook from the journey intro')
    return html


# --------------------------------------------------------------------------
# Donation page.
#
# THE SWITCH BELOW IS THE IMPORTANT PART. Set it to True only once a real
# WiPay or PayPal flow is wired to the Donate button.
#
# Why it exists. The client asked for copy saying donations are processed by
# WiPay and PayPal, plus a "Secured by WiPay & PayPal" trust line. But
# handleDonate() in support.html is still a placeholder: it disables the
# button, shows "processing" for three seconds, re-enables it, and carries the
# comment "Replace this function with your WordPress form submit handler or
# payment gateway redirect." No form action, no POST, no gateway redirect.
#
# Shipping present-tense payment claims and a security badge next to a button
# that takes no money is misleading, whoever asked for the wording. So the
# client's copy is held behind this flag. While it is False the page says
# plainly that the form is being connected and gives a working way to donate.
#
# To go live: connect the gateway, set PAYMENTS_LIVE = True, re-run migrate.
# --------------------------------------------------------------------------
PAYMENTS_LIVE = False

SUPPORT_NOTE_OLD = ('All donations are processed securely. Kwik Fat Cut does '
                    'not store payment information. Your generosity is '
                    'received with gratitude and used with care.')

# Exactly the client's requested wording. Their sentence used a dash before
# "you confirm your gift"; it is a full stop here to match house style.
SUPPORT_NOTE_LIVE = ('Donations are processed securely by WiPay, a trusted '
                     'Caribbean payment provider, or PayPal for international '
                     'giving. Kwik Fat Cut never sees or stores your card '
                     'details. You confirm your gift and enter your details on '
                     'a secure payment page. Your generosity is received with '
                     'gratitude and used with care.')

# Same wording, future tense, plus a route that actually works today.
SUPPORT_NOTE_PENDING = ('Donations will be processed securely by WiPay, a '
                        'trusted Caribbean payment provider, or PayPal for '
                        'international giving. Kwik Fat Cut never sees or '
                        'stores your card details. '
                        '<strong>The donation form is being connected now.</strong> '
                        'Until it is live, email '
                        '<a href="mailto:kwik@kwikfatcut.com">kwik@kwikfatcut.com</a> '
                        'and we will arrange your gift personally. Your '
                        'generosity is received with gratitude and used with care.')

TRUST_OLD = '<span class="trust-item">\U0001F512 Secure payment</span>'
TRUST_LIVE = '<span class="trust-item">\U0001F512 Secured by WiPay &amp; PayPal</span>'
TRUST_PENDING = '<span class="trust-item">\U0001F512 Donation form coming soon</span>'

DONATE_BTN_OLD = ('<button class="donate-btn" onclick="handleDonate(event)">')
DONATE_BTN_PENDING = ('<button class="donate-btn" onclick="handleDonate(event)" '
                      'disabled aria-disabled="true" '
                      'title="The donation form is being connected. '
                      'Email kwik@kwikfatcut.com to give in the meantime.">')

# The placeholder handler fakes a three second "processing" state and then
# resets, which reads to a visitor as a payment that silently failed. While
# payments are off the button is disabled, so replace the body with a no-op
# that keeps the function defined for any other caller.
HANDLE_DONATE_RE = re.compile(
    r'function handleDonate\(e\)\{.*?\n\}', re.S)
HANDLE_DONATE_PENDING = (
    'function handleDonate(e){\n'
    '  // Payment gateway not connected yet. The button is disabled in the\n'
    '  // markup; this stays defined so nothing throws if it is called.\n'
    '  e.preventDefault();\n'
    '}')


def update_support_copy(html, filename, report):
    """Donation page copy, gated on whether payments actually work."""
    if filename != 'support.html':
        return html

    note = SUPPORT_NOTE_LIVE if PAYMENTS_LIVE else SUPPORT_NOTE_PENDING
    trust = TRUST_LIVE if PAYMENTS_LIVE else TRUST_PENDING

    for old in (SUPPORT_NOTE_OLD, SUPPORT_NOTE_LIVE, SUPPORT_NOTE_PENDING):
        if old in html and old != note:
            html = html.replace(old, note, 1)
            report.append('donation copy set to %s wording' %
                          ('live' if PAYMENTS_LIVE else 'pending'))
            break

    for old in (TRUST_OLD, TRUST_LIVE, TRUST_PENDING):
        if old in html and old != trust:
            html = html.replace(old, trust, 1)
            report.append('trust strip set to %s wording' %
                          ('live' if PAYMENTS_LIVE else 'pending'))
            break

    if not PAYMENTS_LIVE:
        if DONATE_BTN_OLD in html:
            html = html.replace(DONATE_BTN_OLD, DONATE_BTN_PENDING, 1)
            report.append('donate button disabled until the gateway is wired')
        if 'Payment gateway not connected yet' not in html:
            html, n = HANDLE_DONATE_RE.subn(HANDLE_DONATE_PENDING, html)
            if n:
                report.append('replaced the simulated 3s payment handler')

    return html


# ==========================================================================
# MEDIA AND LOCATION, September round
# ==========================================================================

# family-dinner.mp4 is 576x1024, so portrait, 25.6s, 5.2MB. The existing
# .video-frame on learn-water-vs-blood.html is aspect-ratio 16/9 with
# object-fit:cover, which would crop a portrait video to a letterbox slice, so
# this gets its own two-column treatment instead of reusing that block.
#
# Loading: preload="none" plus a 53KB poster frame, so the 5.2MB download only
# happens if someone presses play. width and height are set so the browser
# reserves the space and the page does not shift when the poster arrives.
HOMEPAGE_VIDEO = '''
  <!-- KFC:VIDEO START  generated by build/migrate.py -->
  <section class="kfc-video" id="family-dinner">
    <div class="kfc-video-inner">
      <figure class="kfc-video-frame reveal">
        <video
          controls
          playsinline
          preload="none"
          poster="family-dinner-poster.jpg"
          width="576" height="1024">
          <source src="family-dinner.mp4" type="video/mp4">
          <p>Your browser cannot play this video.
             <a href="family-dinner.mp4">Download it instead</a>.</p>
        </video>
      </figure>
      <div class="kfc-video-copy reveal">
        <p class="section-tag">Our Community</p>
        <h2 class="section-title">We are what<br><em>we eat.</em></h2>
        <p class="kfc-video-caption">
          A family in Jamaica preparing and sharing a meal together.
        </p>
      </div>
    </div>
  </section>
  <!-- KFC:VIDEO END -->
'''


def add_homepage_video(html, filename, report):
    """Place the client-confirmed Family Dinner video on the homepage."""
    if filename not in (HOMEPAGE, ALIAS_HOME):
        return html
    if not os.path.exists(os.path.join(ROOT, 'family-dinner.mp4')):
        return html

    # Already exactly right, so leave the file alone. Comparing against the
    # generated block rather than just looking for the marker keeps the pass
    # idempotent while still picking up an edit to HOMEPAGE_VIDEO.
    if HOMEPAGE_VIDEO in html:
        return html

    html = re.sub(r'\n*[ \t]*<!-- KFC:VIDEO START.*?<!-- KFC:VIDEO END -->\n*',
                  '\n', html, flags=re.S)

    # After the mission section, which is where the human side of the site
    # already lives, and before The Bigger Picture.
    anchor = re.search(r'\n[ \t]*<section[^>]*id="problem"', html)
    if not anchor:
        anchor = re.search(r'\n[ \t]*<section[^>]*class="[^"]*social-strip', html)
    if not anchor:
        report.append('WARNING: no anchor for the homepage video')
        return html

    html = html[:anchor.start()] + HOMEPAGE_VIDEO + html[anchor.start():]
    report.append('placed the Family Dinner video on the homepage')
    return html


# Adapted from the client-supplied location-map.html. Kept: general Kingston
# only with no street address, OpenStreetMap, and the plain geo: directions
# link. Changed: the supplied file was a whole standalone light-theme document
# with its own <h1> and its own font stack, so only the card is carried over,
# restyled to the site, and the heading demoted to <h2> so the page keeps one
# <h1>. The iframe gains a title, which it needs for screen readers, and a
# referrer policy so OpenStreetMap is not told which page embedded it.
ABOUT_MAP = '''
  <!-- KFC:MAP START  generated by build/migrate.py, adapted from location-map.html -->
  <section class="kfc-map">
    <div class="kfc-map-card reveal">
      <div class="kfc-map-head">
        <p class="section-tag">Rooted In</p>
        <h2>Kingston, <em>Jamaica</em></h2>
        <p class="kfc-map-sub">Where Kwik Fat Cut was planted, and where it still grows from.</p>
      </div>
      <div class="kfc-map-embed">
        <p class="kfc-map-fallback">
          Map unavailable.
          <a href="https://www.openstreetmap.org/?mlat=17.9714&amp;mlon=-76.7936#map=12/17.97/-76.79"
             target="_blank" rel="noopener">View Kingston on OpenStreetMap</a>
        </p>
        <iframe
          class="kfc-map-frame"
          title="Map showing Kingston, Jamaica"
          loading="lazy"
          referrerpolicy="no-referrer"
          src="https://www.openstreetmap.org/export/embed.html?bbox=-76.85%2C17.92%2C-76.72%2C18.03&amp;layer=mapnik&amp;marker=17.9714%2C-76.7936"></iframe>
      </div>
      <div class="kfc-map-foot">
        <p class="kfc-map-loc">Kingston, Jamaica</p>
        <a class="kfc-map-btn" href="geo:17.9714,-76.7936?q=17.9714,-76.7936(Kingston,Jamaica)">Get Directions</a>
      </div>
    </div>
  </section>
  <!-- KFC:MAP END -->
'''


def add_about_map(html, filename, report):
    """Put the Kingston location card on the About page."""
    if filename != 'about.html':
        return html

    if ABOUT_MAP in html:
        return html

    html = re.sub(r'\n*[ \t]*<!-- KFC:MAP START.*?<!-- KFC:MAP END -->\n*',
                  '\n', html, flags=re.S)

    m = re.search(r'<section class="roots-teaser".*?</section>', html, re.S)
    if not m:
        report.append('WARNING: roots-teaser anchor not found, map not placed')
        return html

    html = html[:m.end()] + ABOUT_MAP + html[m.end():]
    report.append('placed the Kingston location card on the About page')
    return html


# --------------------------------------------------------------------------
# Privacy policy.
#
# The five sections the Phase 1 scope names, added to the two the page shipped
# with. Every statement is written from an audit of what the site actually
# does, not from a template.
#
# Third-Party Services carries the OpenStreetMap disclosure. That has to land
# in the same change as the map itself: the moment the About page embeds an
# OSM iframe, any claim that the site has no embeds and no third-party request
# beyond Google Fonts becomes false, and the visitor's IP reaches OpenStreetMap
# on page load whether or not they interact with the map.
# --------------------------------------------------------------------------
PRIVACY_SECTIONS = [
    ('section3', 'Cookies &amp; Local Storage',
     'No cookies. <em>One setting, stored on your device.</em>',
     '''<p>Kwik Fat Cut sets no cookies. Nothing about your visit is written to a cookie, and nothing is used to follow you between sites.</p>
      <p>The site stores a single value in your browser&rsquo;s local storage: your chosen language. It is saved under the key <code>kfc-locale</code> and holds a short code such as <code>en-GB</code> or <code>es</code>. It exists so the site does not have to ask you again on every page. It never leaves your device, it is not sent to us, and it identifies nothing about you.</p>
      <p>You can clear it at any time through your browser&rsquo;s settings for site data. Clearing it simply means the site guesses your language again on your next visit.</p>'''),

    ('section4', 'Analytics',
     'We do not measure <em>you.</em>',
     '''<p>Kwik Fat Cut runs no analytics software. There is no Google Analytics, no tag manager, no pixel, no heatmap tool, and no visitor tracking script of any kind on this site.</p>
      <p>Our web host keeps standard server logs, as every web host does. These record the page requested, the time, and the requesting IP address, and they exist to keep the site running and secure. They are not used to build a profile of you and are not shared.</p>
      <!-- CLIENT NOTE: accurate as of this build. If analytics are ever added to
           the site, this section must be rewritten before that change goes live. -->'''),

    ('section5', 'Data Retention',
     'We keep as little as possible, <em>for as short as possible.</em>',
     '''<p>We hold on to information only for as long as there is a reason to.</p>
      <p><strong>Donation records.</strong> Name, email and the record of your contribution are kept for as long as financial and charitable reporting obligations require, and then deleted. Payment card details are never stored by us at any point.</p>
      <p><strong>Enquiries.</strong> If you email us, we keep the correspondence for as long as it takes to deal with your question and for a reasonable period afterwards in case you follow up.</p>
      <p><strong>Your language setting.</strong> Stored on your own device until you clear it. We never hold a copy.</p>
      <!-- CLIENT NOTE: the retention periods above are deliberately described in
           plain terms rather than fixed numbers. If the client jurisdiction or
           their payment processor sets specific periods, state those numbers here
           instead. This needs a legal review before publication. -->'''),

    ('section6', 'Third-Party Services',
     'The short list of others <em>involved at all.</em>',
     '''<p>We keep outside services to a minimum. These are the only ones involved in a normal visit.</p>
      <p><strong>Google Fonts.</strong> The site&rsquo;s typefaces are loaded from Google&rsquo;s font servers. Making that request means your IP address is visible to Google, as it is with any file your browser fetches from another domain. No account is needed and nothing is stored on your device by it.</p>
      <p><strong>OpenStreetMap.</strong> The About page embeds a map of Kingston from OpenStreetMap. Because it is an embedded frame rather than a link, your browser contacts OpenStreetMap when that page loads, whether or not you use the map, and your IP address is visible to them in the same way it is to Google Fonts. The map shows the general Kingston area only. It carries no address of ours, and we send no information about you with the request. It is the only embedded frame anywhere on this site.</p>
      <p><strong>Donation processing.</strong> Contributions made through the support page are handled by an external payment provider. Your payment details go to them directly and are governed by their own privacy policy, not this one.</p>
      <p><strong>Links out.</strong> Some pages link to YouTube and to our social accounts. Those are ordinary links, not embeds, so those platforms are not contacted until you choose to follow one. Once you do, you are on their site and under their policy.</p>
      <!-- CLIENT NOTE: name the payment provider explicitly and link to its privacy
           policy. Add the chatbot provider here as well, since the chatbot is on the
           live site but not in this package and its data handling is unknown. -->'''),

    ('section7', 'Your Rights',
     'Your information <em>stays yours.</em>',
     '''<p>You can ask us what we hold about you, ask us to correct it, or ask us to delete it. You do not need to give a reason and it costs nothing.</p>
      <p>Write to <a href="mailto:kwik@kwikfatcut.com">kwik@kwikfatcut.com</a> with what you would like done. We will confirm receipt and respond as quickly as we reasonably can.</p>
      <p>If you would rather simply stop the site remembering your language, clearing your browser&rsquo;s site data for kwikfatcut.com does that immediately, with no need to contact anyone.</p>
      <p>Depending on where you live you may also have the right to complain to a data protection authority. Nothing here limits that.</p>'''),
]

PRIVACY_LEDE_OLD = ('This page sets out exactly how the platform handles your '
                    'information.')
PRIVACY_LEDE_NEW = ('This page sets out exactly what the platform stores, what '
                    'it does not, and how to have any of it removed.')


def add_privacy_sections(html, filename, report):
    if filename != 'privacy.html':
        return html

    blocks = ''.join(
        '\n    <div class="article-section reveal" id="%s">\n'
        '      <p class="sl">%s</p>\n'
        '      <h2>%s</h2>\n'
        '      %s\n'
        '    </div>\n' % s for s in PRIVACY_SECTIONS)

    # Already exactly right, so do not touch the file.
    if blocks in html:
        return html

    # Clear any previous run. The lookahead matters: consuming the newline
    # after the final </div> would weld the next <section> onto it, which is
    # what an earlier version of this did to the share strip.
    html = re.sub(
        r'\n[ \t]*<div class="article-section reveal" id="section[3-7]">'
        r'.*?\n[ \t]*</div>(?=\n)',
        '', html, flags=re.S)
    html = re.sub(r'\n[ \t]*<a href="#section[3-7]">[^<]*</a>', '', html)

    anchor = re.search(
        r'(<div class="article-section reveal" id="section2">.*?\n[ \t]*</div>\n)',
        html, re.S)
    if not anchor:
        report.append('WARNING: privacy section2 anchor not found')
        return html

    html = html[:anchor.end()] + blocks + html[anchor.end():]

    toc = re.search(r'(<a href="#section2">[^<]*</a>)', html)
    if toc:
        links = ''.join('\n      <a href="#%s">%s</a>' % (s[0], s[1])
                        for s in PRIVACY_SECTIONS)
        html = html[:toc.end()] + links + html[toc.end():]

    html = html.replace(PRIVACY_LEDE_OLD, PRIVACY_LEDE_NEW)
    report.append('added the 5 scoped privacy sections, incl. OpenStreetMap')
    return html


# --------------------------------------------------------------------------
# Body
# --------------------------------------------------------------------------
# The header nav is the only <nav> with no class, or one already tagged
# site-nav from a previous run. Footer navs ("footer-nav" on 32 pages, "fn" on
# 16) and the article sidebar table of contents ("toc" on 35) must not match.
HEADER_NAV_RE = re.compile(
    r'<nav(?:\s+class="site-nav")?\s*>.*?</nav>', re.S)


def replace_nav(html):
    m = HEADER_NAV_RE.search(html)
    return html if not m else html[:m.start()] + nav_html() + html[m.end():]


# Two footer nav variants shipped in the source: class "footer-nav" on 32
# pages and class "fn" on 16, with different link sets and different CSS.
# Both are normalised onto footer-nav so one rule in kfc.css covers them.
FOOTER_NAV_RE = re.compile(r'<nav class="(?:footer-nav|fn)"[^>]*>.*?</nav>', re.S)


def replace_footer_nav(html):
    m = FOOTER_NAV_RE.search(html)
    return html if not m else html[:m.start()] + footer_nav_html() + html[m.end():]


def add_main_landmark(html):
    """Wrap page content in <main id="main"> for the skip link and for SEO."""
    if 'id="main"' in html:
        return html
    nav_end = html.find('</nav>')
    foot = html.rfind('<footer')
    if nav_end == -1 or foot == -1 or foot < nav_end:
        return html
    nav_end += len('</nav>')
    return (html[:nav_end] + '\n<main id="main">\n' +
            html[nav_end:foot] + '</main>\n' + html[foot:])


def add_skip_link(html):
    if 'class="skip-link"' in html:
        return html
    return re.sub(r'(<body[^>]*>)',
                  r'\1\n<a class="skip-link" href="#main">Skip to content</a>',
                  html, count=1)


def fix_logo_links(html):
    """href="#" on the logo and footer logo should reach the homepage."""
    html = html.replace('<a href="#" class="logo"',
                        '<a href="%s" class="logo"' % HOMEPAGE)
    html = html.replace('<a href="#" class="footer-logo"',
                        '<a href="%s" class="footer-logo"' % HOMEPAGE)
    return html


VIDEO_RE = re.compile(r'<video\b[^>]*>')


def optimise_video(html, report):
    """Stop large videos downloading before anyone asks for them.

    Both videos on this site are several megabytes. The markup shipped with
    preload="metadata" and poster="" (an empty poster is invalid and makes
    some browsers request the page itself as an image). preload="none" plus a
    real poster frame means the visitor downloads about 50KB instead of 5MB
    unless they press play. playsinline stops iOS taking over the screen.
    """
    def repl(m):
        tag = m.group(0)
        after = html[m.end():m.end() + 400]
        src = re.search(r'<source[^>]*src="([^"]+)"', after)
        if not src:
            return tag
        name = src.group(1)
        path = os.path.join(ROOT, name)
        if not os.path.exists(path) or os.path.getsize(path) <= 2 * 1024 * 1024:
            return tag

        tag = re.sub(r'\s*preload="[^"]*"', '', tag)
        tag = tag[:-1].rstrip() + ' preload="none">'

        poster = os.path.splitext(name)[0] + '-poster.jpg'
        if os.path.exists(os.path.join(ROOT, poster)):
            tag = re.sub(r'\s*poster="[^"]*"', '', tag)
            tag = tag[:-1].rstrip() + ' poster="%s">' % poster
        if 'playsinline' not in tag:
            tag = tag[:-1].rstrip() + ' playsinline>'
        return tag

    out = VIDEO_RE.sub(repl, html)
    if out != html:
        report.append('set preload="none" and a poster on a large video')
    return out


IMG_RE = re.compile(r'<img\b[^>]*>')


def optimise_images(html):
    """First image eager, it is the hero. Everything after it lazy."""
    seen = [0]

    def repl(m):
        tag = m.group(0)
        seen[0] += 1
        if 'loading=' not in tag:
            attr = ('loading="eager" fetchpriority="high"'
                    if seen[0] == 1 else 'loading="lazy"')
            tag = tag[:-1].rstrip() + ' %s>' % attr
        if 'decoding=' not in tag:
            tag = tag[:-1].rstrip() + ' decoding="async">'
        return tag

    return IMG_RE.sub(repl, html)


B64_RE = re.compile(
    r'src="data:image/(png|jpe?g|webp);base64,([A-Za-z0-9+/=\s]{2000,})"')


def extract_base64(html, report):
    """Pull inlined base64 images out into real cacheable files."""
    def repl(m):
        ext = 'jpg' if m.group(1).startswith('jpe') else m.group(1)
        raw = base64.b64decode(re.sub(r'\s+', '', m.group(2)))
        name = 'img-%s.%s' % (hashlib.sha1(raw).hexdigest()[:10], ext)
        path = os.path.join(ROOT, name)
        if not DRY and not os.path.exists(path):
            with open(path, 'wb') as fh:
                fh.write(raw)
        report.append('extracted %s (%d KB out of the HTML)' % (name, len(raw) // 1024))
        return 'src="%s"' % name

    return B64_RE.sub(repl, html)


# The reveal snippet, duplicated across 48 pages, replaced by kfc.js.
#
# This is ANCHORED AT BOTH ENDS on purpose. An earlier version matched the
# opening statement and then scanned forward with a non-greedy `.*?}\s*\)\s*;`
# for the end. That terminated early, at the `}});` inside the observer's own
# callback, and left `},{threshold:0.07});` behind on 47 pages. A stray
# fragment like that is a syntax error, and a syntax error anywhere in a
# <script> block kills every function in it, so shareKFC, copyURL and the
# donation page's handleDonate all silently stopped working.
#
# Anchoring on the final `els.forEach(el => io.observe(el));` makes an early
# stop impossible: either the whole three-statement block matches, or nothing
# is removed and build/verify.py reports the leftover.
OBSERVER_BLOCK = re.compile(
    r'\n?[ \t]*(?:const|var|let)\s+els\s*=\s*document\.querySelectorAll\(\s*[\'"]\.reveal[\'"]\s*\)\s*;'
    r'.*?'
    r'els\.forEach\(\s*el\s*=>\s*io\.observe\(\s*el\s*\)\s*\)\s*;',
    re.S)


def swap_scripts(html, report):
    html, n = OBSERVER_BLOCK.subn('', html)

    # Nothing matched but the observer is still there: the snippet has a shape
    # this pattern does not know. Say so rather than half-removing it.
    if not n and 'IntersectionObserver' in html and '<script' in html:
        if re.search(r'querySelectorAll\(\s*[\'"]\.reveal[\'"]', html):
            report.append('WARNING: reveal observer present but not matched, '
                          'left in place, check build/verify.py')

    html = re.sub(r'<script>\s*</script>', '', html)
    html = re.sub(r'\n?<script src="kfc(?:-i18n)?\.js" defer></script>', '', html)
    return html.replace(
        '</body>',
        '<script src="kfc.js" defer></script>\n'
        '<script src="kfc-i18n.js" defer></script>\n</body>', 1)


# --------------------------------------------------------------------------
# Pass
# --------------------------------------------------------------------------
def process(filename):
    path = os.path.join(ROOT, filename)
    with open(path, encoding='utf-8') as fh:
        original = fh.read()

    report = []
    html = original

    def css_bytes(h):
        return sum(len(b) for b in re.findall(r'<style[^>]*>(.*?)</style>', h, re.S))

    before = css_bytes(html)
    html = re.sub(r'(<style[^>]*>)(.*?)(</style>)',
                  lambda m: m.group(1) + strip_css(m.group(2)) + m.group(3),
                  html, flags=re.S)
    after = css_bytes(html)
    if before != after:
        report.append('inline CSS %d -> %d bytes' % (before, after))

    html = repair_markup(html, filename, report)
    html = normalise_spelling(html, report)
    html = fix_meta_description(html, filename, report)
    html = fix_heading_levels(html, filename, report)

    # Client changes run before the nav is replaced, so the canonical nav is
    # the last word on what the nav contains.
    html = remove_download_section(html, filename, report)
    html = remove_premium_card(html, filename, report)
    html = update_support_copy(html, filename, report)
    html = repoint_download_links(html, report)
    html = add_privacy_sections(html, filename, report)
    html = add_homepage_video(html, filename, report)
    html = add_about_map(html, filename, report)

    html = inject_head(html, filename)
    html = replace_nav(html)
    html = replace_footer_nav(html)
    html = fix_logo_links(html)
    html = add_main_landmark(html)
    html = add_skip_link(html)
    html = extract_base64(html, report)
    html = optimise_images(html)
    html = optimise_video(html, report)
    html = swap_scripts(html, report)

    changed = html != original
    if changed and not DRY:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(html)
    return changed, report


def write_sitemap(pages):
    today = date.today().isoformat()
    rows = []
    for p in sorted(pages):
        loc = SITE_URL if p == HOMEPAGE else SITE_URL + p
        pri = '1.0' if p == HOMEPAGE else ('0.6' if p.startswith('learn-') else '0.8')
        rows.append('  <url>\n    <loc>%s</loc>\n    <lastmod>%s</lastmod>\n'
                    '    <priority>%s</priority>\n  </url>' % (loc, today, pri))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + '\n'.join(rows) + '\n</urlset>\n')
    if not DRY:
        with open(os.path.join(ROOT, 'sitemap.xml'), 'w', encoding='utf-8') as fh:
            fh.write(xml)
    return len(rows)


def write_robots():
    txt = ('User-agent: *\nAllow: /\n'
           + ''.join('Disallow: /%s\n' % p for p in sorted(NOINDEX))
           + '\nSitemap: %ssitemap.xml\n' % SITE_URL)
    if not DRY:
        with open(os.path.join(ROOT, 'robots.txt'), 'w', encoding='utf-8') as fh:
            fh.write(txt)


# The client supplied location-map.html as a standalone light-theme document
# to take the card from, not as a page to publish. Left in the web root it
# would be a public URL with its own <h1>, its own font stack and none of the
# site chrome. It is moved into build/ as a reference copy instead, and the
# card itself lives on the About page.
SOURCE_SNIPPETS = {'location-map.html': 'build/location-map.source.html'}


def park_source_snippets():
    for name, dest in SOURCE_SNIPPETS.items():
        src = os.path.join(ROOT, name)
        if not os.path.exists(src):
            continue
        target = os.path.join(ROOT, dest)
        if DRY:
            print('%-42s would move to %s' % (name, dest))
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(src, target)
        print('%-42s moved to %s, not a published page' % (name, dest))


def main():
    park_source_snippets()
    files = sorted(f for f in os.listdir(ROOT) if f.endswith('.html'))
    touched = 0
    for f in files:
        changed, report = process(f)
        touched += 1 if changed else 0
        print('%-42s %s' % (f, 'rewritten' if changed else 'no change'))
        for line in report:
            print('%-42s   %s' % ('', line))

    indexable = [f for f in files if f not in NOINDEX and f != ALIAS_HOME]
    count = write_sitemap(indexable)
    write_robots()

    print('\n%d of %d pages rewritten' % (touched, len(files)))
    print('sitemap.xml: %d URLs (was 39)' % count)
    if DRY:
        print('\nDRY RUN: nothing written.')


if __name__ == '__main__':
    main()
