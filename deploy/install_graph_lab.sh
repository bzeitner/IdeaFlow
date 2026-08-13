#!/usr/bin/env bash
# Install a prebuilt, pinned Gephi Lite artifact plus IdeaFlow's isolated bridge.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: sudo $0 ARTIFACT CHECKSUM_FILE VERSION" >&2
  exit 2
fi

artifact="$(realpath "$1")"
checksum_file="$(realpath "$2")"
version="$3"
case "$version" in *[!A-Za-z0-9._-]*|'') echo "Unsafe version." >&2; exit 2;; esac
[[ -f "$artifact" && -f "$checksum_file" ]] || { echo "Artifact or checksum is missing." >&2; exit 2; }

cd "$(dirname "$artifact")"
sha256sum --check "$(basename "$checksum_file")"

install_root=/var/www/ideaflow-graph-lab
release_dir="$install_root/releases/$version"
[[ ! -e "$release_dir" ]] || { echo "Release already exists: $release_dir" >&2; exit 2; }
mkdir -p "$release_dir/gephi"

# Reject absolute paths and traversal before extracting an untrusted archive.
if tar -tzf "$artifact" | awk 'BEGIN{bad=0} /^\// || /(^|\/)\.\.($|\/)/ {bad=1} END{exit bad ? 0 : 1}'; then
  echo "Archive contains an unsafe path." >&2
  exit 2
fi
tar -xzf "$artifact" --no-same-owner --no-same-permissions -C "$release_dir/gephi"
[[ -f "$release_dir/gephi/index.html" ]] || { echo "Artifact has no top-level index.html." >&2; exit 2; }

bridge_dir="$(cd "$(dirname "$0")/graph-lab" && pwd)"
install -m 0644 "$bridge_dir/index.html" "$release_dir/index.html"
install -m 0644 "$bridge_dir/config.js" "$release_dir/config.js"
install -m 0644 "$bridge_dir/bridge.js" "$release_dir/bridge.js"
install -m 0644 "$bridge_dir/bridge.css" "$release_dir/bridge.css"
chown -R root:www-data "$release_dir"
find "$release_dir" -type d -exec chmod 0755 {} +
find "$release_dir" -type f -exec chmod 0644 {} +
ln -sfn "$release_dir" "$install_root/current"
nginx -t
systemctl reload nginx
echo "Installed Graph Lab $version at $release_dir"
