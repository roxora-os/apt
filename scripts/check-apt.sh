#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
#
# Use the repository the way an installed system does, in a clean Ubuntu
# container (as root).
#
#   [MIN_VALID_DAYS=7] scripts/check-apt.sh <repository URI> <keyring.gpg>
#
# Writes /etc/apt/sources.list.d/roxora.sources for the container's Ubuntu
# release, as the documentation does, with Signed-By pointing at a copy of
# <keyring.gpg>, and runs "apt-get update" with any warning counted as an
# error: a bad or missing signature, a wrong hash, an expired Release file or
# an unknown suite fails, and so does a Release file valid for less than
# MIN_VALID_DAYS more days.
# Then checks that every package of the suite is a roxora-* package with its
# candidate from this repository, installs the keyring, hwtool and the
# wallpapers, checks that the installed keyring is <keyring.gpg>, and
# resolves (without installing) everything else the suite has, the release
# packages among them, against the Ubuntu archive.

set -euo pipefail

uri="${1:?usage: $0 <repository URI> <keyring.gpg>}"
keyring="${2:?usage: $0 <repository URI> <keyring.gpg>}"
export DEBIAN_FRONTEND=noninteractive

# shellcheck source=/dev/null
codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
echo "Ubuntu ${codename}, $(apt-get --version | head -1)"
case "${uri}" in
	https:*)
		apt-get update -qq
		apt-get install -y -qq --no-install-recommends ca-certificates > /dev/null
		;;
esac

install -D -m 0644 "${keyring}" /usr/share/keyrings/roxora-archive-keyring.gpg
cat > /etc/apt/sources.list.d/roxora.sources <<- SOURCES
	Types: deb
	URIs: ${uri}
	Suites: ${codename}
	Components: main
	Signed-By: /usr/share/keyrings/roxora-archive-keyring.gpg
SOURCES
cat /etc/apt/sources.list.d/roxora.sources

if ! apt-get update --error-on=any > /tmp/update.log 2>&1; then
	cat /tmp/update.log
	echo "check-apt.sh: apt-get update failed" >&2
	exit 1
fi
cat /tmp/update.log
grep -E "^(Get|Hit):.*${uri#*://}.*InRelease" /tmp/update.log > /dev/null || {
	echo "check-apt.sh: apt did not fetch the signed InRelease of ${uri}" >&2
	exit 1
}

# apt names its list files after the URI without the scheme, "/" as "_".
lists="$(echo "${uri#*:}" | sed 's|^/*||; s|/*$||; s|/|_|g')"

# The signed Release file must be valid for MIN_VALID_DAYS more days (7 by
# default): the repository is signed for 10 and again every hour, so less
# means that publishing has stopped or a run signed it with a short time.
min_days="${MIN_VALID_DAYS:-7}"
inrelease=""
for f in /var/lib/apt/lists/*"${lists}_dists_${codename}_InRelease"*; do
	[[ -f "${f}" ]] && inrelease="${f}" && break
done
[[ -n "${inrelease}" ]] || {
	echo "check-apt.sh: apt kept no InRelease of ${uri}" >&2
	exit 1
}
valid_until="$(/usr/lib/apt/apt-helper cat-file "${inrelease}" | sed -n 's/^Valid-Until: //p' | head -1)"
[[ -n "${valid_until}" ]] || {
	echo "check-apt.sh: the Release file of ${codename} has no Valid-Until" >&2
	exit 1
}
left=$((($(date -u -d "${valid_until}" +%s) - $(date -u +%s)) / 86400))
if ((left < min_days)); then
	echo "check-apt.sh: the Release file of ${codename} is valid until ${valid_until}, ${left} days from now (at least ${min_days} needed)" >&2
	exit 1
fi
echo "${codename}: Release valid until ${valid_until} (${left} days)"
# Docker's Ubuntu images keep the lists compressed; apt-helper reads either.
mapfile -t pkgs < <(for f in /var/lib/apt/lists/*"${lists}_dists_${codename}_main_binary-arm64_Packages"*; do
	/usr/lib/apt/apt-helper cat-file "${f}"
done | awk '/^Package:/ { print $2 }' | sort -u)
[[ ${#pkgs[@]} -gt 0 ]] || {
	echo "check-apt.sh: no packages in ${codename}" >&2
	exit 1
}
echo "${codename}: ${pkgs[*]}"
for p in "${pkgs[@]}"; do
	[[ "${p}" == roxora-* ]] || {
		echo "check-apt.sh: ${p} is not a Roxora package" >&2
		exit 1
	}
	# The sources of the candidate version, from its lines in the version table.
	apt-cache policy "${p}" | awk '
		/^  Candidate:/ { cand = $2; next }
		/^ \*\*\* / { invers = ($2 == cand); next }
		/^     [^ ]/ { invers = ($1 == cand); next }
		/^        / && invers { print }
	' | grep -qF "${uri#*://}" || {
		apt-cache policy "${p}"
		echo "check-apt.sh: the candidate of ${p} is not from ${uri}" >&2
		exit 1
	}
done

apt-get install -y -qq --no-install-recommends roxora-archive-keyring roxora-hwtool roxora-wallpapers > /tmp/install.log 2>&1 || {
	cat /tmp/install.log
	exit 1
}
cmp "${keyring}" /usr/share/keyrings/roxora-archive-keyring.gpg
dpkg-query -S /usr/share/keyrings/roxora-archive-keyring.gpg
hwtool --version
dpkg-query -W roxora-archive-keyring roxora-hwtool roxora-wallpapers

# Every package resolves against the Ubuntu archive (one at a time: the
# flavor packages conflict with each other).
for p in "${pkgs[@]}"; do
	apt-get install -s -qq "${p}" > /tmp/simulate.log 2>&1 || {
		cat /tmp/simulate.log
		echo "check-apt.sh: ${p} does not resolve on ${codename}" >&2
		exit 1
	}
	echo "${codename}: ${p} resolves ($(grep -c '^Inst ' /tmp/simulate.log) packages to install)"
done
