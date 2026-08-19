#!/bin/zsh
set -euo pipefail

repo_dir="${0:A:h:h}"
template_dir="${repo_dir}/tools/advanced_app_template"
output_app="${1:-${HOME}/Desktop/Advanced IR Ishihara.app}"
python_bin="${ADVANCED_ISHIHARA_PYTHON:-$(command -v python3)}"
node_bin="${NODE_BIN:-$(command -v node)}"

if [[ -e "$output_app" ]]; then
  print -u2 "Refusing to overwrite existing path: ${output_app}"
  exit 2
fi
if ! "$python_bin" -c 'import numpy; import PIL' >/dev/null 2>&1; then
  print -u2 "Python lacks NumPy/Pillow: ${python_bin}"
  print -u2 "Set ADVANCED_ISHIHARA_PYTHON to a compatible executable."
  exit 1
fi

for required_path in \
  "$template_dir/Info.plist" \
  "$template_dir/Advanced-Ishihara-Launcher" \
  "$repo_dir/server.py" \
  "$repo_dir/advanced/index.html" \
  "$repo_dir/advanced_ishihara/grammar.mjs"; do
  if [[ ! -f "$required_path" ]]; then
    print -u2 "Missing required file: ${required_path}"
    exit 1
  fi
done

build_complete=0
cleanup_incomplete_output() {
  if [[ "$build_complete" -ne 1 && -e "$output_app" ]]; then
    /bin/rm -rf "$output_app"
  fi
}
trap cleanup_incomplete_output EXIT

/bin/mkdir -p "$output_app/Contents/MacOS" "$output_app/Contents/Resources/runtime"
/bin/cp "$template_dir/Info.plist" "$output_app/Contents/Info.plist"
/usr/bin/sed "s|__PYTHON_BIN__|${python_bin}|g" \
  "$template_dir/Advanced-Ishihara-Launcher" \
  > "$output_app/Contents/MacOS/Advanced-Ishihara-Launcher"
/bin/chmod +x "$output_app/Contents/MacOS/Advanced-Ishihara-Launcher"

runtime_dir="$output_app/Contents/Resources/runtime"
/bin/cp "$repo_dir/server.py" "$repo_dir/requirements.txt" "$runtime_dir/"
/bin/cp -R "$repo_dir/advanced" "$repo_dir/advanced_ishihara" "$repo_dir/shared" "$runtime_dir/"
/bin/mkdir -p "$runtime_dir/tools"
/bin/cp "$repo_dir/tools/export_advanced_catalog.mjs" "$runtime_dir/tools/"
"$node_bin" "$repo_dir/tools/export_advanced_catalog.mjs" --format=grammar \
  > "$runtime_dir/advanced_ishihara/grammar_snapshot.json"

if [[ -n "${RASPIVOICE_BIN:-}" ]]; then
  raspivoice_bin="$RASPIVOICE_BIN"
elif [[ -x "${repo_dir:h}/IR-vOICe/raspivoice/Release/raspivoice" ]]; then
  raspivoice_bin="${repo_dir:h}/IR-vOICe/raspivoice/Release/raspivoice"
else
  raspivoice_bin="${HOME}/Dev/Lossfunk/IR-vOICe/raspivoice/Release/raspivoice"
fi
if [[ -x "$raspivoice_bin" ]]; then
  /bin/mkdir -p "$runtime_dir/bin"
  /bin/cp "$raspivoice_bin" "$runtime_dir/bin/raspivoice"
  /bin/chmod +x "$runtime_dir/bin/raspivoice"
else
  print "No raspivoice binary bundled; visual-only mode will still work."
fi

/usr/bin/find "$runtime_dir" -name '__pycache__' -type d -prune -exec /bin/rm -rf {} +
/usr/bin/codesign --force --deep --sign - "$output_app"
/usr/bin/codesign --verify --deep --strict "$output_app"
build_complete=1
print "Built Advanced IR Ishihara launcher: ${output_app}"
