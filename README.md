# TokenBar

A macOS menubar app that displays your Claude Code token usage in real time.

![macOS](https://img.shields.io/badge/macOS-000000?style=flat&logo=apple&logoColor=white)
![Python](https://img.shields.io/badge/Python_3-3776AB?style=flat&logo=python&logoColor=white)

## Features

- **5-hour window** — Progress bar with time remaining before reset (via Claude OAuth API)
- **Today** — Messages, input/output/cache tokens, estimated cost, models used
- **Last 7 days** — Messages, output tokens, estimated cost
- **This month** — Messages, output tokens, estimated cost
- **Since Jan. 2026** — Total historical usage
- **Auto-refresh** every 60 seconds

## Screenshot

> Coming soon

## Requirements

- macOS
- Python 3
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Node.js (for OAuth API calls)

## Installation

```bash
git clone https://github.com/ClemShips/TokenBar.git
cd TokenBar
pip install pyobjc-framework-WebKit py2app
```

### Run in dev mode

```bash
python tokenbar.py
```

### Build as macOS app

```bash
python setup.py py2app
```

The built app will be in `dist/TokenBar.app`.

## How it works

TokenBar reads Claude Code's local JSONL session files (`~/.claude/projects/`) to compute token usage stats. It also fetches live rate-limit data from the Claude OAuth API using credentials stored in the macOS Keychain.

The UI is a native macOS popover rendered with WKWebView.

## License

MIT
