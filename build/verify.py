#!/usr/bin/env python3
"""
Kwik Fat Cut pre-push verification gate.

Run this before every push to main. main auto-deploys to Cloudflare, so a
broken build goes straight to production.

    python3 build/verify.py

Exits non-zero if anything fails, so it drops into CI or a git pre-push hook.

Checks, in order of how badly they burn:

  1. Inline JavaScript parses.
     This exists because it caught a real regression. The observer-removal
     step in migrate.py used to scan forward for the end of the statement and
     stopped early, leaving `},{threshold:0.07});` behind on 47 pages. A stray
     fragment is a syntax error, and a syntax error anywhere in a <script>
     block kills every function declared in it, so the share buttons and the
     donation page's handler silently died. Nothing else in the suite noticed,
     because the localisation smoke test runs kfc.js in jsdom and never
     executes page-level inline script.

  2. Payment honesty.
     The donation page must not claim live payment processing while the
     Donate button has no gateway behind it. See PAYMENTS_LIVE in migrate.py.

  3. Structure, links, headings, leftovers.
"""

import os
import re
import sys
import glob
import json
import shutil
import subprocess
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Internal reference documents, noindexed and out of the sitemap. Standalone
# tables, not site pages, so they carry no site nav by design.
NOT_SITE_PAGES = {'kwik-fatcut-block-map.html'}

FAILURES = []
NOTES = []


def fail(check, detail):
    FAILURES.append((check, detail))


def pages():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, '*.html')))


def read(f):
    with open(os.path.join(ROOT, f), encoding='utf-8') as fh:
        return fh.read()


# --------------------------------------------------------------------------
# 1. Inline JavaScript parses
# --------------------------------------------------------------------------
SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>')


def check_inline_js():
    node = shutil.which('node')
    if not node:
        NOTES.append('node not found, inline JS syntax check SKIPPED')
        return

    checker = os.path.join(ROOT, 'build', '_syntax.js')
    with open(checker, 'w', encoding='utf-8') as fh:
        fh.write(
            'const vm=require("vm");let s="";'
            'process.stdin.on("data",d=>s+=d).on("end",()=>{'
            'try{new vm.Script(s);console.log("OK");}'
            'catch(e){console.log("ERR "+e.message.split("\\n")[0]);}});'
        )

    broken = 0
    try:
        for f in pages():
            for i, code in enumerate(SCRIPT_RE.findall(read(f)), 1):
                if not code.strip():
                    continue
                out = subprocess.run([node, checker], input=code, text=True,
                                     capture_output=True).stdout.strip()
                if out.startswith('ERR'):
                    broken += 1
                    fail('inline JS', '%s script #%d: %s' % (f, i, out[4:]))
    finally:
        if os.path.exists(checker):
            os.remove(checker)

    if not broken:
        print('  inline JavaScript parses on all %d pages' % len(pages()))


def check_observer_leftovers():
    """Belt and braces: the exact fragment the old bug left behind."""
    hits = [f for f in pages()
            if re.search(r'\}\s*,\s*\{\s*threshold\s*:', read(f))
            and 'IntersectionObserver' not in read(f)]
    if hits:
        fail('observer leftover',
             'orphaned threshold fragment in: ' + ', '.join(hits))
    else:
        print('  no orphaned observer fragments')


# --------------------------------------------------------------------------
# 2. Payment honesty
# --------------------------------------------------------------------------
def check_payment_honesty():
    if 'support.html' not in pages():
        return
    html = read('support.html')

    # Is a real gateway wired up?
    wired = bool(
        re.search(r'<form[^>]+action=', html) or
        re.search(r'(wipay|paypal)[a-z.]*\.(com|co|net)', html, re.I) or
        re.search(r'location\.(href|assign)\s*=', html)
    )
    # Is the placeholder handler still there?
    placeholder = 'Replace this function with your' in html or \
                  bool(re.search(r'setTimeout\([^)]*\n?\s*\}\s*,\s*3000\s*\)', html))

    claims_live = bool(re.search(r'Secured by WiPay', html)) or \
        bool(re.search(r'Donations are processed securely by', html))

    if claims_live and not wired:
        fail('payment honesty',
             'support.html claims live WiPay/PayPal processing but no gateway '
             'is wired. Set PAYMENTS_LIVE only after connecting one.')
    elif placeholder and claims_live:
        fail('payment honesty',
             'support.html still contains the placeholder handler while '
             'claiming live processing.')
    else:
        state = 'live' if wired else 'pending, copy is future tense'
        print('  donation page consistent with payment state (%s)' % state)

    # A disabled button and a live claim is also inconsistent.
    if 'donate-btn' in html and 'disabled' in html and claims_live:
        fail('payment honesty',
             'donate button is disabled but the copy claims live processing')


