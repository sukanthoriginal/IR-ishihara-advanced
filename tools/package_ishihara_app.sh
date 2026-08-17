#!/bin/zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
template_dir="${repo_dir}/tools/ishihara_app_template"
output_app="${1:-${HOME}/Desktop/IR Ishihara Simulator.app}"

if [[ -e "$output_app" ]]; then
  print -u2 "Refusing to overwrite existing path: ${output_app}"
  print -u2 "Move or rename it, or pass a different output path."
  exit 2
fi

for required_path in \
  "$template_dir/Info.plist" \
  "$template_dir/IR-Ishihara-Launcher" \
  "$repo_dir/server.py" \
  "$repo_dir/ishihara/index.html" \
  "$repo_dir/ishihara_stimuli/manifest.json"; do
  if [[ ! -f "$required_path" ]]; then
    print -u2 "Missing required file: ${required_path}"
    exit 1
  fi
done

build_app="$output_app"
build_complete=0
cleanup_incomplete_output() {
  if [[ "$build_complete" -ne 1 && -e "$build_app" ]]; then
    /bin/rm -rf "$build_app"
  fi
}
trap cleanup_incomplete_output EXIT

/bin/mkdir -p "$build_app/Contents/MacOS" "$build_app/Contents/Resources/runtime"
/bin/cp "$template_dir/Info.plist" "$build_app/Contents/Info.plist"
/bin/cp "$template_dir/IR-Ishihara-Launcher" \
  "$build_app/Contents/MacOS/IR-Ishihara-Launcher"
/bin/chmod +x "$build_app/Contents/MacOS/IR-Ishihara-Launcher"
/bin/cp "$repo_dir/server.py" "$build_app/Contents/Resources/runtime/server.py"
/bin/cp -R "$repo_dir/ishihara" "$repo_dir/ishihara_stimuli" \
  "$build_app/Contents/Resources/runtime/"

/usr/bin/codesign --force --deep --sign - "$build_app"
/usr/bin/codesign --verify --deep --strict "$build_app"
build_complete=1

print "Built self-contained launcher: ${output_app}"
