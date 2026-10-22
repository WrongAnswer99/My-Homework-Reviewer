"""Fetch pinned, public preview assets and licenses (development tool only)."""
import concurrent.futures
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = {
    'markdown-it.min.js': 'https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js',
    'markdown-it.LICENSE': 'https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/LICENSE',
    'katex.min.js': 'https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js',
    'katex.min.css': 'https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css',
    'katex.LICENSE': 'https://cdn.jsdelivr.net/npm/katex@0.16.22/LICENSE',
    'texmath.min.js': 'https://cdn.jsdelivr.net/npm/markdown-it-texmath@1.0.0/texmath.min.js',
    'texmath.min.css': 'https://cdn.jsdelivr.net/npm/markdown-it-texmath@1.0.0/css/texmath.min.css',
    'texmath.LICENSE': 'https://cdn.jsdelivr.net/npm/markdown-it-texmath@1.0.0/license.txt',
    'purify.min.js': 'https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js',
    'purify.LICENSE': 'https://cdn.jsdelivr.net/npm/dompurify@3.2.6/LICENSE',
}


def fetch(item):
    name, url = item
    blob = urllib.request.urlopen(url, timeout=30).read()
    path = ROOT/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return name, {'url': url, 'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob)}


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        result = dict(pool.map(fetch, ASSETS.items()))
    css = (ROOT/'katex.min.css').read_text()
    fonts = {f: 'https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/'+f
             for f in re.findall(r'url\((fonts/[^)]+\.woff2)\)', css)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        result.update(dict(pool.map(fetch, fonts.items())))
    css = re.sub(r',url\(fonts/[^)]+\) format\("(?:woff|truetype)"\)', '', css)
    (ROOT/'katex.min.css').write_text(css, encoding='utf-8')
    result['katex.min.css']['local_sha256'] = hashlib.sha256((ROOT/'katex.min.css').read_bytes()).hexdigest()
    (ROOT/'vendor.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('Downloaded', len(result), 'public preview assets and licenses')
