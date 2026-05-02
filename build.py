#!/usr/bin/env python3
"""
Static-site builder for israeljoffe.com / israeljoffe.org
Reads _data/posts.json + _data/img-map.json
Renders /index.html, /press/, /writing/, /archive/, and /YYYY/MM/DD/slug/index.html
Replaces external image URLs with local /img/ paths.
"""
import os, json, re, sys, html, shutil
from datetime import datetime
from collections import Counter
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_HOST = os.environ.get('SITE_HOST', 'israeljoffe.com')  # override per site
ACCENT = os.environ.get('ACCENT', '#c08a3e')  # gold for .com, override for .org
TAGLINE = os.environ.get('TAGLINE', 'Media Executive · IT Specialist · Firefighter · Animal Lover · FDIC')

# Verified canonical DocumentCloud references (HTTP 200 confirmed 2026-05-01).
CURATED_DC = [
    {'url': 'https://www.documentcloud.org/documents/21956651-document-from-the-lubavitch-rebbe-menachem-shneerson-israel-joffe/',
     'anchor': 'Letter from the Lubavitcher Rebbe'},
    {'url': 'https://www.documentcloud.org/documents/21956628-the-next-detroit_-the-catastrophic-collapse-of-atlantic-city-israel-joffe/',
     'anchor': 'The Next Detroit — Atlantic City'},
    {'url': 'https://www.documentcloud.org/documents/22064733-world-of-unpredictable-wrestling-at-gleasons-gym-israel-joffe/',
     'anchor': 'WUW at Gleason’s Gym'},
    {'url': 'https://www.documentcloud.org/documents/25895701-comgoogleandroidappsphotos/',
     'anchor': 'Promoted to 2nd-degree black belt'},
    {'url': 'https://www.documentcloud.org/documents/22014760-israel-joffe/',
     'anchor': 'Israel Joffe (file)'},
    {'url': 'https://www.documentcloud.org/documents/21952062-israel-joffe/',
     'anchor': 'Israel Joffe (file)'},
    {'url': 'https://www.documentcloud.org/documents/?q=%2Btag%3A%22Israel-joffe%22',
     'anchor': 'All documents tagged Israel-joffe'},
]

all_pages = json.load(open(f'{ROOT}/_data/posts.json'))
img_map = json.load(open(f'{ROOT}/_data/img-map.json'))

# Posts = dated entries, displayed in archive/grid
posts = [p for p in all_pages if p.get('date')]
posts.sort(key=lambda p: p['date'], reverse=True)
# Pages = ALL crawled (used for backlink aggregation, since DC links live in undated pages too)

# WordPress Pages (recovered from REST API) — rendered as standalone pages
try:
    wp_pages = json.load(open(f'{ROOT}/_data/wp-pages.json'))
except FileNotFoundError:
    wp_pages = []
home_page = next((p for p in wp_pages if p.get('is_home')), None)
content_pages = [p for p in wp_pages if not p.get('is_home') and p.get('slug')]

# --- Curated bio + photo pool for the homepage ---
def _strip_html(h):
    h = re.sub(r'<style[^>]*>.*?</style>', '', h or '', flags=re.S | re.I)
    h = re.sub(r'<script[^>]*>.*?</script>', '', h, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', h)).strip()

_about = next((p for p in wp_pages if p.get('slug') == 'about-me'), None) \
      or next((p for p in wp_pages if p.get('slug') == 'about-extra'), None) \
      or next((p for p in wp_pages if p.get('slug') == 'about'), None)

if _about:
    _bio_text = _strip_html(_about['body_html'])
    _bio_paragraphs = [
        "I was born and raised in NYC and spent most of my life there. I served as a firefighter for the Lawrence-Cedarhurst Fire Department during Hurricane Sandy until debilitating injuries forced me to move on. Moved to DC in 2016 and then down to the Palm Beach area in 2021.",
        "I received my black belt in Brazilian Jiu-Jitsu from UFC Hall of Famer Matt Serra and was promoted to 2nd-degree black belt under WWE Hall of Famer Johnny Rodz. For years I trained at World of Unpredictable Wrestling in Brooklyn and helped build and maintain Johnny's websites and social media.",
        "Worked corporate as a Senior Producer and Social Media Manager at Fox 5 News and ABC News Good Morning America, and as a Senior IT Executive at Starwood Hotels and Resorts / Marriott. Featured in Fox 5, Newsweek, Fox 29, NewsBreak, Getty Images, and Monday Night Wrestling.",
    ]
    _all_imgs = re.findall(r'src="(/img/[^"]+)"', _about['body_html'])
    _seen = set(); _photo_pool = []
    for _i in _all_imgs:
        _low = _i.lower()
        if _i in _seen: continue
        if 'cropped' in _low or 'screenshot' in _low or 'og-default' in _low or _low.endswith('.gif'):
            continue
        _seen.add(_i); _photo_pool.append(_i)
else:
    _bio_paragraphs = []
    _photo_pool = []

def rewrite_imgs(body):
    """Replace external image URLs with local /img/ versions."""
    def sub(m):
        url = m.group(1).split('?')[0]
        local = img_map.get(url, m.group(1))
        return f'src="{local}"'
    return re.sub(r'src="(https?://[^"]+\.(?:jpe?g|png|webp|gif))[^"]*"', sub, body, flags=re.I)

def absolute_to_internal(body):
    """Internal absolute links -> relative paths (preserves structure)."""
    body = re.sub(r'href="https?://(?:www\.)?israeljoffe\.(?:com|org)([^"]*)"', r'href="\1"', body)
    return body

def clean_body(body):
    if not body: return ''
    body = rewrite_imgs(body)
    body = absolute_to_internal(body)
    # Strip WP injected scripts and noisy buttons
    body = re.sub(r'<div[^>]*class="[^"]*sd-block[^"]*"[^>]*>.*?</div>', '', body, flags=re.S | re.I)
    body = re.sub(r'<div[^>]*class="[^"]*jp-relatedposts[^"]*"[^>]*>.*?</div>', '', body, flags=re.S | re.I)
    body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.S | re.I)
    return body.strip()

def fmt_date(iso):
    try:
        return datetime.strptime(iso, '%Y-%m-%d').strftime('%B %-d, %Y')
    except: return iso

def post_url(p):
    return '/' + p['url'].split('://')[1].split('/', 1)[1].rstrip('/') + '/'

# --- Aggregate external links across ALL crawled pages (not just dated posts) ---
ext_by_host = {}  # host -> [{url, anchor, source_post_url, source_post_title, date}]
for p in all_pages:
    src = post_url(p) if p.get('date') else (p['url'].replace('https://israeljoffe.com', '').replace('https://israeljoffe.org', '') or '/')
    for L in p.get('external_links', []):
        host = L['host']
        if not host: continue
        ext_by_host.setdefault(host, []).append({
            'url': L['url'], 'anchor': L['anchor'] or host, 'source': src,
            'source_title': p['title'], 'date': p.get('date',''),
        })

