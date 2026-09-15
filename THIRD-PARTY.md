# Third-party notices

This repository is a BetterTouchTool configuration. It does not vendor any
third-party code, but it does embed third-party trademarks, call third-party
services at runtime, and depend on software it does not ship. The non-commercial
grant in [`LICENSE`](LICENSE) covers **this repository's own code only**.

## Trademarks in the preset

`bttpreset/Default.bttpreset` embeds product icons as base64 `BTTIconData` for
the widgets that display them. Those marks belong to their owners and are used
here for identification only, with no affiliation or endorsement implied:

| Mark | Owner |
| --- | --- |
| Claude | Anthropic PBC |
| Codex | OpenAI |
| OpenCode | the OpenCode project |

Swap or delete those icons in the preset before redistributing it. `assets/`
holds no third-party artwork.

## Services queried at runtime

Nothing here caches or redistributes third-party content; every provider is
called live and its answer is used in place.

| Service | Used for | Terms to observe |
| --- | --- | --- |
| QWeather (和风天气) | Weather fallback | Requires your own API key, and **attribution is required by their terms**. The widget writes the provider name into its trace for that reason. Free tier is 50k requests/month; usage is ~4.4k/month at the default 600 s refresh. |
| Open-Meteo | Weather fallback | No key. Non-commercial use per their free-tier terms. |
| Apple Weather | Weather, via a Shortcut you author | Governed by Apple's terms; no key or credential ships here. |
| LRCLIB, LrcAPI | Lyric lookup | Community APIs with no SLA. Respect their rate limits; the widgets already bound concurrency and retries. |
| NetEase Cloud Music (网易云音乐), QQ Music | Lyric lookup | Lyric text is copyrighted by the respective rights holders and their licensors. Fetched for personal display on your own machine — do not redistribute what you fetch. |

Provider facts and the reasons each one is ordered where it is live in
[`specs/reference/PROVIDERS.md`](specs/reference/PROVIDERS.md).

## Software you install yourself

None of these are bundled, and each carries its own license:

| Dependency | Role |
| --- | --- |
| [BetterTouchTool](https://folivora.ai) | Commercial application this config drives. Not affiliated with this repository. |
| `nowplaying-cli` | Reads the Now Playing dictionary. |
| `codexbar` | Supplies the quota widgets' usage records. |
| `jq` | JSON handling in the shell widgets. |

## Platform frameworks

- `actions/nowplaying-state.m` is compiled by **you**, against the private
  `MediaRemote` framework that ships with macOS. No Apple headers or binaries
  are redistributed here; the source exists so you can build the helper
  locally. See the compile line in that file.
- The Lyrics widget reads Apple Music's own cached TTML and Apple Music
  playback state through AppleScript and MediaRemote on your machine.

## Not affiliated

This is an independent, personal configuration. It is not affiliated with,
endorsed by, or supported by Folivora (BetterTouchTool), Apple, Anthropic,
OpenAI, OpenCode, QWeather, NetEase, Tencent, or the LRCLIB and LrcAPI
maintainers.
