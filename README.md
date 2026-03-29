# TokenBar

A macOS menubar app that displays your Claude Code token usage in real time.

![macOS](https://img.shields.io/badge/macOS-000000?style=flat&logo=apple&logoColor=white)
![Python](https://img.shields.io/badge/Python_3-3776AB?style=flat&logo=python&logoColor=white)

## Screenshot

![TokenBar popover](assets/screenshot.png)

## Features

- **5-hour window** — Progress bar with time remaining before reset (via Claude OAuth API)
- **Today** — Messages, input/output/cache tokens, estimated cost, per-model breakdown with tabs
- **This month** — Messages, output tokens, estimated cost
- **Since Jan. 2026** — Total historical usage
- **Velocity estimation** — ETA to limit when above 50%, day-over-day comparison
- **Native notifications** — macOS alerts at 80%/95% with sound, click to open popover
- **Dark/light mode** — Automatic via `prefers-color-scheme`, compact/detailed toggle
- **Right-click menu** — Copy stats, dashboard, preferences, about
- **i18n** — French/English auto-detected from system language
- **Auto-update check** — Checks GitHub Releases once/day, banner in popover if update available
- **Auto-refresh** every 60 seconds

## Requirements

- macOS
- Python 3
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Node.js (for OAuth API calls)

## Installation

### Homebrew (recommended)

```bash
brew tap clemships/tokenbar
brew install --cask tokenbar
```

### Manual

Download `TokenBar-v1.0.0.zip` from the [latest release](https://github.com/ClemShips/TokenBar/releases/latest), unzip, and drag `TokenBar.app` to `/Applications`.

### From source

```bash
git clone https://github.com/ClemShips/TokenBar.git
cd TokenBar
pip install pyobjc-framework-WebKit py2app
```

**Run in dev mode**

```bash
python3 tokenbar.py
```

**Build as macOS app**

```bash
python setup.py py2app
```

The built app will be in `dist/TokenBar.app`.

## Configuration

TokenBar reads `~/.config/tokenbar/config.json` (created via right-click → Preferences). Available options:

| Key | Default | Description |
|-----|---------|-------------|
| `refresh_interval` | `60` | Refresh interval in seconds |
| `currency` | `"$"` | Currency symbol |
| `menubar_display` | `"percent"` | `percent` / `cost_today` / `tokens_today` |
| `alert_thresholds` | `[80, 95]` | Notification thresholds (%) |
| `alert_sound` | `"Glass"` | macOS system sound name |
| `pricing` | Sonnet defaults | Per-token pricing overrides |

## How it works

TokenBar reads Claude Code's local JSONL session files (`~/.claude/projects/`) to compute token usage stats. It also fetches live rate-limit data from the Claude OAuth API using credentials stored in the macOS Keychain.

The UI is a native macOS popover rendered with WKWebView.

## License

MIT