# Pick out featured groups
documentcloud = []
substack = []
press = []
PRESS_HOSTS = {
    'fox5ny.com': 'Fox 5 News', 'newsweek.com': 'Newsweek', 'fox29.com': 'Fox 29',
    'original.newsbreak.com': 'NewsBreak', 'newsbreak.com': 'NewsBreak',
    'gettyimages.ca': 'Getty Images', 'gettyimages.com': 'Getty Images',
    'mondaynightwrestling.com': 'Monday Night Wrestling',
    'muckrack.com': 'Muck Rack',
}
def dedupe_keep_best(items):
    """Dedupe by URL-without-querystring, keep entry with longest non-URL anchor."""
    by_key = {}
    for it in items:
        k = it['url'].split('?')[0].rstrip('/')
        prev = by_key.get(k)
        # Prefer entry whose anchor is descriptive text, not just the URL
        cur_anchor_quality = 0 if it['anchor'].startswith('http') else len(it['anchor'])
        prev_anchor_quality = 0 if prev and prev['anchor'].startswith('http') else (len(prev['anchor']) if prev else -1)
        if not prev or cur_anchor_quality > prev_anchor_quality:
            by_key[k] = it
    return list(by_key.values())

for host, items in ext_by_host.items():
    if 'substack' in host:
        substack.extend(dedupe_keep_best(items))
    elif host in PRESS_HOSTS:
        for it in dedupe_keep_best(items):
            it['outlet'] = PRESS_HOSTS[host]
            press.append(it)

# DocumentCloud: use curated canonical list, not harvested.
documentcloud = CURATED_DC
substack = dedupe_keep_best(substack)

# --- Templates ---
PERSON_LD = {
    "@context": "https://schema.org",
    "@type": "Person",
    "name": "Israel Joffe",
    "url": f"https://{SITE_HOST}/",
    "image": f"https://{SITE_HOST}/img/og-default-square.jpg",
    "jobTitle": "Media Executive · IT Specialist · Firefighter",
    "sameAs": [
        "https://x.com/IsraelJoffe3",
        "https://x.com/izzyJoffe",
        "https://www.linkedin.com/in/israeljoffe",
        "https://www.instagram.com/israeljoffe",
        "https://muckrack.com/israel-joffe_",
        "https://israeljoffe.substack.com/",
        "https://www.youtube.com/izzyjoffe",
        "https://medium.com/@israeljoffe",
        "https://www.reddit.com/r/ISRAEL_JOFFE/",
        "https://www.documentcloud.org/documents/?q=%2Btag%3A%22Israel-joffe%22",
        "https://israeljoffe.com/",
        "https://israeljoffe.org/",
    ],
}