# --------------------------------------------------------------------------
# 3. Structure, links, headings, leftovers
# --------------------------------------------------------------------------
def check_structure():
    from lxml import html as lh
    bad = 0
    for f in pages():
        d = lh.fromstring(read(f))
        issues = []
        if len(d.xpath('//main')) != 1:
            issues.append('main=%d' % len(d.xpath('//main')))
        if len(d.xpath('//h1')) != 1:
            issues.append('h1=%d' % len(d.xpath('//h1')))
        # The header nav carries class="site-nav" so the fixed-header CSS can
        # be scoped to it and never leak onto the footer nav or a sidebar
        # table of contents. Reference docs legitimately have none.
        n_site = len(d.xpath('//nav[@class="site-nav"]'))
        want = 0 if f in NOT_SITE_PAGES else 1
        if n_site != want:
            issues.append('site-nav=%d (want %d)' % (n_site, want))
        stray = [n.get('class') or '(none)'
                 for n in d.xpath('//nav') if n.get('class') != 'site-nav'
                 and n.get('class') not in ('footer-nav', 'toc', 'page-nav')]
        if stray:
            issues.append('unknown nav class: ' + ','.join(stray))
        if d.xpath('//main//footer'):
            issues.append('footer inside main')
        if not d.xpath('//meta[@name="description"]'):
            issues.append('no meta description')
        lv = [int(e.tag[1]) for e in d.xpath('//h1|//h2|//h3|//h4|//h5|//h6')]
        if any(lv[i + 1] - lv[i] > 1 for i in range(len(lv) - 1)):
            issues.append('skipped heading level')
        if issues:
            bad += 1
            fail('structure', '%s: %s' % (f, ', '.join(issues)))
    if not bad:
        print('  structure clean on all %d pages' % len(pages()))


def check_links():
    bad = 0
    for f in pages():
        for target in re.findall(r'(?:href|src)="([^"#:]+)"', read(f)):
            t = target.split('#')[0]
            if not t or t.startswith(('http', 'mailto', 'tel', 'data:')):
                continue
            if not os.path.exists(os.path.join(ROOT, t)):
                bad += 1
                fail('broken link', '%s -> %s' % (f, t))
    if not bad:
        print('  no broken local links')


def check_orphans():
    navf = re.compile(r'<nav\b.*?</nav>|<footer\b.*?</footer>', re.S)
    inbound = collections.Counter()
    for f in pages():
        body = navf.sub('', read(f))
        for m in re.findall(r'href="([^"#:]+\.html)', body):
            inbound[os.path.basename(m)] += 1
    articles = [f for f in pages() if f.startswith('learn-')] + ['opian-meals.html']
    orphans = [a for a in articles if inbound[a] == 0]
    if orphans:
        fail('orphaned articles', ', '.join(orphans))
    else:
        print('  no orphaned articles (%d checked)' % len(articles))


def check_removed_content():
    """The September client removals must stay removed."""
    banned = {
        'Deeper, if you choose': 'premium card',
        'Premium features': 'premium copy',
        'go deeper with us': 'premium hook',
        'App Store': 'app store button',
        'Google Play': 'play store button',
        'log your first meal': 'app download copy',
    }
    exempt = {'kwik-fatcut-block-map.html'}   # internal noindexed reference doc
    bad = 0
    for f in pages():
        if f in exempt:
            continue
        html = read(f)
        for needle, what in banned.items():
            if needle in html:
                bad += 1
                fail('removed content returned', '%s still has %s' % (f, what))
    for f in pages():
        if re.search(r'href="[^"]*#download"', read(f)):
            bad += 1
            fail('dead link', '%s links to the removed #download section' % f)
    if not bad:
        print('  client removals still in place, no #download links')


