# Roxora apt repository

Roxora's own packages for installed Roxora systems, served by GitHub Pages at
https://roxora-os.github.io/apt. Ubuntu's packages come from the Ubuntu
archive and the hardware packages (kernel, device tree, U-Boot, firmware)
from Armbian's repository; this repository carries only packages named
`roxora-*`, and the apt pin of a Roxora system takes nothing else from it.

How a system uses it, and how a system from before the repository joins it:
[Updating to a new Roxora release](https://github.com/roxora-os/build/blob/main/docs/install.md#updating-to-a-new-roxora-release).

## Layout

    dists/<suite>/InRelease, Release, Release.gpg
    dists/<suite>/main/binary-arm64/Packages{,.gz,.xz}, by-hash/
    pool/main/r/<package>/<package>_<version>_<arch>.deb
    roxora-archive-keyring.asc, roxora-archive-keyring.gpg
    index.html

One suite per Ubuntu release, named after it: `noble` (24.04), `resolute`
(26.04) and `stonking` (26.10). One component, `main`, for `arm64`;
packages for all architectures are in the same index.

A new suite starts with the keyring, hwtool and the wallpapers, which go
into every suite. Its release packages come with the first release of the
build repository whose `boards.conf` has images for it; the script of an
earlier release builds nothing for that suite.

## Packages

| Package | From | Suites |
| --- | --- | --- |
| `roxora-archive-keyring` | `keys/roxora-archive-keyring.asc`, built by `packages/keyring/build.sh` | all |
| `roxora-hwtool`, `roxora-hwtool-gtk` | the releases of [roxora-os/hwtool](https://github.com/roxora-os/hwtool), not drafts or pre-releases, from 0.3.0 on; each file checked against the SHA-256 digest GitHub reports for it (`scripts/fetch-github-releases.py`) | all |
| `roxora-imager` | the releases of [roxora-os/imager](https://github.com/roxora-os/imager), the same way, from 0.1.9 on: its arm64 `.deb` | all |
| `roxora-wallpapers` | [roxora-os/design](https://github.com/roxora-os/design) at the commit in `packages/wallpapers/wallpapers.conf`, every file checked against its SHA-256 there (`packages/wallpapers/build.sh`) | all |
| `roxora-release`, `roxora-cli`, `roxora-desktop-gnome-minimal`, `roxora-desktop-gnome` | the releases of [roxora-os/build](https://github.com/roxora-os/build), not drafts or pre-releases: built at the release's tag by that tag's `userpatches/roxora/packages/build.sh` (`scripts/fetch-release-packages.sh`). Version `<release>+<Ubuntu version>`, for example `2026.11.5+24.04`. | one build per suite |

What the release packages hold is described in the build repository's
[architecture.md](https://github.com/roxora-os/build/blob/main/docs/architecture.md#roxoras-apt-repository).

## How a run works

`.github/workflows/publish.yml` runs on every push to `main`, every hour, by
hand and on a `repository_dispatch` (`hwtool-release`, `build-release`):

1. `scripts/assemble.py --list` reads what is published: the InRelease of
   each suite, checked against `keys/roxora-archive-keyring.asc`.
2. The packages are built and fetched as in the table. Release packages are
   built only for releases the repository does not publish yet.
3. `scripts/assemble.py` takes every published package file, from the
   Actions cache of earlier runs or else downloaded, checks it against the
   published index, and adds this run's packages. A package
   whose name, version and architecture are published already must have
   the same content (control data, files, modes): a published version never
   changes, and a different package under the same version stops the run.
   Nothing is ever removed. It writes the indexes with `apt-ftparchive`,
   keeps the previous indexes under `by-hash/` (Pages can serve the old
   InRelease for some minutes after a deploy), and signs each Release file
   (InRelease and Release.gpg, SHA-512 digests).
4. The secret key comes from the `APT_SIGNING_KEY` secret, is imported into
   a throwaway gpg home for that one step and checked to be the key of
   `config/repo.conf`.
5. Before anything is deployed, clean `ubuntu:24.04`, `ubuntu:26.04` and
   `ubuntu:26.10` containers on arm64 use the result as a system does
   (`scripts/check-apt.sh`): `apt-get update` with every warning an error
   (gpgv checks the signature on 24.04, Sequoia's sqv from 26.04), every
   package a `roxora-*` package with its candidate from this repository,
   the keyring, hwtool and the wallpapers installed, every package resolved
   against the Ubuntu archive.
6. A run on `main` deploys to Pages, every time (see Valid-Until below),
   and the same containers then check the published URL.

New hwtool, Roxora Imager and Roxora releases are picked up by the hourly
run, so nothing needs to be done here for them. To publish at once, run the
workflow by hand (Actions, Publish, Run workflow). GitHub turns off
scheduled workflows in a repository without activity for 60 days; a push or
a manual run turns it on again.

`workflow_dispatch` takes `build_ref`, a branch, tag or commit of the build
repository whose release packages are built as well; such a run checks the
result and is never deployed.

## Size

GitHub Pages publishes a site of at most 1 GB, with a soft limit of 100 GB
of downloads a month. Nothing is ever removed from this repository, and
each version of `roxora-imager` adds about 170 MB (the other packages
together are a few MB), so it has room for about five of them. Each run
writes the size of the repository to its log, with a warning from 700 MB
on. Past 1 GB the deploy may fail, and if no run deploys for 10 days the
Release files expire (see below). Before that, older versions of
`roxora-imager` have to be removed, or its package has to get smaller.

The hourly run itself downloads little from Pages: the published package
files are kept in the Actions cache between runs (a cached file is used
only when it matches the signed index), the list of what is published
reads only the indexes, and the expired and short-lived repositories of
the checks hold only the keyring. Every desktop image build downloads
`roxora-imager` once.

## Betas

Only releases reach this repository. Beta release packages (a build tag
`vYYYY.MM.N-beta.N`, version `YYYY.MM.N~beta.N`) would go into a second
component, `beta`, of the same suites: a system that wants betas adds
`beta` to `Components:` of its `roxora.sources`, and the apt pin needs no
change, because it matches on the repository's host. That component is not
built yet.

## Valid-Until, and when publishing stops

Each Release file says until when it is valid: 10 days after the run that
signed it (`REPO_VALID_DAYS` in `config/repo.conf`). Every run from `main`
signs and deploys again, with a new `Date` and `Valid-Until`, also when no
package changed, so with the hourly schedule the files are never more than
about an hour old. A replayed old Release file (an attacker or a stale
mirror holding back updates) stops working after 10 days. After every
deploy, `scripts/check-apt.sh` on the live URL fails if the files are valid
for less than 7 more days; each run also checks that a repository valid for
5 days, and one that has expired, are refused.

If no run deploys for more than 10 days, the files expire. `apt update`
then stops with an error for this source, on every Roxora system:

    E: Release file for https://roxora-os.github.io/apt/dists/noble/InRelease is expired (invalid since ...). Updates for this repository will not be applied.

Ubuntu's and Armbian's sources still update; Roxora's packages do not, and
apt exits non-zero, which can stop scripts and unattended jobs that run
`apt update`. Recovery is on this side only: run the Publish workflow
(Actions, Publish, Run workflow, or `gh workflow run publish.yml -R
roxora-os/apt`); once it is deployed, the next `apt update` on each system
works again, nothing has to be done there. Things that stop the schedule:
GitHub turns scheduled workflows off in a repository without a commit for
60 days (the runs themselves do not count), Actions being turned off, or the
`APT_SIGNING_KEY` secret going missing. Turn the workflow on again (Actions,
Publish, Enable workflow) and run it. A user who cannot wait can skip the
check for one update: `sudo apt -o Acquire::Check-Valid-Until=false update`.

The key itself is valid until 2031-11-14; it has to be extended (and
`roxora-archive-keyring` published again) before then.

## Signing key

Ed25519, `Roxora apt <oss@gahingwoo.com>`, fingerprint
`07353CCFAF21725418E3CE4798FC2BE6A9A8952D`, valid until 2031-11-14. The
public key is `keys/roxora-archive-keyring.asc`; the secret key is only in
the `APT_SIGNING_KEY` secret.

## Building locally

The packages and an unsigned repository, without root:

    packages/keyring/build.sh debs/all
    packages/wallpapers/build.sh debs/all
    scripts/fetch-github-releases.py debs/all
    python3 scripts/assemble.py --debs debs --out repo

## License

GPL-2.0, see `LICENSE`. The wallpapers are CC BY-SA 4.0 (see the package's
copyright file).