def head(title, desc, canonical, og_image=None, og_type='website', published=None, modified=None, post_title=None):
    og = og_image or f'https://{SITE_HOST}/img/og-default.jpg'
    og_sq = f'https://{SITE_HOST}/img/og-default-square.jpg'
    is_article = og_type == 'article'
    ld_blocks = [PERSON_LD]
    if is_article and published:
        ld_blocks.append({
            "@context": "https://schema.org",
            "@type": "BlogPosting",
            "headline": post_title or title,
            "datePublished": published,
            "dateModified": modified or published,
            "url": canonical,
            "image": og,
            "author": {"@type": "Person", "name": "Israel Joffe", "url": f"https://{SITE_HOST}/"},
            "publisher": {"@type": "Person", "name": "Israel Joffe", "url": f"https://{SITE_HOST}/"},
            "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        })
    else:
        ld_blocks.append({
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "Israel Joffe",
            "url": f"https://{SITE_HOST}/",
            "potentialAction": {
                "@type": "SearchAction",
                "target": f"https://{SITE_HOST}/archive/?q={{search_term_string}}",
                "query-input": "required name=search_term_string",
            },
        })
    ld_json = '\n'.join(f'<script type="application/ld+json">{json.dumps(b, separators=(",", ":"))}</script>' for b in ld_blocks)
    article_metas = ''
    if is_article:
        article_metas = (
            (f'<meta property="article:published_time" content="{published}T00:00:00Z" />\n' if published else '')
            + (f'<meta property="article:modified_time" content="{modified}T00:00:00Z" />\n' if modified else '')
            + '<meta property="article:author" content="Israel Joffe" />\n'
            + '<meta property="article:section" content="Personal" />\n'
        )
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}" />
<meta name="author" content="Israel Joffe" />
<meta name="theme-color" content="#0e0e0e" />
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1" />
<link rel="canonical" href="{canonical}" />
<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<meta property="og:type" content="{og_type}" />
<meta property="og:title" content="{html.escape(title)}" />
<meta property="og:description" content="{html.escape(desc)}" />
<meta property="og:url" content="{canonical}" />
<meta property="og:site_name" content="Israel Joffe" />
<meta property="og:locale" content="en_US" />
<meta property="og:image" content="{og}" />
<meta property="og:image:secure_url" content="{og}" />
<meta property="og:image:type" content="image/jpeg" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:image:alt" content="Israel Joffe — {html.escape(TAGLINE)}" />
<meta property="og:image" content="{og_sq}" />
<meta property="og:image:type" content="image/jpeg" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="1200" />
<meta property="og:image:alt" content="Israel Joffe (square)" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{html.escape(title)}" />
<meta name="twitter:description" content="{html.escape(desc)}" />
<meta name="twitter:image" content="{og}" />
<meta name="twitter:image:alt" content="Israel Joffe — {html.escape(TAGLINE)}" />
<meta name="twitter:site" content="@IsraelJoffe3" />
<meta name="twitter:creator" content="@IsraelJoffe3" />
{article_metas}<meta name="apple-mobile-web-app-title" content="Israel Joffe" />
<meta name="application-name" content="Israel Joffe" />
<meta name="format-detection" content="telephone=no" />
<link rel="alternate" type="application/rss+xml" title="Israel Joffe — RSS" href="/feed.xml" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet" />
<link rel="stylesheet" href="/styles.css?v=BUILD" />
{ld_json}
</head>
<body>
<div class="grain" aria-hidden="true"></div>
<header class="masthead" id="top">
  <a class="wordmark" href="/" aria-label="Israel Joffe — home">
    <span class="wm-italic">Israel</span><span class="wm-roman">Joffe</span>
  </a>
  <nav class="primary" aria-label="Primary">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/photos/">Photos</a>
    <a href="/bjj/">BJJ</a>
    <a href="/writing/">Writing</a>
    <a href="/press/">Press</a>
    <a href="/archive/">Archive</a>
    <a href="/contact/">Contact</a>
  </nav>
  <button class="menu-toggle" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="mobile-nav">
    <span></span><span></span><span></span>
  </button>
</header>
<div class="mobile-nav" id="mobile-nav" aria-hidden="true">
  <nav class="mobile-nav-inner" aria-label="Mobile primary">
    <a href="/">Home</a>
    <a href="/about/">About</a>
    <a href="/photos/">Photos</a>
    <a href="/bjj/">BJJ</a>
    <a href="/writing/">Writing</a>
    <a href="/press/">Press</a>
    <a href="/archive/">Archive</a>
    <a href="/contact/">Contact</a>
  </nav>
</div>
'''

def footer():
    # Quiet backlinks footer — DocumentCloud + extra references for SEO
    dc_links = ''.join(f'<a href="{html.escape(it["url"])}" rel="noopener" target="_blank">{html.escape(it["anchor"][:32]) or "ref"}</a>' for it in documentcloud)
    return f'''
<footer class="colophon">
  <div class="colophon-frame">
    <div class="colophon-brand">
      <span class="colophon-mark"><em>Israel</em>Joffe</span>
      <span class="colophon-tag">{html.escape(TAGLINE)}</span>
    </div>
    <div class="colophon-meta">
      <p><a href="https://twitter.com/israeljoffe" target="_blank" rel="noopener me">Twitter</a></p>
      <p><a href="https://www.linkedin.com/in/israeljoffe" target="_blank" rel="noopener me">LinkedIn</a></p>
      <p><a href="https://www.instagram.com/israeljoffe" target="_blank" rel="noopener me">Instagram</a></p>
      <p><a href="https://muckrack.com/israel-joffe_" target="_blank" rel="noopener me">Muck Rack</a></p>
      <p><a href="https://israeljoffe.substack.com" target="_blank" rel="noopener me">Substack</a></p>
    </div>
    <div class="colophon-fine">
      <p>© <span id="year">2026</span> Israel Joffe. All rights reserved.</p>
    </div>
  </div>
  <nav class="bg-refs" aria-label="Referenced documents">
    {dc_links}
  </nav>
</footer>
<script>document.getElementById("year").textContent=new Date().getFullYear();
(function(){{const t=document.querySelector(".menu-toggle"),d=document.getElementById("mobile-nav"),b=document.body;if(!t||!d)return;t.addEventListener("click",()=>{{const o=b.classList.toggle("menu-open");t.setAttribute("aria-expanded",o);d.setAttribute("aria-hidden",!o);}});document.querySelectorAll('.mobile-nav a').forEach(a=>a.addEventListener("click",()=>b.classList.remove("menu-open")));}})();
(function(){{var els=document.querySelectorAll('.reveal,.hero,.press-strip,.recent,.section-head,.grid-card,.ct-card,.pr-outlet,.ar-year,.wpp-head,.wpp-body,.bio-snip,.photo-strip,.ps-tile');els.forEach(function(e){{e.classList.add('reveal')}});if(!('IntersectionObserver' in window)){{els.forEach(function(e){{e.classList.add('is-visible')}});return}}var io=new IntersectionObserver(function(es){{es.forEach(function(en){{if(en.isIntersecting){{en.target.classList.add('is-visible');io.unobserve(en.target)}}}})}},{{rootMargin:'0px 0px -8% 0px',threshold:0.05}});els.forEach(function(e){{io.observe(e)}});}})();
</script>
</body></html>'''

def render_post(p):
    body = clean_body(p['body_html'])
    hero = p.get('hero')
    if hero:
        local = img_map.get(hero.split('?')[0], hero)
        og = f'https://{SITE_HOST}{local}'
    else:
        og = None
    canonical = f'https://{SITE_HOST}{post_url(p)}'
    desc = (p.get('description') or html.unescape(re.sub(r'<[^>]+>', ' ', body)).strip())[:200] or p['title']
    h = head(p['title'] + ' · Israel Joffe', desc, canonical, og,
             og_type='article', published=p.get('date'), modified=p.get('date'), post_title=p['title'])
    article = f'''
<main class="post-page">
  <article class="post">
    <header class="post-head">
      <p class="post-date">{fmt_date(p['date'])}</p>
      <h1 class="post-title">{html.escape(p['title'])}</h1>
    </header>
    <div class="post-body">{body}</div>
    <footer class="post-foot">
      <p><a href="/" class="link-back">← Home</a> · <a href="/archive/" class="link-back">Archive</a></p>
    </footer>
  </article>
</main>
'''
    return h + article + footer()

def render_index():
    recent = posts[:8]
    canonical = f'https://{SITE_HOST}/'
    desc = ('Israel Joffe — New York media executive, senior IT specialist, '
            "and former Lawrence-Cedarhurst firefighter. Brazilian Jiu-Jitsu 2nd-degree black belt under Johnny Rodz. "
            'Featured in Fox 5, Newsweek, Fox 29, NewsBreak, and Getty Images. '
            f'{len(posts)} posts since 2020 — writing, press, public service.')
    h = head('Israel Joffe — ' + TAGLINE, desc, canonical, f'https://{SITE_HOST}/img/og-default.jpg')
    cards = ''
    for p in recent:
        hero = img_map.get((p.get('hero') or '').split('?')[0], p.get('hero') or '')
        cards += f'''
    <a class="grid-card" href="{post_url(p)}">
      <div class="gc-img">{f'<img src="{html.escape(hero)}" alt="Israel Joffe" loading="lazy" />' if hero else ''}</div>
      <div class="gc-meta">
        <p class="gc-date">{fmt_date(p['date'])}</p>
        <h3 class="gc-title">{html.escape(p['title'])}</h3>
      </div>
    </a>'''
    bio_html = ''
    if _bio_paragraphs and _photo_pool:
        portrait = _photo_pool[0]
        bio_paras = ''.join(f'<p>{html.escape(t)}</p>' for t in _bio_paragraphs)
        bio_html = f'''
  <section class="bio-snip reveal" id="bio">
    <div class="bs-frame">
      <figure class="bs-portrait"><img src="{html.escape(portrait)}" alt="Israel Joffe" loading="lazy" /></figure>
      <div class="bs-body">
        <p class="eyebrow">About</p>
        <h2 class="bs-title">Israel Joffe.</h2>
        <div class="bs-prose">{bio_paras}</div>
        <p class="bs-cta"><a class="link-cta" href="/about/">Read the full bio →</a></p>
      </div>
    </div>
  </section>'''
    photos_html = ''
    if _photo_pool:
        gallery = _photo_pool[1:13]
        tiles = ''.join(
            f'<a class="ps-tile" href="/photos/"><img src="{html.escape(src)}" alt="Israel Joffe" loading="lazy" /></a>'
            for src in gallery
        )
        photos_html = f'''
  <section class="photo-strip reveal" id="photos">
    <header class="section-head"><h2 class="section-title">Photos</h2><a class="section-more" href="/photos/">All photos →</a></header>
    <div class="ps-grid">{tiles}</div>
  </section>'''
    main = f'''
<main class="home">
  <section class="hero">
    <picture class="hero-image"><img src="/img/hero.jpg" alt="Israel Joffe" loading="eager" /></picture>
    <div class="hero-veil"></div>
    <div class="hero-frame">
      <p class="hero-eyebrow"><span class="rule"></span><span>{html.escape(TAGLINE)}</span></p>
      <h1 class="hero-title">
        <span class="hl-line"><span class="hl-word">Israel</span></span>
        <span class="hl-line"><span class="hl-word hl-em">Joffe.</span></span>
      </h1>
      <p class="hero-sub">A media executive, IT specialist, firefighter, and writer based in New York.<br>
      Featured in Fox 5, Newsweek, Fox 29, NewsBreak.</p>
      <div class="hero-cta-row">
        <a class="link-cta on-image" href="/writing/"><span>Read the writing</span></a>
        <a class="link-fine on-image" href="/press/"><span class="dot"></span>Press &amp; mentions</a>
      </div>
    </div>
  </section>
  <section class="press-strip" aria-label="As featured in">
    <p class="ps-eyebrow">As featured in</p>
    <div class="ps-row">
      <a href="/press/#fox5ny.com">Fox 5 News</a>
      <a href="/press/#newsweek.com">Newsweek</a>
      <a href="/press/#fox29.com">Fox 29</a>
      <a href="/press/#newsbreak.com">NewsBreak</a>
      <a href="/press/#mondaynightwrestling.com">Monday Night Wrestling</a>
      <a href="/press/#muckrack.com">Muck Rack</a>
    </div>
  </section>
  {bio_html}
  {photos_html}
  <section class="recent">
    <header class="section-head"><h2 class="section-title">Recent writing</h2><a class="section-more" href="/archive/">All {len(posts)} posts →</a></header>
    <div class="grid">{cards}
    </div>
  </section>
</main>
'''
    return h + main + footer()

def render_wp_page(p):
    """Render a recovered WordPress Page as a standalone route."""
    canonical = f'https://{SITE_HOST}/{p["slug"]}/'
    desc_text = _strip_html(p['body_html'])
    SITE_DESC = ('New York media executive, senior IT specialist, and former Lawrence-Cedarhurst firefighter. '
                 'BJJ 2nd-degree black belt under Johnny Rodz. Featured in Fox 5, Newsweek, Fox 29, NewsBreak, Getty Images.')
    if len(desc_text) < 80:
        desc = f'Israel Joffe — {SITE_DESC}'
    else:
        desc = f'Israel Joffe — {desc_text[:240]}'
    page_title = p['title'].strip() or 'Israel Joffe'
    if page_title.lower() == 'israel joffe':
        title_tag = f'Israel Joffe — {p["slug"].replace("-", " ").title()}'
    else:
        title_tag = f'{page_title} · Israel Joffe'
    h = head(title_tag, desc, canonical)
    main = f'''<main class="wp-page reveal"><div class="wpp-frame">
  <header class="wpp-head">
    <p class="eyebrow">Israel Joffe</p>
    <h1 class="wpp-title">{html.escape(p["title"])}</h1>
  </header>
  <div class="wpp-body">{p["body_html"]}</div>
  <footer class="wpp-foot"><a class="link-back" href="/">← Home</a></footer>
</div></main>'''
    return h + main + footer()

def render_archive():
    canonical = f'https://{SITE_HOST}/archive/'
    h = head('Archive · Israel Joffe',
             f'Six years of writing — politics, technology, public service, BJJ, the FDIC, and life in New York. {len(posts)} posts since 2020.',
             canonical)
    by_year = {}
    for p in posts: by_year.setdefault(p['date'][:4], []).append(p)
    yrs = ''
    for yr in sorted(by_year, reverse=True):
        items = ''.join(f'<li><a href="{post_url(p)}"><span class="ar-d">{fmt_date(p["date"])}</span><span class="ar-t">{html.escape(p["title"])}</span></a></li>' for p in by_year[yr])
        yrs += f'<section class="ar-year"><h2 class="ar-h">{yr}</h2><ol class="ar-list">{items}</ol></section>'
    main = f'<main class="archive-page"><div class="page-frame"><header class="section-head"><h1 class="section-title">Archive</h1><p>{len(posts)} posts since 2020.</p></header>{yrs}</div></main>'
    return h + main + footer()

def render_press():
    canonical = f'https://{SITE_HOST}/press/'
    h = head('Press & Mentions · Israel Joffe', 'Press coverage of Israel Joffe in Fox 5 News, Newsweek, Fox 29, NewsBreak, Monday Night Wrestling, Getty Images, and more.', canonical)
    by_outlet = {}
    for it in press: by_outlet.setdefault(it['outlet'], []).append(it)
    sections = ''
    for outlet, items in sorted(by_outlet.items(), key=lambda x: -len(x[1])):
        host = next((h for h, name in PRESS_HOSTS.items() if name == outlet), '')
        rows = ''.join(f'<li><a href="{html.escape(it["url"])}" target="_blank" rel="noopener">{html.escape(it["anchor"]) or outlet}</a><span class="pr-src">via <a href="{it["source"]}">{html.escape(it["source_title"])}</a></span></li>' for it in items)
        sections += f'<section class="pr-outlet" id="{host}"><h2>{outlet}</h2><ol class="pr-list">{rows}</ol></section>'
    main = f'<main class="press-page"><div class="page-frame"><header class="section-head"><p class="eyebrow">As featured in</p><h1 class="section-title">Press &amp; Mentions</h1><p>{len(press)} citations across {len(by_outlet)} outlets.</p></header>{sections}</div></main>'
    return h + main + footer()

def render_writing():
    canonical = f'https://{SITE_HOST}/writing/'
    h = head('Writing · Israel Joffe', 'Substack and long-form writing by Israel Joffe.', canonical)
    sb_rows = ''.join(f'<li><a href="{html.escape(it["url"])}" target="_blank" rel="noopener">{html.escape(it["anchor"]) or "Substack post"}</a><span class="pr-src">via <a href="{it["source"]}">{html.escape(it["source_title"])}</a></span></li>' for it in substack)
    main = f'''<main class="press-page">
<div class="page-frame">
  <header class="section-head">
    <p class="eyebrow">Writing</p>
    <h1 class="section-title">Writing</h1>
    <p>Long-form on Substack.</p>
  </header>
  <section class="pr-outlet" id="substack"><h2>Substack</h2><ol class="pr-list">{sb_rows}</ol></section>
</div>
</main>'''
    return h + main + footer()

def render_contact():
    canonical = f'https://{SITE_HOST}/contact/'
    h = head('Contact · Israel Joffe', 'Reach Israel Joffe directly — email, LinkedIn, X, Substack, and Muck Rack press inbox.', canonical)
    main = f'''<main class="contact-page">
<div class="page-frame contact-frame">
  <header class="ct-head">
    <p class="eyebrow">Contact</p>
    <h1 class="section-title">Get in touch</h1>
    <p class="ct-lede">For press inquiries, speaking, or collaboration — pick the channel that suits you. I read every message.</p>
  </header>
  <div class="ct-grid">
    <a class="ct-card" href="mailto:israeljoffe@gmail.com">
      <p class="ct-h">Email</p>
      <p class="ct-big">israeljoffe@gmail.com</p>
      <p class="ct-d">Best for longer notes, press, partnerships.</p>
    </a>
    <a class="ct-card" href="https://muckrack.com/israel-joffe_" target="_blank" rel="noopener">
      <p class="ct-h">Press · Muck Rack</p>
      <p class="ct-big">muckrack.com/israel-joffe_</p>
      <p class="ct-d">Verified press profile — pitch, fact-check, source requests.</p>
    </a>
    <a class="ct-card" href="https://www.linkedin.com/in/israeljoffe" target="_blank" rel="noopener">
      <p class="ct-h">LinkedIn</p>
      <p class="ct-big">linkedin.com/in/israeljoffe</p>
      <p class="ct-d">Professional connection, work conversations.</p>
    </a>
    <a class="ct-card" href="https://x.com/IsraelJoffe3" target="_blank" rel="noopener">
      <p class="ct-h">X · DMs open</p>
      <p class="ct-big">@IsraelJoffe3</p>
      <p class="ct-d">Quick public conversation, hot takes, links.</p>
    </a>
    <a class="ct-card" href="https://israeljoffe.substack.com/" target="_blank" rel="noopener">
      <p class="ct-h">Substack</p>
      <p class="ct-big">israeljoffe.substack.com</p>
      <p class="ct-d">Subscribe to long-form essays.</p>
    </a>
    <a class="ct-card" href="https://www.instagram.com/israeljoffe" target="_blank" rel="noopener">
      <p class="ct-h">Instagram</p>
      <p class="ct-big">@israeljoffe</p>
      <p class="ct-d">Photos · BJJ · travel.</p>
    </a>
  </div>
</div>
</main>'''
    return h + main + footer()

def render_about():
    canonical = f'https://{SITE_HOST}/about/'
    h = head('About · Israel Joffe', 'Israel Joffe — Media Executive, IT Specialist, Firefighter, BJJ practitioner, and writer based in New York.', canonical)
    main = f'''<main class="about-page">
<div class="page-frame about-frame">
  <div class="ap-photo"><img src="/img/about.jpg" alt="Israel Joffe" loading="lazy" /></div>
  <div class="ap-body">
    <p class="eyebrow">About</p>
    <h1 class="section-title">Israel Joffe</h1>
    <p class="ap-lede"><em>Media Executive · Senior IT Specialist · Firefighter · World traveler · Writer.</em></p>
    <p>Israel Joffe is a New York–based media executive and IT specialist whose work has appeared in Fox 5, Newsweek, Fox 29, NewsBreak, and across the Monday Night Wrestling and World Of Unpredictable Wrestling networks. He writes on documents archived at DocumentCloud and on Substack.</p>
    <p>Outside the desk: Brazilian Jiu-Jitsu practitioner, fitness obsessive, traveler, dog person.</p>
  </div>
</div>
</main>'''
    return h + main + footer()

def render_styles():
    accent = ACCENT
    return ('''
:root {
  --bone:#efebe3; --bone-soft:#e7e1d4; --bone-warm:#ddd5c2; --rule:#d9d2c4;
  --ink:#0e0e0e; --ink-soft:#2a2925; --ink-mute:#6f6a5e; --ink-faint:#a8a193;
  --accent:''' + accent + ''';
  --display:"Instrument Serif", Georgia, serif;
  --sans:"Inter", -apple-system, system-ui, sans-serif;
  --frame-pad:clamp(20px,5vw,64px); --max:1240px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bone);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.65;-webkit-font-smoothing:antialiased;overflow-x:hidden}
