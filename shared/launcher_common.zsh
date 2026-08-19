#!/bin/zsh

show_launcher_error() {
  /usr/bin/osascript -l AppleScript \
    -e 'on run argv' \
    -e 'display dialog (item 1 of argv) buttons {"OK"} default button "OK" with icon stop' \
    -e 'end run' \
    -- "$1"
}

wait_for_page() {
  local page_url="$1"
  local expected_text="$2"
  local attempts="${3:-40}"
  local page
  local _attempt=1
  while [[ "$_attempt" -le "$attempts" ]]; do
    page="$(/usr/bin/curl -fsS --max-time 1 "$page_url" 2>/dev/null)" || true
    if [[ "$page" == *"$expected_text"* ]]; then
      return 0
    fi
    /bin/sleep 0.25
    _attempt=$((_attempt + 1))
  done
  return 1
}
