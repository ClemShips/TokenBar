#!/usr/bin/env python3
import json
import logging
import os
import sys
import glob
import subprocess
import threading
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger("tokenbar")

import objc
from Foundation import NSObject, NSURL, NSTimer
from AppKit import (
    NSApplication, NSStatusBar, NSVariableStatusItemLength,
    NSPopover, NSPopoverBehaviorTransient,
    NSViewController, NSMakeSize, NSMakeRect, NSRectEdgeMinY,
)
from WebKit import WKWebView, WKWebViewConfiguration

CLAUDE_DIR = os.path.expanduser("~/.claude")

PRICING = {
    "input":        3.00 / 1_000_000,
    "output":      15.00 / 1_000_000,
    "cache_read":   0.30 / 1_000_000,
    "cache_create": 3.75 / 1_000_000,
}

SHORT_NAMES = {
    "claude-sonnet-4-6":          "Sonnet 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-opus-4-5-20251101":   "Opus 4.5",
    "claude-opus-4-6":            "Opus 4.6",
    "claude-haiku-4-5-20251001":  "Haiku 4.5",
}

NODE_BIN     = "/usr/local/bin/node"
SECURITY_BIN = "/usr/bin/security"

FETCH_SCRIPT = """
const r = await fetch('https://claude.ai/api/oauth/usage', {
  headers: {
    'Authorization': `Bearer ${process.env.CLAUDE_TOKEN}`,
    'Content-Type': 'application/json',
    'anthropic-beta': 'oauth-2025-04-20',
    'User-Agent': 'Claude-Code/2.1.77'
  }
});
process.stdout.write(await r.text());
"""

MONTHS_FR = ["", "jan.", "fév.", "mars", "avr.", "mai", "juin",
             "juil.", "août", "sep.", "oct.", "nov.", "déc."]


def short_model(name):
    for k, v in SHORT_NAMES.items():
        if k in name:
            return v
    return name[:16]


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def get_oauth_token():
    result = subprocess.run(
        [SECURITY_BIN, "find-generic-password", "-s", "Claude Code-credentials", "-g"],
        capture_output=True, text=True
    )
    output = result.stdout + result.stderr
    if "password:" not in output:
        return None
    raw = output.split('password: "')[1].rstrip('"\n')
    data = json.loads(raw)
    return data.get("claudeAiOauth", {}).get("accessToken")


def fetch_live_usage():
    token = get_oauth_token()
    if not token:
        return None
    try:
        env = {
            "HOME":         os.environ.get("HOME", ""),
            "USER":         os.environ.get("USER", ""),
            "TMPDIR":       os.environ.get("TMPDIR", "/tmp"),
            "PATH":         "/usr/local/bin:/usr/bin:/bin",
            "CLAUDE_TOKEN": token,
        }
        result = subprocess.run(
            [NODE_BIN, "--input-type=module"],
            input=FETCH_SCRIPT,
            capture_output=True, text=True, timeout=8,
            env=env
        )
        return json.loads(result.stdout)
    except Exception as e:
        log.warning("fetch_live_usage failed: %s", e)
        return None


def empty_usage():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            "messages": 0, "cost": 0.0, "by_model": {}}


def add_message_to(bucket, usage, model):
    bucket["input"]        += usage.get("input_tokens", 0)
    bucket["output"]       += usage.get("output_tokens", 0)
    bucket["cache_read"]   += usage.get("cache_read_input_tokens", 0)
    bucket["cache_create"] += usage.get("cache_creation_input_tokens", 0)
    bucket["messages"]     += 1
    bucket["cost"] += (
        usage.get("input_tokens", 0)                * PRICING["input"] +
        usage.get("output_tokens", 0)               * PRICING["output"] +
        usage.get("cache_read_input_tokens", 0)     * PRICING["cache_read"] +
        usage.get("cache_creation_input_tokens", 0) * PRICING["cache_create"]
    )
    m = short_model(model)
    bucket["by_model"][m] = bucket["by_model"].get(m, 0) + usage.get("output_tokens", 0)


