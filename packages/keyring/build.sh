#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
#
# Build roxora-archive-keyring from keys/roxora-archive-keyring.asc.
#
#   packages/keyring/build.sh <output directory>
#
# Writes roxora-archive-keyring_<version>_all.deb, and the key as
# roxora-archive-keyring.asc and roxora-archive-keyring.gpg next to it (the
# files the repository publishes at its top level). Stops when the key's
# fingerprint is not REPO_KEY_FINGERPRINT of config/repo.conf.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="$(cd "${HERE}/../.." && pwd)"
out="${1:?usage: $0 <output directory>}"
mkdir -p "${out}"
out="$(cd "${out}" && pwd)"

declare REPO_KEY_FINGERPRINT KEYRING_VERSION KEYRING_EPOCH
# shellcheck source=config/repo.conf
source "${TOP}/config/repo.conf"
# shellcheck source=packages/keyring/keyring.conf
source "${HERE}/keyring.conf"

asc="${TOP}/keys/roxora-archive-keyring.asc"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# Exactly one primary key, the one in repo.conf.
mapfile -t fprs < <(gpg --show-keys --with-colons "${asc}" 2> /dev/null | awk -F: '$1 == "pub" { want = 1; next } want && $1 == "fpr" { print $10; want = 0 }')
if [[ ${#fprs[@]} -ne 1 || "${fprs[0]}" != "${REPO_KEY_FINGERPRINT}" ]]; then
	echo "build.sh: ${asc} holds ${fprs[*]:-no key}, expected ${REPO_KEY_FINGERPRINT} alone" >&2
	exit 1
fi

pkg="${work}/pkg"
mkdir -p "${pkg}/DEBIAN" "${pkg}/usr/share/keyrings" "${pkg}/usr/share/doc/roxora-archive-keyring"
gpg --homedir "${work}" --batch --dearmor < "${asc}" > "${pkg}/usr/share/keyrings/roxora-archive-keyring.gpg" 2> /dev/null
cat > "${pkg}/usr/share/doc/roxora-archive-keyring/copyright" <<- COPYRIGHT
	Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
	Upstream-Name: roxora-archive-keyring
	Source: https://github.com/roxora-os/apt

	Files: *
	Copyright: 2026 Ga Hing Woo
	License: public-domain
	 The public key that signs Roxora's apt repository. A public key is not
	 subject to copyright; it may be copied and used without restriction.
COPYRIGHT
cat > "${pkg}/DEBIAN/control" <<- CONTROL
	Package: roxora-archive-keyring
	Version: ${KEYRING_VERSION}
	Architecture: all
	Maintainer: Roxora Project <oss@gahingwoo.com>
	Section: misc
	Priority: optional
	Multi-Arch: foreign
	Homepage: https://github.com/roxora-os/apt
	Description: OpenPGP key of Roxora's apt repository
	 The public key that signs Roxora's apt repository at
	 https://roxora-os.github.io/apt, as the keyring file that its apt source
	 names in Signed-By: /usr/share/keyrings/roxora-archive-keyring.gpg.
	 Fingerprint ${REPO_KEY_FINGERPRINT}.
CONTROL
chmod -R u=rwX,go=rX "${pkg}"
find "${pkg}" -exec touch --no-dereference --date="@${KEYRING_EPOCH}" {} +
SOURCE_DATE_EPOCH="${KEYRING_EPOCH}" dpkg-deb --root-owner-group --build "${pkg}" \
	"${out}/roxora-archive-keyring_${KEYRING_VERSION}_all.deb" > /dev/null
install -m 0644 "${asc}" "${out}/roxora-archive-keyring.asc"
install -m 0644 "${pkg}/usr/share/keyrings/roxora-archive-keyring.gpg" "${out}/roxora-archive-keyring.gpg"
echo "${out}/roxora-archive-keyring_${KEYRING_VERSION}_all.deb"
