#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (c) 2026 Ga Hing Woo <gahing@gahingwoo.com>
#
# Build the release packages (roxora-release and the flavor packages) of
# Roxora's releases.
#
#   scripts/fetch-release-packages.sh <output directory> <published list> [<ref>]
#
# For every release of BUILD_REPO (config/repo.conf) that is neither a draft
# nor a pre-release, checks out its tag and runs the build repository's own
# userpatches/roxora/packages/build.sh once per suite, into
# <output directory>/<suite>/. Releases from before the release packages
# (no such script at the tag) are skipped, and so are releases whose
# roxora-release the repository already publishes: <published list> is the
# output of "scripts/assemble.py --list". The tag's ROXORA_RELEASE must be
# the release's version.
#
# With <ref>, builds that branch, tag or commit of the build repository
# instead, whatever its version; for a test run that is not deployed.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOP="$(dirname "${HERE}")"
out="${1:?usage: $0 <output directory> <published list> [<ref>]}"
published="${2:?usage: $0 <output directory> <published list> [<ref>]}"
only_ref="${3:-}"
mkdir -p "${out}"
out="$(cd "${out}" && pwd)"

declare BUILD_REPO REPO_SUITES
# shellcheck source=config/repo.conf
source "${TOP}/config/repo.conf"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# build <ref> <expected version, or empty for any>
build() {
	declare ref="$1" want="$2" src="${work}/src"
	rm -rf "${src}"
	git init -q "${src}"
	git -C "${src}" fetch -q --depth 1 "https://github.com/${BUILD_REPO}.git" "${ref}"
	git -C "${src}" -c advice.detachedHead=false checkout -q FETCH_HEAD
	declare script="${src}/userpatches/roxora/packages/build.sh"
	if [[ ! -x "${script}" ]]; then
		echo "${ref}: no release packages (no userpatches/roxora/packages/build.sh)"
		return 0
	fi
	declare version
	version="$(sed -n 's/^ROXORA_RELEASE=//p' "${src}/userpatches/roxora/roxora-repo.conf")"
	if [[ -n "${want}" && "${version}" != "${want}" ]]; then
		echo "fetch-release-packages.sh: ${ref} builds release packages ${version}, not ${want}" >&2
		return 1
	fi
	declare suite
	for suite in ${REPO_SUITES}; do
		if grep -q "^${suite} roxora-release ${version}+" "${published}"; then
			echo "${ref}: roxora-release ${version} for ${suite} is published already"
			continue
		fi
		echo "${ref}: building the release packages ${version} for ${suite} ($(git -C "${src}" rev-parse HEAD))"
		"${script}" --suite "${suite}" --out "${out}/${suite}"
	done
}

if [[ -n "${only_ref}" ]]; then
	build "${only_ref}" ""
	exit 0
fi

curl_args=(--fail --silent --show-error --location -H "Accept: application/vnd.github+json")
[[ -n "${GITHUB_TOKEN:-}" ]] && curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
curl "${curl_args[@]}" "https://api.github.com/repos/${BUILD_REPO}/releases?per_page=100" > "${work}/releases.json"
python3 -c '
import json, sys
for r in json.load(open(sys.argv[1])):
    if not r["draft"] and not r["prerelease"]:
        print(r["tag_name"])
' "${work}/releases.json" > "${work}/tags"
while read -r tag; do
	[[ "${tag}" =~ ^v([0-9]{4}\.[0-9]{2}\.[0-9]+)$ ]] || continue
	declare v="${BASH_REMATCH[1]}"
	# Every suite published already: nothing to check out.
	declare missing=no suite
	for suite in ${REPO_SUITES}; do
		grep -q "^${suite} roxora-release ${v}+" "${published}" || missing=yes
	done
	[[ "${missing}" == yes ]] || continue
	build "refs/tags/${tag}" "${v}"
done < "${work}/tags"
