#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
"""Assemble Roxora's apt repository.

    scripts/assemble.py --debs DIR --out DIR [--live URL [--cache DIR]] [--sign]
    scripts/assemble.py --list --live URL

--debs holds the packages built or fetched for this run: DIR/all/*.deb go
into every suite, DIR/<suite>/*.deb into that suite only (the release
packages, which are built per Ubuntu release).

--live is the published repository. Everything it publishes is kept: its
signed indexes are checked against keys/roxora-archive-keyring.asc, its
package files are downloaded and checked against them, and they go into the
new repository unchanged. A package of this run whose name, version and
architecture the live repository already has must have the same content;
if it differs the run stops, because a published version never changes.
Without --live the repository holds this run's packages only. --list
prints what the live repository publishes, one "<suite> <package>
<version> <architecture>" per line, after the same checks of its indexes,
and stops; it downloads no package files.

--cache is a directory that keeps the live repository's package files from
one run to the next, by their pool path. A file there is used when it
matches the published index, so a run downloads only the files it does not
have yet (GitHub Pages has a monthly download limit, see README.md).

Every Release file is valid for REPO_VALID_DAYS (config/repo.conf) from now
(Valid-Until). --sign signs the Release files (InRelease and Release.gpg) with the key of
config/repo.conf from the gpg home directory in GNUPGHOME.

Writes the repository to --out: pool/, dists/<suite>/, the public key as
roxora-archive-keyring.asc and .gpg, and site/ (the index page). Prints
"changed=true" or "changed=false": whether the packages differ from the live
repository's.
"""

import argparse
import gzip
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

TOP = pathlib.Path(__file__).resolve().parent.parent
KEY_ASC = TOP / "keys" / "roxora-archive-keyring.asc"


def die(msg):
    print(f"assemble.py: {msg}", file=sys.stderr)
    sys.exit(1)


def conf():
    """KEY=VALUE lines of config/repo.conf."""
    values = {}
    for line in (TOP / "config" / "repo.conf").read_text().splitlines():
        m = re.match(r'^([A-Z_]+)=(.*)$', line)
        if m:
            values[m.group(1)] = m.group(2).strip('"')
    return values