img{max-width:100%;display:block}
a{color:inherit;text-decoration:none}
ul,ol{list-style:none}
.grain{position:fixed;inset:0;pointer-events:none;z-index:100;opacity:.05;mix-blend-mode:multiply;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.6 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>")}

/* Masthead */
.masthead{position:fixed;top:0;left:0;right:0;display:flex;align-items:center;justify-content:space-between;padding:14px var(--frame-pad);z-index:50;color:var(--bone);background:rgba(14,14,14,.92);border-bottom:1px solid rgba(239,235,227,.08);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
.wordmark{display:inline-flex;align-items:baseline;gap:0;font-family:var(--display);font-size:clamp(22px,2.4vw,30px);line-height:1;color:var(--bone)}
.wm-italic{font-style:italic;color:var(--accent);margin-right:.04em}
.wm-roman{color:var(--bone)}
.primary{display:flex;gap:clamp(14px,2vw,28px);font-size:11px;letter-spacing:.2em;text-transform:uppercase}
.primary a{color:#efebe3;padding:6px 0;position:relative;transition:color .25s}
.primary a:hover{color:var(--accent)}
.primary a::after{content:"";position:absolute;left:0;right:0;bottom:-2px;height:1px;background:currentColor;transform-origin:left;transform:scaleX(0);transition:transform .4s cubic-bezier(.2,.7,.2,1)}
.primary a:hover::after{transform:scaleX(1)}
.menu-toggle{display:none;width:48px;height:48px;background:transparent;border:1px solid rgba(239,235,227,.5);border-radius:999px;align-items:center;justify-content:center;flex-direction:column;gap:6px;cursor:pointer;padding:0}
.menu-toggle span{display:block;width:22px;height:1.5px;background:var(--bone);border-radius:2px;transition:transform .3s}
.menu-open .menu-toggle span:nth-child(1){transform:translateY(7.5px) rotate(45deg)}
.menu-open .menu-toggle span:nth-child(2){opacity:0}
.menu-open .menu-toggle span:nth-child(3){transform:translateY(-7.5px) rotate(-45deg)}
.mobile-nav{position:fixed;top:0;left:0;right:0;height:100vh;height:100dvh;z-index:49;background:rgba(14,14,14,.97);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);opacity:0;visibility:hidden;transition:opacity .32s,visibility .32s;overflow-y:auto}
.menu-open .mobile-nav{opacity:1;visibility:visible}
.menu-open{overflow:hidden}
.mobile-nav-inner{display:flex;flex-direction:column;padding:calc(env(safe-area-inset-top,0) + 88px) 28px 40px;width:100%;max-width:460px;margin:0 auto}
.mobile-nav a{padding:18px 0;font-family:var(--display);font-size:28px;color:var(--bone);border-bottom:1px solid rgba(239,235,227,.1)}
.mobile-nav a:hover{color:var(--accent)}

/* Hero */
.hero{position:relative;min-height:clamp(560px,84vh,760px);display:flex;align-items:center;background:var(--ink);overflow:hidden;isolation:isolate;text-align:center;color:var(--bone)}
.hero-image{position:absolute;inset:0;z-index:0}
.hero-image img{width:100%;height:100%;object-fit:cover;object-position:center 25%;filter:saturate(.85) contrast(1.05);animation:zoom 22s ease-in-out infinite alternate}
@keyframes zoom{from{transform:scale(1.02)}to{transform:scale(1.12) translate(-1.5%,-1%)}}
.hero-veil{position:absolute;inset:0;z-index:1;background:radial-gradient(ellipse at 50% 50%,rgba(14,14,14,.35),rgba(14,14,14,.7) 70%,rgba(14,14,14,.92)),linear-gradient(180deg,rgba(14,14,14,.4),rgba(14,14,14,.85))}
.hero-frame{position:relative;z-index:2;max-width:1080px;margin:0 auto;padding:clamp(96px,14vh,140px) var(--frame-pad)}
.hero-eyebrow{display:inline-flex;align-items:center;gap:14px;font-size:11px;letter-spacing:.32em;text-transform:uppercase;background:rgba(14,14,14,.55);padding:8px 18px;border:1px solid rgba(239,235,227,.18);border-radius:999px;margin-bottom:36px;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
.hero-eyebrow .rule{display:inline-block;width:28px;height:1px;background:var(--accent)}
.hero-title{font-family:var(--display);font-weight:400;font-size:clamp(56px,11vw,140px);line-height:.96;letter-spacing:-.022em;margin-bottom:28px;text-shadow:0 2px 24px rgba(0,0,0,.6)}
.hl-line{display:block;overflow:hidden}
.hl-word{display:inline-block;animation:rise 1s cubic-bezier(.2,.7,.2,1) forwards;opacity:0;transform:translateY(110%)}
.hl-line:nth-child(1) .hl-word{animation-delay:.2s}
.hl-line:nth-child(2) .hl-word{animation-delay:.4s}
.hl-em{font-style:italic;color:var(--accent)}
@keyframes rise{to{opacity:1;transform:translateY(0)}}
.hero-sub{font-family:var(--display);font-style:italic;font-size:clamp(18px,1.9vw,26px);line-height:1.5;color:rgba(239,235,227,.95);max-width:52ch;margin:0 auto 28px;text-shadow:0 1px 8px rgba(0,0,0,.55)}
.hero-cta-row{display:flex;justify-content:center;gap:24px;flex-wrap:wrap;margin-top:24px}
.link-cta{display:inline-flex;align-items:center;gap:14px;padding:10px 0;font-size:12px;letter-spacing:.22em;text-transform:uppercase;border-bottom:1px solid currentColor;transition:gap .4s,color .3s}
.link-cta:hover{gap:20px;color:var(--accent)}
.link-cta.on-image{color:var(--bone);border-color:var(--bone)}
.link-fine{display:inline-flex;align-items:center;gap:10px;font-family:var(--display);font-size:17px;font-style:italic;color:rgba(239,235,227,.9)}
.link-fine.on-image .dot{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--accent)}
.link-back{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink-mute);border-bottom:1px solid currentColor;padding-bottom:2px}

/* Press strip */
.press-strip{padding:clamp(40px,6vh,72px) var(--frame-pad);text-align:center;background:var(--bone)}
.ps-eyebrow{font-size:11px;letter-spacing:.28em;text-transform:uppercase;color:var(--accent);margin-bottom:18px}
.ps-row{display:flex;flex-wrap:wrap;justify-content:center;gap:20px 36px;font-family:var(--display);font-size:clamp(20px,2.4vw,30px)}
.ps-row a{color:var(--ink);transition:color .25s;border-bottom:1px solid transparent}
.ps-row a:hover{color:var(--accent);border-color:var(--accent)}

/* Recent grid */
.recent{padding:clamp(64px,9vh,100px) var(--frame-pad) clamp(80px,12vh,140px);max-width:var(--max);margin:0 auto}
.section-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:36px;padding-bottom:20px;border-bottom:1px solid var(--rule);flex-wrap:wrap;gap:12px}
.section-title{font-family:var(--display);font-weight:400;font-size:clamp(34px,5.4vw,64px);line-height:1;letter-spacing:-.012em;color:var(--ink)}
.section-more{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink-mute);border-bottom:1px solid currentColor;padding-bottom:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:clamp(20px,2.5vw,36px)}
.grid-card{background:var(--bone-soft);overflow:hidden;border-radius:4px;transition:transform .4s,box-shadow .4s}
.grid-card:hover{transform:translateY(-3px);box-shadow:0 18px 40px -24px rgba(14,14,14,.35)}
.gc-img{aspect-ratio:4/3;overflow:hidden;background:var(--ink)}
.gc-img img{width:100%;height:100%;object-fit:cover;transition:transform 1.2s}
.grid-card:hover .gc-img img{transform:scale(1.05)}
.gc-meta{padding:18px 22px 24px}
.gc-date{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);margin-bottom:8px}
.gc-title{font-family:var(--display);font-weight:400;font-size:clamp(20px,2vw,26px);line-height:1.15;color:var(--ink)}