def check_embeds_disclosed():
    """Any third-party embed must be named in the privacy policy.

    The About page now carries an OpenStreetMap iframe. An embed contacts the
    third party on page load whether or not the visitor touches it, so the
    privacy policy has to say so, and it has to stop claiming the site has no
    embeds. This check exists so the two can never drift apart: add an iframe
    to any page and the build fails until the policy names its host.
    """
    hosts = {}
    for f in pages():
        for m in re.finditer(r'<iframe[^>]*\bsrc="https?://([^/"]+)', read(f)):
            hosts.setdefault(m.group(1), set()).add(f)

    if 'privacy.html' not in pages():
        return
    policy = read('privacy.html')

    # A leftover "no embeds" claim is worse than a missing one.
    for phrase in ('no iframes', 'It is the only embedded frame',
                   'Nothing from those platforms is embedded'):
        if phrase in policy and not hosts:
            continue
    if not hosts and 'It is the only embedded frame' in policy:
        fail('privacy accuracy',
             'privacy.html describes an embedded frame but no page has one')

    known = {
        'www.openstreetmap.org': 'OpenStreetMap',
        'openstreetmap.org': 'OpenStreetMap',
        'www.youtube.com': 'YouTube',
        'player.vimeo.com': 'Vimeo',
        'www.google.com': 'Google',
    }
    for host, where in sorted(hosts.items()):
        name = known.get(host)
        named = (name and name in policy) or host in policy
        if not named:
            fail('privacy accuracy',
                 'iframe from %s on %s is not disclosed in privacy.html'
                 % (host, ', '.join(sorted(where))))

    # Accessibility: an iframe with no title is unusable with a screen reader.
    for f in pages():
        for m in re.finditer(r'<iframe(?![^>]*\btitle=)[^>]*>', read(f)):
            fail('iframe accessibility', '%s: iframe without a title' % f)

    if hosts:
        print('  %d embed host(s) disclosed in the privacy policy: %s'
              % (len(hosts), ', '.join(sorted(hosts))))
    else:
        print('  no third-party embeds on any page')


def check_media():
    """Large media must not be fetched before the visitor asks for it."""
    for f in pages():
        for m in re.finditer(r'<video\b[^>]*>', read(f)):
            tag = m.group(0)
            src = re.search(r'<video[^>]*>\s*<source[^>]*src="([^"]+)"',
                            read(f)[m.start():m.start() + 400])
            name = src.group(1) if src else '(unknown)'
            path = os.path.join(ROOT, name)
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size > 2 * 1024 * 1024:
                if 'preload="none"' not in tag:
                    fail('media loading',
                         '%s: %s is %dKB but preload is not "none"'
                         % (f, name, size // 1024))
                if 'poster=' not in tag or 'poster=""' in tag:
                    fail('media loading',
                         '%s: %s has no poster frame, so nothing shows until '
                         'the visitor presses play' % (f, name))
    print('  video loading behaviour checked')


def check_i18n():
    cfg = os.path.join(ROOT, 'i18n', 'locales.json')
    if not os.path.exists(cfg):
        fail('i18n', 'i18n/locales.json missing')
        return
    try:
        locales = json.load(open(cfg, encoding='utf-8'))['locales']
    except (ValueError, KeyError) as e:
        fail('i18n', 'locales.json unreadable: %s' % e)
        return
    for code, spec in locales.items():
        if spec.get('mode') == 'transform':
            p = os.path.join(ROOT, 'i18n', spec['transform'] + '.json')
            if not os.path.exists(p):
                fail('i18n', '%s needs missing %s' % (code, p))
        if spec.get('mode') == 'dictionary':
            d = os.path.join(ROOT, 'i18n', spec['dictionary'])
            if not os.path.isdir(d):
                fail('i18n', '%s needs missing folder %s' % (code, d))
    missing = [f for f in ('kfc.css', 'kfc.js', 'kfc-i18n.js')
               if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        fail('i18n', 'shared layer missing: ' + ', '.join(missing))
    if not FAILURES:
        print('  i18n config and shared layer present')


def main():
    print('Kwik Fat Cut verification\n')
    print('1. JavaScript')
    check_inline_js()
    check_observer_leftovers()
    print('\n2. Payment honesty')
    check_payment_honesty()
    print('\n3. Structure and content')
    check_structure()
    check_links()
    check_orphans()
    check_removed_content()
    check_embeds_disclosed()
    check_media()
    check_i18n()

    for n in NOTES:
        print('\nNOTE: %s' % n)

    if FAILURES:
        print('\n%d FAILURE(S):' % len(FAILURES))
        for check, detail in FAILURES[:40]:
            print('  [%s] %s' % (check, detail))
        if len(FAILURES) > 40:
            print('  ... and %d more' % (len(FAILURES) - 40))
        print('\nDO NOT PUSH.')
        sys.exit(1)

    print('\nAll static checks passed.')
    print('Now run the rendered checks, which catch what this one cannot:')
    print('    node build/layout-test.js')
    print('    node build/smoke-test.js')


if __name__ == '__main__':
    main()
