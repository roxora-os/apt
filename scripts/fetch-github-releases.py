#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
"""Download the packages of Roxora's tools from their GitHub releases.

    scripts/fetch-github-releases.py <output directory> [PACKAGE=VERSION]...

For each tool in config/repo.conf (HWTOOL, IMAGER), takes every release of
<TOOL>_REPO that is neither a draft nor a pre-release and whose version is
<TOOL>_MIN_VERSION or newer, and from each the files
<package>_<version>_arm64.deb for every package of <TOOL>_PACKAGES. Each
file must match the SHA-256 digest GitHub reports for it; a release file
without a digest stops the run, and so does a tool with no packages at all.
A tool with <TOOL>_KEEP=N takes only its N newest such releases, and the
releases of the versions given as PACKAGE=VERSION (the pins of
assemble.py --pin), as the repository keeps no others.
GITHUB_TOKEN, when set, is sent with the API requests.
"""

import functools
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

TOP = pathlib.Path(__file__).resolve().parent.parent
TOOLS = ("HWTOOL", "IMAGER")


def die(msg):
    print(f"fetch-github-releases.py: {msg}", file=sys.stderr)
    sys.exit(1)


def conf():
    values = {}
    for line in (TOP / "config" / "repo.conf").read_text().splitlines():
        m = re.match(r'^([A-Z_]+)=(.*)$', line)
        if m:
            values[m.group(1)] = m.group(2).strip('"')
    return values


def get(url, api=True):
    headers = {"User-Agent": "roxora-apt-fetch"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as r:
        return r.read()


def version_ge(a, b):
    return subprocess.run(["dpkg", "--compare-versions", a, "ge", b]).returncode == 0


def fetch(out, repo, minimum, packages, keep=None, pinned=()):
    releases = json.loads(get(f"https://api.github.com/repos/{repo}/releases?per_page=100"))
    names = "|".join(re.escape(p) for p in packages)
    wanted = []
    for rel in releases:
        if rel["draft"] or rel["prerelease"]:
            continue
        m = re.fullmatch(r"v(\d+\.\d+\.\d+)", rel["tag_name"])
        if not m or not version_ge(m.group(1), minimum):
            continue
        wanted.append((m.group(1), rel))
    # Newest first.
    wanted.sort(key=functools.cmp_to_key(lambda a, b: 0 if a[0] == b[0] else (-1 if version_ge(a[0], b[0]) else 1)))
    if keep:
        wanted = [w for i, w in enumerate(wanted) if i < keep or w[0] in pinned]
    found = 0
    for version, rel in wanted:
        for asset in rel["assets"]:
            if not re.fullmatch(rf"({names})_{re.escape(version)}_arm64\.deb", asset["name"]):
                continue
            digest = asset.get("digest") or ""
            if not digest.startswith("sha256:"):
                die(f"{repo} {rel['tag_name']} {asset['name']}: GitHub reports no SHA-256 digest")
            body = get(asset["browser_download_url"], api=False)
            if hashlib.sha256(body).hexdigest() != digest[7:]:
                die(f"{repo} {rel['tag_name']} {asset['name']}: does not match its digest {digest}")
            (out / asset["name"]).write_bytes(body)
            print(f"{asset['name']} {digest}")
            found += 1
    if found == 0:
        die(f"no packages found in the releases of {repo}")


def main():
    out = pathlib.Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    c = conf()
    pins = {}
    for p in sys.argv[2:]:
        pkg, sep, version = p.partition("=")
        if not sep or not pkg or not version:
            die(f"{p}: not PACKAGE=VERSION")
        pins.setdefault(pkg, set()).add(version)
    for tool in TOOLS:
        packages = c[f"{tool}_PACKAGES"].split()
        keep = int(c[f"{tool}_KEEP"]) if c.get(f"{tool}_KEEP") else None
        pinned = set().union(*(pins.get(p, set()) for p in packages))
        fetch(out, c[f"{tool}_REPO"], c[f"{tool}_MIN_VERSION"], packages, keep, pinned)


if __name__ == "__main__":
    main()