/* Page frames */
.page-frame{max-width:920px;margin:0 auto;padding:clamp(112px,16vh,160px) var(--frame-pad) clamp(72px,10vh,120px)}
.page-frame .section-head{flex-direction:column;align-items:flex-start;border-bottom-color:var(--rule)}
.eyebrow{font-size:11px;letter-spacing:.28em;text-transform:uppercase;color:var(--accent)}

/* Post page */
.post-page{padding:clamp(100px,14vh,140px) var(--frame-pad) clamp(80px,12vh,140px);max-width:760px;margin:0 auto}
.post-head{margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid var(--rule)}
.post-date{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);margin-bottom:12px}
.post-title{font-family:var(--display);font-weight:400;font-size:clamp(36px,5.6vw,64px);line-height:1.05;letter-spacing:-.015em;color:var(--ink)}
.post-body{font-size:18px;line-height:1.75;color:var(--ink-soft)}
.post-body p{margin-bottom:1.2em}
.post-body img{margin:32px 0;border-radius:4px;width:100%;height:auto}
.post-body a{color:var(--accent);border-bottom:1px solid currentColor;transition:opacity .2s}
.post-body a:hover{opacity:.8}
.post-body h2,.post-body h3{font-family:var(--display);font-weight:400;color:var(--ink);margin:1.5em 0 .5em;line-height:1.2}
.post-body h2{font-size:32px}
.post-body h3{font-size:24px}
.post-body blockquote{border-left:2px solid var(--accent);padding-left:24px;margin:24px 0;font-style:italic;color:var(--ink)}
.post-foot{margin-top:48px;padding-top:24px;border-top:1px solid var(--rule)}

