#!/usr/bin/env python3
"""
Kwik Fat Cut string extraction and spelling audit.

Three jobs:

  extract   Walk every page, key each translatable string by a content hash,
            and write per-page JSON skeletons for a translator to fill in.
            Strings that appear on many pages are pooled into _common.json so
            the nav, footer and share block are translated once, not 50 times.

  audit     Report British spellings that appear in the content but are
            missing from i18n/en-US.json, and American spellings sitting in
            what is meant to be a British source. Run this after any content
            edit, otherwise the en-US locale silently drifts.

  status    Report Spanish progress against the client's confirmed 13 pages.

Usage:
    python3 build/extract-strings.py extract es --scope    the 13 pages only
    python3 build/extract-strings.py extract es --merge    keep existing work
    python3 build/extract-strings.py audit
    python3 build/extract-strings.py status

The hash must stay identical to the one in kfc-i18n.js. If you change it
here, change it there, and re-extract every locale.
"""

import os
import re
import sys
import json
import glob
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, 'i18n')

SKIP_TAGS = {'script', 'style', 'code', 'pre', 'noscript', 'svg'}

# A string on at least this many pages is chrome, not page content.
COMMON_THRESHOLD = 10

# --------------------------------------------------------------------------
# The client's confirmed Spanish scope, sent 21 September.
#
# Eleven map to a file in the repo. Two do not:
#
#   Library             No page of that name exists. Candidates are
#                       foods-hub.html, learn-ancient-archive.html and
#                       learn-jamaican-foods-az.html. Needs the client to say
#                       which, or a URL.
#   Tash's LiquidFood   Brand new page. The client is sending the URL and
#                       content. Nothing to extract until it exists.
#
# Do not guess at either. A wrong guess means paying a translator to
# translate the wrong page.
# --------------------------------------------------------------------------
SPANISH_SCOPE = [
    'index.html',            # Homepage (/)
    'learn-ackee.html',
    'learn.html',            # Learn Hub
    'learn-water.html',
    'team.html',
    'learn-taino-diet.html',
    'support.html',          # donation page
    'founder.html',
    'about.html',
    'thank-you.html',
    'why-we-exist.html',
]

SPANISH_UNRESOLVED = {
    'Library': 'no page of that name; candidates foods-hub.html, '
               'learn-ancient-archive.html, learn-jamaican-foods-az.html',
    "Tash's LiquidFood": 'new page, client sending URL and content',
}


def fnv1a(text):
    """FNV-1a 32 bit over UTF-16 code units.

    JavaScript's charCodeAt yields UTF-16 units, so an emoji is two units
    there and one codepoint in Python. The site uses emoji in the Roots
    cards and several feature grids, so the string has to be encoded to
    UTF-16 first or those keys would never match.
    """
    units = text.encode('utf-16-le')
    h = 0x811c9dc5
    for i in range(0, len(units), 2):
        code = units[i] | (units[i + 1] << 8)
        h ^= code
        h = (h + (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24)) & 0xFFFFFFFF
    return '%08x' % h


def normalise(text):
    return re.sub(r'\s+', ' ', text).strip()