def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url):
    """The body at url, or None for a 404."""
    req = urllib.request.Request(url, headers={"User-Agent": "roxora-apt-assemble"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def stanzas(text):
    """Paragraphs of a deb822 file as dicts (continuation lines kept)."""
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        if not para.strip():
            continue
        d, key = {}, None
        for line in para.splitlines():
            if line[:1] in (" ", "\t") and key:
                d[key] += "\n" + line
            else:
                key, _, value = line.partition(":")
                d[key] = value.strip()
        out.append(d)
    return out


def release_hashes(release_text, field="SHA256"):
    """{path: (hash, size)} of the SHA256 (or SHA512) list of a Release file."""
    for d in stanzas(release_text):
        if field in d:
            out = {}
            for line in d[field].splitlines():
                parts = line.split()
                if len(parts) == 3:
                    out[parts[2]] = (parts[0], int(parts[1]))
            return out
    return {}


class Keyring:
    def __init__(self, work):
        self.home = work / "gnupg-verify"
        self.home.mkdir(mode=0o700)
        self.gpg = work / "roxora-archive-keyring.gpg"
        with open(KEY_ASC, "rb") as src, open(self.gpg, "wb") as dst:
            run("gpg", "--homedir", str(self.home), "--batch", "--dearmor", stdin=src, stdout=dst,
                stderr=subprocess.DEVNULL)

    def verify_inrelease(self, data, work, name):
        """The Release text of a signed InRelease, if the signature is good."""
        p = work / name
        p.write_bytes(data)
        out = work / (name + ".txt")
        r = subprocess.run(["gpgv", "--keyring", str(self.gpg), "--output", str(out), str(p)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            die(f"{name}: bad signature\n{r.stderr}")
        return out.read_text()


def live_state(url, suites, keyring, work, packages=True, cache=None):
    """{suite: [stanza, ...]} of the live repository; with packages, its
    package files in work/live, from cache or downloaded, and checked."""
    state = {}
    for suite in suites:
        data = fetch(f"{url}/dists/{suite}/InRelease")
        if data is None:
            state[suite] = None
            continue
        release = keyring.verify_inrelease(data, work, f"InRelease-{suite}")
        hashes = release_hashes(release)
        path = None
        for name in ("main/binary-arm64/Packages.xz", "main/binary-arm64/Packages.gz", "main/binary-arm64/Packages"):
            if name in hashes:
                path = name
                break
        if path is None:
            die(f"live {suite}: the Release file lists no main/binary-arm64/Packages")
        body = fetch(f"{url}/dists/{suite}/{path}")
        if body is None or hashlib.sha256(body).hexdigest() != hashes[path][0]:
            die(f"live {suite}: {path} is missing or does not match its Release file")
        if path.endswith(".xz"):
            import lzma
            body = lzma.decompress(body)
        elif path.endswith(".gz"):
            body = gzip.decompress(body)
        # The live index files, kept under by-hash for clients that have the
        # previous InRelease (GitHub Pages caches pages for some minutes).
        prev = work / "previous" / suite
        for name, (h, _) in hashes.items():
            if not name.startswith("main/binary-arm64/Packages"):
                continue
            b = fetch(f"{url}/dists/{suite}/{name}")
            if b is not None and hashlib.sha256(b).hexdigest() == h:
                (prev / name).parent.mkdir(parents=True, exist_ok=True)
                (prev / name).write_bytes(b)
        state[suite] = stanzas(body.decode())

    if all(v is None for v in state.values()):
        # No suite at all: either nothing was published yet, or the site is
        # not what it should be. The key is published with the first run.
        if fetch(f"{url}/roxora-archive-keyring.asc") is not None:
            die(f"{url} serves the key but no suite: refusing to start the repository again")
        print(f"{url}: nothing published yet", file=sys.stderr)
    for suite, entries in state.items():
        if entries is None:
            state[suite] = []
            continue
        if not packages:
            continue
        for e in entries:
            dst = work / "live" / e["Filename"]
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            kept = cache / e["Filename"] if cache else None
            if kept and kept.is_file() and sha256(kept) == e["SHA256"]:
                shutil.copyfile(kept, dst)
                continue
            body = fetch(f"{url}/{e['Filename']}")
            if body is None or hashlib.sha256(body).hexdigest() != e["SHA256"]:
                die(f"live {suite}: {e['Filename']} is missing or does not match the index")
            dst.write_bytes(body)
            if kept:
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_bytes(body)
    return state


def control(deb):
    text = subprocess.run(["dpkg-deb", "-f", str(deb)], check=True, capture_output=True, text=True).stdout
    return stanzas(text)[0]


def same_content(a, b, work):
    """Do two packages hold the same control data and files?"""
    with tempfile.TemporaryDirectory(dir=work) as t:
        da, db = pathlib.Path(t, "a"), pathlib.Path(t, "b")
        run("dpkg-deb", "-R", str(a), str(da))
        run("dpkg-deb", "-R", str(b), str(db))
        r = subprocess.run(["diff", "-r", "--no-dereference", str(da), str(db)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[:4000], file=sys.stderr)
            return False
        # File modes and owners, which diff does not compare.
        la = subprocess.run(["dpkg-deb", "-c", str(a)], capture_output=True, text=True, check=True).stdout
        lb = subprocess.run(["dpkg-deb", "-c", str(b)], capture_output=True, text=True, check=True).stdout
        strip = lambda s: sorted((l.split()[0], l.split()[1], l.split()[-1]) for l in s.splitlines())
        return strip(la) == strip(lb)


def pool_path(c):
    name = c["Package"]
    prefix = name[:4] if name.startswith("lib") else name[0]
    return f"pool/main/{prefix}/{name}/{name}_{c['Version'].split(':')[-1]}_{c['Architecture']}.deb"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debs")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--live")
    ap.add_argument("--cache")
    ap.add_argument("--sign", action="store_true")
    args = ap.parse_args()

    c = conf()
    suites = c["REPO_SUITES"].split()
    if args.list:
        if not args.live:
            die("--list needs --live")
        work = pathlib.Path(tempfile.mkdtemp(prefix="assemble-"))
        live = live_state(args.live, suites, Keyring(work), work, packages=False)
        for s in suites:
            for e in live[s]:
                print(s, e["Package"], e["Version"], e["Architecture"])
        shutil.rmtree(work)
        return
    if not args.debs or not args.out:
        die("--debs and --out are needed")
    debs = pathlib.Path(args.debs).resolve()
    out = pathlib.Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="assemble-"))
    keyring = Keyring(work)

    cache = pathlib.Path(args.cache).resolve() if args.cache else None
    live = live_state(args.live, suites, keyring, work, cache=cache) if args.live else {s: [] for s in suites}

    # suite -> {(package, version, arch): (path of the file, index stanza or None)}
    entries = {s: {} for s in suites}
    for s in suites:
        for e in live[s]:
            entries[s][(e["Package"], e["Version"], e["Architecture"])] = (work / "live" / e["Filename"], e)
    live_keys = {s: set(entries[s]) for s in suites}

    for sub in sorted(p for p in debs.iterdir() if p.is_dir()):
        targets = suites if sub.name == "all" else [sub.name]
        if sub.name != "all" and sub.name not in suites:
            die(f"{sub}: {sub.name} is not a suite of config/repo.conf ({' '.join(suites)})")
        for deb in sorted(sub.glob("*.deb")):
            ctl = control(deb)
            if not ctl.get("Package", "").startswith("roxora-"):
                die(f"{deb.name}: only roxora-* packages belong in this repository")
            key = (ctl["Package"], ctl["Version"], ctl["Architecture"])
            for s in targets:
                if key in entries[s]:
                    have = entries[s][key][0]
                    if sha256(have) == sha256(deb):
                        continue
                    if key in live_keys[s] and same_content(have, deb, work):
                        print(f"{s}: {key[0]} {key[1]} rebuilt with the same content, keeping the published file")
                        continue
                    die(f"{s}: {key[0]} {key[1]} ({key[2]}) is already "
                        f"{'published' if key in live_keys[s] else 'in this run'} with different content; give the new one a new version")
                entries[s][key] = (deb, None)

    # One file per pool path, the same in every suite that lists it.
    pool = {}
    for s in suites:
        for key, (path, _) in entries[s].items():
            ctl = control(path)
            dst = pool_path(ctl)
            if dst in pool and sha256(pool[dst]) != sha256(path):
                die(f"{dst}: two different files for one pool path")
            pool[dst] = path
    for dst, src in pool.items():
        (out / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out / dst)
        os.chmod(out / dst, 0o644)

    # REPO_VALID_SECONDS in the environment replaces it, for the workflow's
    # check that apt and check-apt.sh refuse a short-lived or expired repository.
    valid_seconds = int(os.environ.get("REPO_VALID_SECONDS") or int(c["REPO_VALID_DAYS"]) * 86400)
    arch = c["REPO_ARCHITECTURES"]
    comp = c["REPO_COMPONENT"]
    changed = False
    for s in suites:
        # apt-ftparchive indexes a directory: give it the files of this suite.
        tree = work / "tree" / s
        for key, (path, _) in entries[s].items():
            dst = tree / pool_path(control(path))
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.link(out / pool_path(control(path)), dst)
        bindir = out / "dists" / s / comp / f"binary-{arch}"
        bindir.mkdir(parents=True)
        packages = subprocess.run(
            ["apt-ftparchive", "-o", "APT::FTPArchive::MD5=false", "-o", "APT::FTPArchive::SHA1=false",
             "packages", "pool"], cwd=tree, check=True, capture_output=True, text=True).stdout
        # Same order every run: by package, then version file name.
        paras = sorted(stanzas(packages), key=lambda d: d["Filename"])
        text = "\n\n".join("\n".join(f"{k}: {v}" for k, v in d.items()) for d in paras) + "\n"
        (bindir / "Packages").write_text(text)
        with open(bindir / "Packages.gz", "wb") as f:
            f.write(gzip.compress(text.encode(), mtime=0))
        run("xz", "-k", "-9", str(bindir / "Packages"))
        (bindir / "Release").write_text(
            f"Archive: {s}\nOrigin: {c['REPO_ORIGIN']}\nLabel: {c['REPO_LABEL']}\nComponent: {comp}\nArchitecture: {arch}\n")

        if {k for k in entries[s]} != live_keys[s] or any(e[1] is None for e in entries[s].values()):
            changed = True

        # Index files of this run and of the live repository by their
        # hashes (Acquire-By-Hash), so a client that holds either InRelease
        # finds the files it names.
        for src_dir in (bindir, work / "previous" / s / comp / f"binary-{arch}"):
            if not src_dir.is_dir():
                continue
            for f in src_dir.glob("Packages*"):
                data = f.read_bytes()
                for algo in ("SHA256", "SHA512"):
                    h = hashlib.new(algo.lower(), data).hexdigest()
                    dst = bindir / "by-hash" / algo / h
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(data)

        rel_conf = work / f"release-{s}.conf"
        rel_conf.write_text("\n".join([
            f'APT::FTPArchive::Release::Origin "{c["REPO_ORIGIN"]}";',
            f'APT::FTPArchive::Release::Label "{c["REPO_LABEL"]}";',
            f'APT::FTPArchive::Release::Suite "{s}";',
            f'APT::FTPArchive::Release::Codename "{s}";',
            f'APT::FTPArchive::Release::Architectures "{arch}";',
            f'APT::FTPArchive::Release::Components "{comp}";',
            f'APT::FTPArchive::Release::Description "Roxora packages for Ubuntu {s}";',
            'APT::FTPArchive::Release::Acquire-By-Hash "yes";',
            f'APT::FTPArchive::Release::ValidTime "{valid_seconds}";',
            'APT::FTPArchive::Release::MD5 "false";',
            'APT::FTPArchive::Release::SHA1 "false";',
            'APT::FTPArchive::Release::SHA256 "true";',
            'APT::FTPArchive::Release::SHA512 "true";',
            ""]))
        dist = out / "dists" / s
        # Only the index files, not the by-hash copies, go into Release.
        release = subprocess.run(
            ["apt-ftparchive", "-c", str(rel_conf), "-o", "APT::FTPArchive::Release::Patterns::=Packages*",
             "-o", "APT::FTPArchive::Release::Patterns::=Release", "release", "."],
            cwd=dist, check=True, capture_output=True, text=True).stdout
        release = "\n".join(l for l in release.splitlines() if "/by-hash/" not in l) + "\n"
        (dist / "Release").write_text(release)
        if args.sign:
            fpr = c["REPO_KEY_FINGERPRINT"]
            common = ["gpg", "--batch", "--yes", "--no-tty", "--digest-algo", "SHA512", "--local-user", fpr]
            run(*common, "--clearsign", "--output", str(dist / "InRelease"), str(dist / "Release"))
            run(*common, "--armor", "--detach-sign", "--output", str(dist / "Release.gpg"), str(dist / "Release"))
            # Check what was signed, with the public key only.
            for args_v in (["--keyring", str(keyring.gpg), str(dist / "InRelease")],
                           ["--keyring", str(keyring.gpg), str(dist / "Release.gpg"), str(dist / "Release")]):
                r = subprocess.run(["gpgv", *args_v], capture_output=True, text=True)
                if r.returncode != 0:
                    die(f"{s}: the new signature does not verify\n{r.stderr}")

        # Every index file the Release lists is there with its hash, and
        # every package file the index names is there with its hash.
        for name, (h, size) in release_hashes(release).items():
            p = dist / name
            if not p.is_file() or sha256(p) != h or p.stat().st_size != size:
                die(f"{s}: {name} does not match the Release file")
        for d in paras:
            p = out / d["Filename"]
            if not p.is_file() or sha256(p) != d["SHA256"]:
                die(f"{s}: {d['Filename']} does not match the index")
        print(f"{s}: {len(paras)} packages")
        for d in paras:
            print(f"  {d['Package']} {d['Version']} {d['Architecture']}")

    # The public key, for the one-time setup of systems from before the
    # repository, and the index page.
    shutil.copyfile(KEY_ASC, out / "roxora-archive-keyring.asc")
    shutil.copyfile(keyring.gpg, out / "roxora-archive-keyring.gpg")
    top_files = ["roxora-archive-keyring.asc", "roxora-archive-keyring.gpg"]
    for f in sorted((TOP / "site").iterdir()):
        shutil.copyfile(f, out / f.name)
        top_files.append(f.name)
    if args.live:
        for name in top_files:
            if fetch(f"{args.live}/{name}") != (out / name).read_bytes():
                changed = True
    else:
        changed = True
    shutil.rmtree(work)
    print(f"changed={'true' if changed else 'false'}")


if __name__ == "__main__":
    main()