/* Archive */
.archive-page .ar-year{margin-bottom:48px}
.ar-h{font-family:var(--display);font-weight:400;font-size:clamp(36px,5vw,52px);color:var(--accent);margin-bottom:16px;line-height:1}
.ar-list li{border-bottom:1px solid var(--rule)}
.ar-list a{display:flex;justify-content:space-between;align-items:baseline;gap:24px;padding:14px 0;transition:padding-left .3s}
.ar-list a:hover{padding-left:8px;color:var(--accent)}
.ar-d{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink-mute);min-width:140px}
.ar-t{font-family:var(--display);font-size:clamp(17px,1.7vw,22px);text-align:right;flex:1}

/* Press / Writing */
.press-page .pr-outlet{margin-bottom:48px}
.pr-outlet h2{font-family:var(--display);font-weight:400;font-size:clamp(28px,3.4vw,40px);color:var(--ink);margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--rule)}
.pr-list li{padding:12px 0;border-bottom:1px solid var(--rule);display:flex;flex-direction:column;gap:4px}
.pr-list a{color:var(--ink);font-family:var(--display);font-size:18px;border-bottom:1px solid transparent;transition:border-color .25s}
.pr-list a:hover{border-color:var(--accent);color:var(--accent)}
.pr-src{font-size:12px;color:var(--ink-mute)}
.pr-src a{font-size:12px;font-family:var(--sans);color:var(--ink-mute);border-bottom:1px dashed transparent}
.pr-src a:hover{border-color:var(--ink-mute)}