def walk_strings(path):
    """Yield the normalised text of every translatable node on a page."""
    from lxml import html as lh

    doc = lh.fromstring(open(path, encoding='utf-8').read())
    body = doc.body if doc.body is not None else doc

    out = []
    for node in body.iter():
        # Comments and processing instructions have a callable tag in lxml.
        # Their text is markup notes, not copy.
        if not isinstance(node.tag, str):
            continue
        if node.tag.lower() in SKIP_TAGS:
            continue

        for chunk in (node.text, node.tail):
            if not chunk or not chunk.strip():
                continue
            parent = node.getparent() if chunk is node.tail else node
            blocked = False
            while parent is not None:
                ptag = parent.tag if isinstance(parent.tag, str) else ''
                if ptag.lower() in SKIP_TAGS:
                    blocked = True
                    break
                parent = parent.getparent()
            if not blocked:
                out.append(normalise(chunk))
    return out


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------
def cmd_extract(locale, merge, scope_only):
    pages = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ROOT, '*.html')))

    per_page = {}
    counts = collections.Counter()
    for page in pages:
        strings = walk_strings(os.path.join(ROOT, page))
        per_page[page] = strings
        for s in set(strings):
            counts[s] += 1

    common = {s for s, n in counts.items() if n >= COMMON_THRESHOLD}
    out_dir = os.path.join(I18N, locale)
    os.makedirs(out_dir, exist_ok=True)

    def load_existing(name):
        p = os.path.join(out_dir, name)
        if merge and os.path.exists(p):
            return json.load(open(p, encoding='utf-8')).get('strings', {})
        return {}

    def write(name, strings, note):
        existing = load_existing(name)
        entries = {}
        for s in strings:
            key = fnv1a(s)
            entries[key] = {'src': s, 't': existing.get(key, {}).get('t', '')}
        payload = {
            '_note': note,
            '_locale': locale,
            '_instructions': (
                'Fill in every "t". Leave it empty and that string renders in '
                'English, which is safe but visibly mixed. Never edit "src" or '
                'the key: the key is a hash of "src" and changing either breaks '
                'the lookup. If source copy changes, re-run extract --merge.'
            ),
            'strings': entries
        }
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        done = sum(1 for e in entries.values() if e['t'])
        return len(entries), done

    total, translated = write(
        '_common.json', sorted(common),
        'Shared chrome: nav, footer, share block. Loaded on every page.')
    print('%-34s %4d strings, %d translated' % ('_common.json', total, translated))

    targets = SPANISH_SCOPE if scope_only else pages
    grand, grand_done = total, translated
    for page in targets:
        if page not in per_page:
            print('%-34s MISSING from the repo' % page)
            continue
        unique = sorted(set(per_page[page]) - common)
        if not unique:
            continue
        n, d = write(page.replace('.html', '.json'), unique,
                     'Page content for %s' % page)
        grand += n
        grand_done += d

    label = 'TOTAL (client scope)' if scope_only else 'TOTAL (all pages)'
    print('%-34s %4d strings, %d translated (%d%%)' %
          (label, grand, grand_done,
           round(100 * grand_done / grand) if grand else 0))
    print('\nWritten to %s/' % os.path.relpath(out_dir, ROOT))

    if scope_only and SPANISH_UNRESOLVED:
        print('\nNot extracted, still unresolved with the client:')
        for name, why in SPANISH_UNRESOLVED.items():
            print('   %-20s %s' % (name, why))


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def cmd_status():
    d = os.path.join(I18N, 'es')
    if not os.path.isdir(d):
        print('No Spanish files yet. Run: extract es --scope')
        return

    common_path = os.path.join(d, '_common.json')
    if os.path.exists(common_path):
        c = json.load(open(common_path, encoding='utf-8'))['strings']
        cdone = sum(1 for v in c.values() if v['t'])
        print('%-30s %5d strings %5d done  %3d%%' %
              ('_common.json (all pages)', len(c), cdone,
               round(100 * cdone / len(c)) if c else 0))

    print()
    tot = done = words = 0
    for page in SPANISH_SCOPE:
        f = os.path.join(d, page.replace('.html', '.json'))
        if not os.path.exists(f):
            print('%-30s not extracted' % page)
            continue
        st = json.load(open(f, encoding='utf-8'))['strings']
        dn = sum(1 for v in st.values() if v['t'])
        w = sum(len(re.findall(r'\w+', v['src'])) for v in st.values())
        tot += len(st); done += dn; words += w
        print('%-30s %5d strings %5d done  %3d%%  ~%d words' %
              (page, len(st), dn, round(100 * dn / len(st)) if st else 0, w))

    print('\n%-30s %5d strings %5d done  %3d%%  ~%d words' %
          ('SCOPE TOTAL (11 of 13)', tot, done,
           round(100 * done / tot) if tot else 0, words))
    print('\nStill unresolved:')
    for name, why in SPANISH_UNRESOLVED.items():
        print('   %-20s %s' % (name, why))


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
BRITISH_HINTS = [
    re.compile(r'^\w+(isation|isations)$'),
    re.compile(r'^\w+(ise|ised|ising|iser)$'),
    re.compile(r'^\w+(our|ours|oured|ouring)$'),
    re.compile(r'^\w+(tre|tres|bre|bres)$'),
    re.compile(r'^\w*(ae|oe)\w+$'),
    re.compile(r'^\w+(yse|ysed|ysing)$'),
]

