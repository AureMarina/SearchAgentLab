#!/usr/bin/env python3
"""Offline production ESM packaging; stdlib only, no CDN or npm download.

The normal esbuild script remains supported. This fallback preserves lazy import
boundaries, rewrites local module dependencies to cache-busted asset URLs, and
inlines the CSS import graph. It does not minify JS or perform tree-shaking.
"""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'searchagent_lab/static'
ASSETS = STATIC / 'assets'
ENTRY = STATIC / 'studio/app.js'
CSS = STATIC / 'studio/shell.css'
# Only local literal imports are used in this repository. Both static and lazy
# imports are checked for on-disk existence and confined to the static tree.
IMPORT = re.compile(r'''(?P<head>\bfrom\s*|\bimport\s*(?:\(\s*)?)(?P<quote>['"])(?P<path>\.[^'"\n]+)(?P=quote)''')
CSS_IMPORT = re.compile(r'''@import\s+(['"])([^'"]+)\1\s*;''')


def local(base, relative):
    path = (base.parent / relative).resolve()
    if not path.is_relative_to(STATIC.resolve()) or not path.is_file():
        raise RuntimeError(f'Invalid local dependency: {base}: {relative}')
    return path


def build():
    modules = {}
    def visit(path):
        path = path.resolve()
        if path in modules:
            return
        source = path.read_text()
        modules[path] = source
        for match in IMPORT.finditer(source):
            visit(local(path, match['path']))
    visit(ENTRY)
    def css_text(path, parents=()):
        if path in parents:
            raise RuntimeError('CSS import cycle')
        source = path.read_text()
        # Existing styles only use root/data URLs. Fail rather than silently
        # move a relative image/font URL to the wrong asset directory.
        for url in re.findall(r'url\(\s*[\'"]?([^\)\'" ]+)', source):
            if not url.startswith(('/', 'data:', 'https:', 'http:', '#')):
                raise RuntimeError(f'Relative CSS asset requires rewriting: {path}: {url}')
        return CSS_IMPORT.sub(lambda m: css_text(local(path, m[2]), (*parents, path)), source)
    css = css_text(CSS.resolve())
    seed = hashlib.sha256(('\n'.join(str(p.relative_to(STATIC.resolve())) + '\n' + modules[p] for p in sorted(modules)) + css).encode()).hexdigest()
    names = {p: ('main-' if p == ENTRY.resolve() else 'chunk-') + hashlib.sha256((seed + str(p.relative_to(STATIC.resolve()))).encode()).hexdigest()[:16].upper() + '.js' for p in modules}
    content = {}
    dependencies = {}
    for path, source in modules.items():
        deps = []
        def rewrite(match):
            dest = local(path, match['path'])
            deps.append(names[dest])
            return match['head'] + match['quote'] + './' + names[dest] + match['quote']
        content[names[path]] = IMPORT.sub(rewrite, source)
        dependencies[names[path]] = sorted(set(deps))
    css_name = 'shell-' + hashlib.sha256(css.encode()).hexdigest()[:16].upper() + '.css'
    content[css_name] = css
    # Stage and validate everything before removing old generated assets.
    ASSETS.mkdir(exist_ok=True)
    for name in list(ASSETS.iterdir()):
        if re.fullmatch(r'(main|shell|chunk)-[A-Z0-9]+\.(js|css)', name.name):
            name.unlink()
    for name, source in content.items():
        (ASSETS / name).write_text(source)
    main = names[ENTRY.resolve()]
    html_path = STATIC / 'index.html'
    html = html_path.read_text()
    html = re.sub(r'<link\b[^>]*rel="(?:stylesheet|modulepreload)"[^>]*>\s*', '', html)
    html = re.sub(r'<script type="module"[^>]*></script>', '', html)
    html = html.replace('</head>', f'<link rel="stylesheet" href="/assets/{css_name}">\n<script type="module" src="/assets/{main}"></script>\n</head>')
    html_path.write_text(html)
    manifest = {'builder': 'Python stdlib native ESM packager (unminified)', 'main': '/assets/'+main, 'css': '/assets/'+css_name, 'preload': [], 'files': [{'name':n,'bytes':len(s.encode()),'sha256':hashlib.sha256(s.encode()).hexdigest()} for n,s in content.items()], 'dependencies':dependencies}
    (ASSETS/'build-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps({'modules':len(modules),'files':len(content),'bytes':sum(len(x.encode()) for x in content.values()),'main':main,'css':css_name}))


if __name__ == '__main__':
    build()
