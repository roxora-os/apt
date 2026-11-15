#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
#
# Build roxora-wallpapers from roxora-os/design at the commit in
# wallpapers.conf.
#
#   packages/wallpapers/build.sh <output directory>
#
# Writes roxora-wallpapers_<version>_all.deb. Every file from the design
# repository is checked against its SHA-256 in wallpapers.conf, and every
# file in the package gets the time stamp WALLPAPERS_EPOCH, so a rebuild
# gives the same files.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${1:?usage: $0 <output directory>}"
mkdir -p "${out}"
out="$(cd "${out}" && pwd)"

declare WALLPAPERS_VERSION WALLPAPERS_DESIGN_COMMIT WALLPAPERS_EPOCH
declare -a WALLPAPERS_FILES=()
# shellcheck source=packages/wallpapers/wallpapers.conf
source "${HERE}/wallpapers.conf"
version="${WALLPAPERS_VERSION}+git${WALLPAPERS_DESIGN_COMMIT:0:7}"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
pkg="${work}/pkg"
mkdir -p "${pkg}/DEBIAN" "${pkg}/usr/share/backgrounds/roxora" "${pkg}/usr/share/doc/roxora-wallpapers" \
	"${pkg}/usr/share/gnome-background-properties" "${pkg}/usr/share/glib-2.0/schemas"

declare entry file sum
for entry in "${WALLPAPERS_FILES[@]}"; do
	read -r file sum <<< "${entry}"
	curl --fail --silent --show-error --location --retry 3 -o "${work}/${file}" \
		"https://raw.githubusercontent.com/roxora-os/design/${WALLPAPERS_DESIGN_COMMIT}/wallpapers/${file}"
	echo "${sum}  ${work}/${file}" | sha256sum --check --status || {
		echo "build.sh: design ${WALLPAPERS_DESIGN_COMMIT} wallpapers/${file} does not match wallpapers.conf" >&2
		exit 1
	}
	[[ "${file}" == *.png ]] && install -m 0644 "${work}/${file}" "${pkg}/usr/share/backgrounds/roxora/${file}"
done
install -m 0644 "${HERE}/roxora-wallpapers.xml" "${pkg}/usr/share/gnome-background-properties/roxora-wallpapers.xml"
install -m 0644 "${HERE}/90_roxora-wallpapers.gschema.override" \
	"${pkg}/usr/share/glib-2.0/schemas/90_roxora-wallpapers.gschema.override"
{
	printf '%s\n' "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/" \
		"Upstream-Name: Roxora wallpapers" \
		"Source: https://github.com/roxora-os/design/tree/${WALLPAPERS_DESIGN_COMMIT}/wallpapers" "" \
		"Files: *" "Copyright: 2026 DavidQyn" "           2026 Ga Hing Woo" "License: CC-BY-SA-4.0" "" \
		"License: CC-BY-SA-4.0"
	sed -e 's/^$/./' -e 's/^/ /' "${work}/LICENSE"
} > "${pkg}/usr/share/doc/roxora-wallpapers/copyright"
cat > "${pkg}/DEBIAN/control" <<- CONTROL
	Package: roxora-wallpapers
	Version: ${version}
	Architecture: all
	Maintainer: Roxora Project <oss@gahingwoo.com>
	Section: graphics
	Priority: optional
	Homepage: https://github.com/roxora-os/design
	Description: Roxora desktop wallpapers
	 Four wallpapers in light and dark versions for the GNOME background list,
	 with Roxora Facet as the default wallpaper. By DavidQyn and Ga Hing Woo,
	 CC BY-SA 4.0.
CONTROL
chmod -R u=rwX,go=rX "${pkg}"
find "${pkg}" -exec touch --no-dereference --date="@${WALLPAPERS_EPOCH}" {} +
SOURCE_DATE_EPOCH="${WALLPAPERS_EPOCH}" dpkg-deb --root-owner-group --build "${pkg}" \
	"${out}/roxora-wallpapers_${version}_all.deb" > /dev/null
echo "${out}/roxora-wallpapers_${version}_all.deb"