/* About */
.about-frame{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:clamp(40px,6vw,80px);align-items:center}
.ap-photo{aspect-ratio:4/5;overflow:hidden;border-radius:4px;background:var(--ink)}
.ap-photo img{width:100%;height:100%;object-fit:cover;object-position:center top}
.ap-lede{font-family:var(--display);font-size:clamp(22px,2.6vw,30px);font-style:italic;line-height:1.35;color:var(--ink);margin:18px 0 24px}
.ap-body p{font-size:17px;line-height:1.7;color:var(--ink-soft);margin-bottom:18px;max-width:56ch}

/* WordPress Pages — recovered content */
.wp-page{padding:clamp(120px,16vh,180px) var(--frame-pad) clamp(80px,12vh,120px)}
.wpp-frame{max-width:980px;margin:0 auto}
.wpp-head{margin-bottom:48px;text-align:center;padding-bottom:32px;border-bottom:1px solid var(--rule)}
.wpp-head .eyebrow{font-size:11px;letter-spacing:.32em;text-transform:uppercase;color:var(--accent);font-weight:600;margin-bottom:14px}
.wpp-title{font-family:var(--display);font-size:clamp(40px,7vw,82px);line-height:1.05;letter-spacing:-.02em;color:var(--ink)}
.wpp-body{font-size:17px;line-height:1.75;color:var(--ink-soft)}
.wpp-body p{margin-bottom:22px;max-width:64ch}
.wpp-body h1,.wpp-body h2,.wpp-body h3{font-family:var(--display);color:var(--ink);margin:48px 0 18px;letter-spacing:-.01em;line-height:1.15}
.wpp-body h2{font-size:clamp(28px,3.6vw,40px)}
.wpp-body h3{font-size:clamp(22px,2.8vw,30px)}
.wpp-body img{max-width:100%;height:auto;margin:32px auto;display:block;border-radius:4px}
.wpp-body a{color:var(--accent);border-bottom:1px solid currentColor;transition:opacity .2s}
.wpp-body a:hover{opacity:.7}
.wpp-body figure{margin:32px 0}
.wpp-body figcaption{font-size:13px;color:var(--ink-mute);text-align:center;margin-top:8px;font-style:italic}
.wpp-body .wp-block-gallery,.wpp-body .gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin:32px 0}
.wpp-body .wp-block-gallery img,.wpp-body .gallery img{margin:0;aspect-ratio:1;object-fit:cover}
.wpp-body iframe,.wpp-body video{max-width:100%;margin:32px auto;display:block}
.wpp-foot{margin-top:64px;padding-top:32px;border-top:1px solid var(--rule)}
/* Bio snip on homepage */
.bio-snip{padding:clamp(72px,10vh,120px) var(--frame-pad);background:var(--bone-soft);border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.bs-frame{max-width:var(--max);margin:0 auto;display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:clamp(40px,6vw,80px);align-items:center}
.bs-portrait{aspect-ratio:4/5;overflow:hidden;border-radius:4px;background:var(--ink);box-shadow:0 24px 60px -32px rgba(14,14,14,.45)}
.bs-portrait img{width:100%;height:100%;object-fit:cover;object-position:center top;transition:transform 1.4s}
.bs-portrait:hover img{transform:scale(1.03)}
.bs-body .eyebrow{margin-bottom:14px}
.bs-title{font-family:var(--display);font-weight:400;font-size:clamp(40px,5.4vw,68px);line-height:1;letter-spacing:-.018em;color:var(--ink);margin-bottom:24px}
.bs-prose p{font-size:17px;line-height:1.75;color:var(--ink-soft);margin-bottom:18px;max-width:60ch}
.bs-cta{margin-top:24px}
.bs-cta a{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);border-bottom:1px solid currentColor;padding-bottom:2px}
@media (max-width:780px){.bs-frame{grid-template-columns:1fr}.bs-portrait{aspect-ratio:4/5;max-width:420px;margin:0 auto}}

/* Photo strip on homepage */
.photo-strip{padding:clamp(64px,9vh,100px) var(--frame-pad) clamp(40px,6vh,72px);max-width:var(--max);margin:0 auto}
.ps-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:clamp(10px,1.4vw,18px)}
.ps-tile{aspect-ratio:1;overflow:hidden;background:var(--ink);border-radius:3px;display:block}
.ps-tile img{width:100%;height:100%;object-fit:cover;transition:transform 1.4s,filter .4s;filter:saturate(.92)}
.ps-tile:hover img{transform:scale(1.06);filter:saturate(1.05)}
@media (max-width:900px){.ps-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:560px){.ps-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}

/* Keti-style reveal-on-scroll entrance */
.reveal{opacity:0;transform:translateY(14px);transition:opacity 1.1s cubic-bezier(.2,.7,.2,1),transform 1.1s cubic-bezier(.2,.7,.2,1)}
.reveal.is-visible{opacity:1;transform:translateY(0)}
.hero,.press-strip,.recent,.section-head,.grid-card,.ct-card,.pr-outlet,.ar-year,.wpp-head,.wpp-body,.bio-snip,.photo-strip,.ps-tile{will-change:opacity,transform}
@media (prefers-reduced-motion:reduce){.reveal,.reveal.is-visible{opacity:1;transform:none;transition:none}}