# Words the hints catch that are identical in both variants.
NOT_VARIANTS = {
    'arise', 'advertise', 'advertising', 'compromise', 'compromised',
    'exercise', 'exercised', 'exercising', 'post-exercise', 'enterprise',
    'improvise', 'otherwise', 'paradise', 'precise', 'promise', 'raise',
    'raised', 'raising', 'rise', 'rising', 'sitting-rising', 'surprise',
    'surprised', 'treatise', 'wise', 'noise', 'fractalnoise', 'franchise',
    'merchandise', 'revise', 'supervise', 'televise', 'despise', 'disguise',
    'expertise', 'premise', 'demise', 'concise', 'poise', 'praise', 'cruise',
    'bruise', 'our', 'ours', 'your', 'yours', 'four', 'hour', 'hours',
    'flour', 'sour', 'tour', 'pour', 'devour', 'contour', 'glamour',
    'does', 'goes', 'echoed', 'canoe', 'canoes', 'aloe', 'algae', 'reggae',
    'potatoes', 'tomatoes', 'shoelaces', 'poetic', 'coercion', 'aerobic',
    'socioeconomic', 'archaeology', 'archaeological', 'archaeologists',
    'aegean', 'ajoene', 'averroes', 'israel', 'michael', 'paella',
    'practice', 'practices', 'licence', 'storey', 'genre', 'genres',
    'acre', 'acres', 'ogre', 'sabre', 'macabre', 'timbre', 'calibre',
}

# The American form is also correct British usage here, so its presence is
# not evidence of drift. British splits practice (noun) from practise (verb).
AMBIGUOUS = {'practice', 'practices', 'license', 'licenses'}


def load_protected():
    try:
        with open(os.path.join(I18N, 'protected.json'), encoding='utf-8') as fh:
            return json.load(fh).get('phrases', [])
    except (IOError, ValueError):
        return []


def cmd_audit():
    words = json.load(open(os.path.join(I18N, 'en-US.json'), encoding='utf-8'))['words']
    known_gb = set(words)
    known_us = set(words.values())

    protected = load_protected()
    protected_re = (re.compile('|'.join(re.escape(p) for p in
                    sorted(protected, key=len, reverse=True)))
                    if protected else None)

    corpus = collections.Counter()
    where = collections.defaultdict(set)
    for path in sorted(glob.glob(os.path.join(ROOT, '*.html'))):
        text = ' '.join(walk_strings(path))
        # Proper nouns are not spelling choices. Removing them here stops the
        # World Health Organization being reported as American drift forever.
        if protected_re:
            text = protected_re.sub(' ', text)
        for w in re.findall(r"[A-Za-z][A-Za-z'-]+", text):
            lw = w.lower()
            corpus[lw] += 1
            where[lw].add(os.path.basename(path))

    missing = []
    for w, n in corpus.items():
        if w in known_gb or w in NOT_VARIANTS:
            continue
        if any(p.match(w) for p in BRITISH_HINTS):
            missing.append((w, n))

    print('=== British spellings not covered by i18n/en-US.json ===')
    if missing:
        for w, n in sorted(missing, key=lambda x: -x[1]):
            print('  %-24s %3d occurrences   %s' %
                  (w, n, ', '.join(sorted(where[w])[:2])))
        print('\n  Confirm each is a real variant, then add it to en-US.json '
              'or to NOT_VARIANTS in this file.')
    else:
        print('  none')

    print('\n=== American spellings in the en-GB source ===')
    found = [(w, corpus[w]) for w in known_us if corpus.get(w)]
    strays = [(w, n) for w, n in found if w not in known_gb and w not in AMBIGUOUS]
    if strays:
        for w, n in sorted(strays, key=lambda x: -x[1]):
            gb = [k for k, v in words.items() if v == w]
            print('  %-24s %3d occurrences   British form: %s   %s' %
                  (w, n, '/'.join(gb), ', '.join(sorted(where[w])[:2])))
        print('\n  The source is meant to be British throughout. These make the '
              'en-GB reading inconsistent and they survive the en-US transform '
              'untouched, so both locales look sloppy. Resolve in the content.')
    else:
        print('  none')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == 'extract':
        locale = (sys.argv[2] if len(sys.argv) > 2 and
                  not sys.argv[2].startswith('--') else 'es')
        cmd_extract(locale, '--merge' in sys.argv, '--scope' in sys.argv)
    elif cmd == 'audit':
        cmd_audit()
    elif cmd == 'status':
        cmd_status()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