def parse_sessions():
    now         = datetime.now(timezone.utc)
    local_now   = datetime.now()
    today_start = datetime.combine(local_now.date(), datetime.min.time()).astimezone(timezone.utc)
    month_start = today_start.replace(day=1)
    window_7d   = now - timedelta(days=7)
    cutoff_mtime = (now - timedelta(days=32)).timestamp()

    buckets = {"today": empty_usage(), "7d": empty_usage(), "month": empty_usage()}
    skipped_lines = 0

    for path in glob.glob(os.path.join(CLAUDE_DIR, "projects", "**", "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff_mtime:
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get("type") != "assistant":
                            continue
                        ts_str = d.get("timestamp")
                        if not ts_str:
                            continue
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts < window_7d:
                            continue
                        usage = d.get("message", {}).get("usage", {})
                        model = d.get("message", {}).get("model", "unknown")
                        add_message_to(buckets["7d"], usage, model)
                        if ts >= today_start:
                            add_message_to(buckets["today"], usage, model)
                        if ts >= month_start:
                            add_message_to(buckets["month"], usage, model)
                    except Exception:
                        skipped_lines += 1
        except Exception as e:
            log.warning("parse_sessions: cannot read %s: %s", path, e)

    if skipped_lines:
        log.debug("parse_sessions: skipped %d malformed lines", skipped_lines)
    return buckets


def parse_history():
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    msgs = out = cost = 0
    skipped_lines = 0

    for path in glob.glob(os.path.join(CLAUDE_DIR, "projects", "**", "*.jsonl"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get("type") != "assistant":
                            continue
                        ts = datetime.fromisoformat(
                            d.get("timestamp", "").replace("Z", "+00:00")
                        )
                        if ts < cutoff:
                            continue
                        u   = d.get("message", {}).get("usage", {})
                        inp = u.get("input_tokens", 0)
                        o   = u.get("output_tokens", 0)
                        cr  = u.get("cache_read_input_tokens", 0)
                        cc  = u.get("cache_creation_input_tokens", 0)
                        msgs += 1
                        out  += o
                        cost += (inp * PRICING["input"]       + o  * PRICING["output"] +
                                 cr  * PRICING["cache_read"]  + cc * PRICING["cache_create"])
                    except Exception:
                        skipped_lines += 1
        except Exception as e:
            log.warning("parse_history: cannot read %s: %s", path, e)

    if skipped_lines:
        log.debug("parse_history: skipped %d malformed lines", skipped_lines)
    return {"msgs": msgs, "out": out, "cost": cost}


class ActionHandler(NSObject):
    delegate = None

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        action_type = body.get("type") if isinstance(body, dict) else body
        if action_type == "quit":
            NSApplication.sharedApplication().terminate_(None)
        elif action_type == "refresh":
            if self.delegate:
                self.delegate.start_refresh()


class AppDelegate(NSObject):

    def applicationDidFinishLaunching_(self, notification):
        self._cached_data = None
        self._webview = None
        self._popover = None

        self._setup_status_item()
        self._setup_webview()
        self._setup_popover()
        self.start_refresh()

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            60.0, self,
            objc.selector(self.timerFired_, signature=b'v@:@'),
            None, True
        )

    def _setup_status_item(self):
        bar = NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        btn = self._status_item.button()
        btn.setTitle_("◆ …")
        btn.setAction_(objc.selector(self.togglePopover_, signature=b'v@:@'))
        btn.setTarget_(self)

    def _setup_webview(self):
        config = WKWebViewConfiguration.alloc().init()

        handler = ActionHandler.alloc().init()
        handler.delegate = self
        config.userContentController().addScriptMessageHandler_name_(handler, "action")
        self._handler = handler

        self._webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, 340, 620), config
        )

        if getattr(sys, "frozen", False):
            res = os.path.join(os.path.dirname(sys.executable), "..", "Resources")
        else:
            res = os.path.dirname(os.path.abspath(__file__))

        html_path = os.path.join(res, "ui", "index.html")
        file_url  = NSURL.fileURLWithPath_(html_path)
        base_url  = NSURL.fileURLWithPath_(os.path.dirname(html_path) + "/")
        self._webview.loadFileURL_allowingReadAccessToURL_(file_url, base_url)

    def _setup_popover(self):
        self._popover = NSPopover.alloc().init()
        self._popover.setContentSize_(NSMakeSize(340, 620))
        self._popover.setBehavior_(NSPopoverBehaviorTransient)

        vc = NSViewController.alloc().init()
        vc.setView_(self._webview)
        self._popover.setContentViewController_(vc)

    def togglePopover_(self, sender):
        if self._popover.isShown():
            self._popover.performClose_(sender)
        else:
            btn = self._status_item.button()
            self._popover.showRelativeToRect_ofView_preferredEdge_(
                btn.bounds(), btn, NSRectEdgeMinY
            )
            if self._cached_data:
                self._push_data_to_webview(self._cached_data)

    def timerFired_(self, timer):
        self.start_refresh()

    def start_refresh(self):
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        live    = fetch_live_usage()
        local   = parse_sessions()
        history = parse_history()

        data = {
            "live":    live,
            "today":   local["today"],
            "7d":      local["7d"],
            "month":   local["month"],
            "history": history,
        }
        self._cached_data = data

        fh_pct = (live or {}).get("five_hour", {}).get("utilization")
        if fh_pct is not None:
            title = f"◆ {round(fh_pct)}%"
        else:
            title = f"◆ {fmt_tokens(local['today']['output'])}"
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(self.updateTitle_, signature=b'v@:@'),
            title, False
        )

        if self._popover.isShown():
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                objc.selector(self.updateWebview_, signature=b'v@:@'),
                json.dumps(data), False
            )

    def updateTitle_(self, title):
        self._status_item.button().setTitle_(title)

    def updateWebview_(self, json_str):
        self._push_data_to_webview(json.loads(json_str))

    def _push_data_to_webview(self, data):
        js = f"if(window.updateData) window.updateData({json.dumps(data)})"
        self._webview.evaluateJavaScript_completionHandler_(js, None)


if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()