/* Contact */
.contact-page{padding:clamp(120px,16vh,180px) var(--frame-pad) clamp(80px,12vh,120px)}
.contact-frame{max-width:1080px;margin:0 auto}
.ct-head{margin-bottom:48px;text-align:center}
.ct-head .eyebrow{font-size:11px;letter-spacing:.28em;text-transform:uppercase;color:var(--accent);margin-bottom:12px;font-weight:600}
.ct-lede{font-family:var(--display);font-size:clamp(20px,2.2vw,24px);font-style:italic;line-height:1.45;color:var(--ink-soft);max-width:62ch;margin:18px auto 0}
.ct-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.ct-card{display:block;padding:28px 26px;background:var(--bone-soft);border:1px solid var(--rule);border-radius:6px;transition:transform .35s ease,box-shadow .35s ease,border-color .35s,background .35s;color:var(--ink)}
.ct-card:hover{transform:translateY(-3px);box-shadow:0 18px 40px -22px rgba(0,0,0,.25);border-color:var(--accent);background:#fff}
.ct-card .ct-h{font-size:11px;letter-spacing:.24em;text-transform:uppercase;color:var(--accent);font-weight:600;margin-bottom:10px}
.ct-card .ct-big{font-family:var(--display);font-size:clamp(20px,2.2vw,26px);line-height:1.2;color:var(--ink);margin-bottom:8px;word-break:break-word}
.ct-card .ct-d{font-size:14px;color:var(--ink-mute);line-height:1.5}

/* Footer */
.colophon{background:var(--ink);color:var(--bone-warm);border-top:1px solid rgba(239,235,227,.08)}
.colophon-frame{max-width:var(--max);margin:0 auto;padding:56px var(--frame-pad);display:grid;grid-template-columns:1fr 1fr 1fr;gap:48px;align-items:start}
.colophon-mark{font-family:var(--display);font-size:30px;color:var(--bone);line-height:1;display:block;margin-bottom:6px}
.colophon-mark em{font-style:italic;color:var(--accent);margin-right:.04em}
.colophon-tag{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--ink-faint)}
.colophon-meta{font-size:14px;line-height:1.7}
.colophon-meta a{color:var(--bone-warm);border-bottom:1px solid transparent;transition:border-color .25s}
.colophon-meta a:hover{border-color:var(--accent);color:var(--bone)}
.colophon-fine{font-size:11px;color:var(--ink-faint);text-align:right}
.bg-refs{padding:14px var(--frame-pad);border-top:1px solid rgba(239,235,227,.06);font-size:10px;line-height:1.8;color:rgba(239,235,227,.32);display:flex;flex-wrap:wrap;gap:6px 14px}
.bg-refs a{color:rgba(239,235,227,.42);text-decoration:none;border-bottom:1px solid transparent;transition:color .25s,border-color .25s}
.bg-refs a:hover{color:var(--accent);border-color:var(--accent)}

@media (max-width:820px){
  .primary{display:none}.menu-toggle{display:flex!important}
  .colophon-frame{grid-template-columns:1fr;gap:24px}
  .colophon-fine{text-align:left}
  .ar-list a{flex-direction:column;gap:4px;align-items:flex-start;padding:14px 0}
  .ar-t{text-align:left}
  .about-frame{grid-template-columns:1fr;gap:32px}
  .ap-photo{max-width:360px;aspect-ratio:1/1}
  .ps-row{gap:14px 24px;font-size:18px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}
''').replace('BUILD_TIMESTAMP', os.environ.get('BUILD', 'dev'))

# --- WRITE ---
def write(path, content):
    out = os.path.join(ROOT, path.lstrip('/'))
    os.makedirs(os.path.dirname(out) or out, exist_ok=True)
    with open(out, 'w') as f: f.write(content)

write('index.html', render_index())
write('about/index.html', render_about())
write('archive/index.html', render_archive())
write('press/index.html', render_press())
write('writing/index.html', render_writing())
write('contact/index.html', render_contact())

# Render every recovered WordPress Page at /<slug>/
for p in content_pages:
    write(f'{p["slug"]}/index.html', render_wp_page(p))
write('styles.css', render_styles())

post_count = 0
for p in posts:
    rel = post_url(p).lstrip('/')
    write(rel + 'index.html', render_post(p))
    post_count += 1

# Sitemap (with image extension)
sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemaps-image/1.1">']
_main_paths = [('/', 1.0), ('/about/', 0.8), ('/writing/', 0.9), ('/press/', 0.9), ('/archive/', 0.7), ('/contact/', 0.85)]
_main_paths += [(f'/{p["slug"]}/', 0.7) for p in content_pages]
for path, prio in _main_paths:
    sm.append(f'  <url><loc>https://{SITE_HOST}{path}</loc><priority>{prio}</priority><changefreq>weekly</changefreq></url>')
for p in posts:
    hero = p.get('hero')
    img_xml = ''
    if hero:
        local = img_map.get(hero.split('?')[0], hero)
        img_xml = f'<image:image><image:loc>https://{SITE_HOST}{local}</image:loc></image:image>'
    sm.append(f'  <url><loc>https://{SITE_HOST}{post_url(p)}</loc><lastmod>{p["date"]}</lastmod><priority>0.6</priority>{img_xml}</url>')
sm.append('</urlset>')
write('sitemap.xml', '\n'.join(sm))

# robots.txt — allow Google + Google-Extended (Gemini answers)
write('robots.txt',
      'User-agent: *\nAllow: /\n\n'
      'User-agent: GPTBot\nDisallow: /\n'
      'User-agent: ClaudeBot\nDisallow: /\n'
      'User-agent: CCBot\nDisallow: /\n'
      'User-agent: anthropic-ai\nDisallow: /\n'
      'User-agent: PerplexityBot\nDisallow: /\n\n'
      f'Sitemap: https://{SITE_HOST}/sitemap.xml\n')

# RSS feed
def rss_item(p):
    canonical = f'https://{SITE_HOST}{post_url(p)}'
    pub = datetime.strptime(p['date'], '%Y-%m-%d').strftime('%a, %d %b %Y 00:00:00 +0000')
    desc_text = (p.get('description') or html.unescape(re.sub(r'<[^>]+>', ' ', p.get('body_html','')))[:300]).strip()
    return f'<item><title>{html.escape(p["title"])}</title><link>{canonical}</link><guid>{canonical}</guid><pubDate>{pub}</pubDate><description>{html.escape(desc_text)}</description></item>'
rss_items = ''.join(rss_item(p) for p in posts[:30])
write('feed.xml', f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Israel Joffe</title><link>https://{SITE_HOST}/</link><description>{html.escape(TAGLINE)}</description><language>en-US</language>{rss_items}</channel></rss>')

# _headers
write('_headers',
      '/*\n'
      '  Cache-Control: public, max-age=0, must-revalidate\n'
      '  X-Content-Type-Options: nosniff\n'
      '  Referrer-Policy: strict-origin-when-cross-origin\n'
      '  Permissions-Policy: interest-cohort=()\n\n'
      '/styles.css\n  Cache-Control: public, max-age=300, must-revalidate\n\n'
      '/img/*\n  Cache-Control: public, max-age=31536000, immutable\n\n'
      '/sitemap.xml\n  Content-Type: application/xml\n  Cache-Control: public, max-age=3600\n\n'
      '/feed.xml\n  Content-Type: application/rss+xml; charset=utf-8\n  Cache-Control: public, max-age=3600\n\n'
      '/robots.txt\n  Content-Type: text/plain\n  Cache-Control: public, max-age=86400\n')

# favicon
write('favicon.svg', '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#0e0e0e"/><text x="32" y="46" font-family="Georgia, serif" font-size="40" fill="#efebe3" text-anchor="middle" letter-spacing="-1"><tspan fill="''' + ACCENT + '''" font-style="italic">I</tspan><tspan>J</tspan></text></svg>''')

print(f'  built {post_count} post pages + 5 page templates + sitemap + robots')
