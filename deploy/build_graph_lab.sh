#!/usr/bin/env bash
# Build the pinned, reviewed Gephi Lite revision into a deployable archive.
set -euo pipefail

repo=https://github.com/gephi/gephi-lite.git
tag='@gephi/gephi-lite@1.0.2'
commit=d47ecb459a00e2942ee0c2b8d6630015124b9ff4
output_dir="${1:-$PWD/dist}"
build_root="$(mktemp -d)"
trap 'rm -rf "$build_root"' EXIT

git clone --depth 1 --branch "$tag" "$repo" "$build_root/source"
actual="$(git -C "$build_root/source" rev-parse HEAD)"
[[ "$actual" == "$commit" ]] || { echo "Unexpected Gephi Lite commit: $actual" >&2; exit 1; }

cd "$build_root/source"
export npm_config_cache="$build_root/npm-cache"
npm ci --legacy-peer-deps
# npm can omit native optional packages from workspace lockfiles (npm/cli#4828).
# Restore the exact lock-selected Rollup and SWC bindings without saving changes.
rollup_version="$(node -p "require('./node_modules/vite/node_modules/rollup/package.json').version")"
swc_version="$(node -p "require('./node_modules/@swc/core/package.json').version")"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) rollup_platform=darwin-arm64; swc_platform=darwin-arm64 ;;
  Darwin-x86_64) rollup_platform=darwin-x64; swc_platform=darwin-x64 ;;
  Linux-x86_64) rollup_platform=linux-x64-gnu; swc_platform=linux-x64-gnu ;;
  Linux-aarch64) rollup_platform=linux-arm64-gnu; swc_platform=linux-arm64-gnu ;;
  *) echo "Unsupported Graph Lab build platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
npm install --no-save --ignore-scripts --legacy-peer-deps \
  "@rollup/rollup-${rollup_platform}@${rollup_version}" \
  "@swc/core-${swc_platform}@${swc_version}" \
  graphology@0.25.4
BASE_URL=/gephi/ npm run build

# Keep the isolated UI self-hosted. Upstream CSS imports Google Fonts; removing
# those imports preserves its declared fallback stacks without third-party
# requests or a broader production CSP.
find packages/gephi-lite/build -type f -name '*.css' -exec \
  perl -0pi -e 's/\@import"https:\/\/fonts\.googleapis\.com\/[^"]+";//g' {} +
if grep -R -q 'fonts.googleapis.com' packages/gephi-lite/build; then
  echo "External Google Fonts import remains in the Graph Lab build." >&2
  exit 1
fi

mkdir -p "$output_dir"
artifact="$output_dir/gephi-lite-1.0.2.tar.gz"
COPYFILE_DISABLE=1 tar -czf "$artifact" -C packages/gephi-lite/build .
(cd "$output_dir" && sha256sum "$(basename "$artifact")" > "$(basename "$artifact").sha256")
echo "$artifact"
