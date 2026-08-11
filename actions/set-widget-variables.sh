#!/usr/bin/env zsh

set -euo pipefail

/usr/bin/osascript <<'APPLESCRIPT'
tell application "BetterTouchTool"
    set_persistent_string_variable "BTT_WIDGET_CLASH_LATENCY_UUID" to "59F8C568-022F-4BD9-B3EB-63A7676592DF"
    set_persistent_string_variable "BTT_WIDGET_CLASH_REGION_UUID" to "CF76E4C0-5986-41F9-8F3E-00A6C8F160FE"
    set_persistent_string_variable "BTT_WIDGET_CLAUDE_UUID" to "8C95B746-77DA-4B76-A966-6EBB10E755F4"
    set_persistent_string_variable "BTT_WIDGET_CODEX_UUID" to "508ECCA7-BAD5-469D-9418-94E2C370AE37"
    set_persistent_string_variable "BTT_WIDGET_DATE_TIME_UUID" to "0E55B8EC-C241-467A-A341-A1780D95047E"
    set_persistent_string_variable "BTT_WIDGET_LYRICS_UUID" to "E19BB023-5060-4A56-95C8-6E7402779870"
    set_persistent_string_variable "BTT_WIDGET_NOW_PLAYING_UUID" to "710F54C5-25B0-4A2C-B960-D9C0FE78B1B7"
    set_persistent_string_variable "BTT_WIDGET_OPENCODE_UUID" to "AE01C9E2-9EC6-4329-8358-8389BFB850F8"
    set_persistent_string_variable "BTT_WIDGET_STAR_UUID" to "A05C5D37-7EAA-4F7B-AC00-23183CC8C6A1"
    set_persistent_string_variable "BTT_WIDGET_TIMER_UUID" to "E25C395A-FE13-4216-BC59-6317FD0454BF"
    set_persistent_string_variable "BTT_WIDGET_WEATHER_ICON_UUID" to "E953195F-2D7C-4AAA-B288-3DFB4DC1DA0A"
    set_persistent_string_variable "BTT_WIDGET_WEATHER_UUID" to "6C52DA31-FC05-495E-847D-C51B9AD9E33C"
end tell
APPLESCRIPT