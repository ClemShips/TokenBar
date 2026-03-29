#!/usr/bin/env python3
import json
import logging
import os
import shutil
import sys
import glob
import subprocess
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

import re


class TokenRedactFilter(logging.Filter):
    _pattern = re.compile(r'(sk-ant-[A-Za-z0-9_-]{8})[A-Za-z0-9_-]*')

    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = self._pattern.sub(r'\1…REDACTED', record.msg)
        if record.args:
            record.args = tuple(
                self._pattern.sub(r'\1…REDACTED', str(a)) if isinstance(a, str) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger("tokenbar")
log.addFilter(TokenRedactFilter())

import objc
from Foundation import NSObject, NSURL, NSTimer, NSLocale, NSBundle
from AppKit import (
    NSApplication, NSStatusBar, NSVariableStatusItemLength,
    NSPopover, NSPopoverBehaviorTransient,
    NSViewController, NSMakeSize, NSMakeRect, NSRectEdgeMinY,
    NSSound,
    NSMenu, NSMenuItem,
    NSPasteboard, NSPasteboardTypeString,
)
from WebKit import WKWebView, WKWebViewConfiguration

try:
    objc.loadBundle(
        "UserNotifications",
        bundle_path="/System/Library/Frameworks/UserNotifications.framework",
        module_globals=globals(),
    )
    UNUserNotificationCenter = objc.lookUpClass("UNUserNotificationCenter")
    UNMutableNotificationContent = objc.lookUpClass("UNMutableNotificationContent")
    UNNotificationRequest = objc.lookUpClass("UNNotificationRequest")
    UNNotificationSound = objc.lookUpClass("UNNotificationSound")

    objc.registerMetaDataForSelector(
        b"UNUserNotificationCenter",
        b"requestAuthorizationWithOptions:completionHandler:",
        {
            "arguments": {
                3: {
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {
                            0: {"type": b"^v"},
                            1: {"type": b"Z"},
                            2: {"type": b"@"},
                        },
                    }
                }
            }
        },
    )

    objc.registerMetaDataForSelector(
        b"UNUserNotificationCenter",
        b"addNotificationRequest:withCompletionHandler:",
        {
            "arguments": {
                3: {
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {
                            0: {"type": b"^v"},
                            1: {"type": b"@"},
                        },
                    }
                }
            }
        },
    )

    _app_delegate_ref = None

    class _UNDelegate(NSObject):
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, handler):
            handler(0x07)

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, handler):
            if _app_delegate_ref and hasattr(_app_delegate_ref, 'togglePopover_'):
                _app_delegate_ref.togglePopover_(None)
            handler()

    objc.registerMetaDataForSelector(
        b"_UNDelegate",
        b"userNotificationCenter:willPresentNotification:withCompletionHandler:",
        {
            "arguments": {
                4: {
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {
                            0: {"type": b"^v"},
                            1: {"type": b"Q"},
                        },
                    }
                }
            }
        },
    )

    objc.registerMetaDataForSelector(
        b"_UNDelegate",
        b"userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:",
        {
            "arguments": {
                4: {
                    "callable": {
                        "retval": {"type": b"v"},
                        "arguments": {
                            0: {"type": b"^v"},
                        },
                    }
                }
            }
        },
    )

    _un_delegate = _UNDelegate.alloc().init()
    _HAS_UN = True
except Exception as e:
    _HAS_UN = False
    _un_delegate = None
    log.warning("UserNotifications framework not available: %s", e)

CLAUDE_DIR   = os.path.expanduser("~/.claude")
CACHE_DIR    = os.path.expanduser("~/.config/tokenbar")
CACHE_FILE   = os.path.join(CACHE_DIR, "cache.json")
CONFIG_FILE  = os.path.join(CACHE_DIR, "config.json")

DEFAULT_CONFIG = {
    "refresh_interval": 60,
    "currency": "$",
    "menubar_display": "percent",
    "alert_thresholds": [80, 95],
    "alert_sound": "Glass",
    "pricing": {
        "input":        3.00 / 1_000_000,
        "output":      15.00 / 1_000_000,
        "cache_read":   0.30 / 1_000_000,
        "cache_create": 3.75 / 1_000_000,
    },
}

PRICING = DEFAULT_CONFIG["pricing"].copy()


def load_config():
    config = DEFAULT_CONFIG.copy()
    config["pricing"] = DEFAULT_CONFIG["pricing"].copy()
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user.get("pricing"), dict):
            config["pricing"].update(user["pricing"])
            user.pop("pricing")
        config.update(user)
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, TypeError) as e:
        log.warning("Invalid config file, using defaults: %s", e)
    return config

MENU_STRINGS = {
    "fr": {
        "copy_stats":  "Copier les stats",
        "dashboard":   "Dashboard Anthropic",
        "preferences": "Préférences...",
        "about":       "À propos...",
        "quit":        "Quitter",
    },
    "en": {
        "copy_stats":  "Copy stats",
        "dashboard":   "Anthropic Dashboard",
        "preferences": "Preferences...",
        "about":       "About...",
        "quit":        "Quit",
    },
}

APP_VERSION          = "1.0.0"
GITHUB_URL           = "https://github.com/ClemShips/TokenBar"
GITHUB_API_LATEST    = "https://api.github.com/repos/ClemShips/TokenBar/releases/latest"
UPDATE_CHECK_HOURS   = 24


def _version_tuple(v):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except Exception:
        return (0,)


def check_for_update(cache):
    now = datetime.now(timezone.utc)
    last_str = cache.get("last_update_check")
    if last_str:
        try:
            last = datetime.fromisoformat(last_str)
            if (now - last).total_seconds() < UPDATE_CHECK_HOURS * 3600:
                latest = cache.get("latest_version")
                if latest and _version_tuple(latest) > _version_tuple(APP_VERSION):
                    if cache.get("update_installed_version") == latest:
                        return None
                    return {"version": latest, "url": cache.get("latest_url", GITHUB_URL + "/releases")}
                return None
        except Exception:
            pass
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"User-Agent": f"TokenBar/{APP_VERSION}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
        tag = payload.get("tag_name", "")
        url = payload.get("html_url", GITHUB_URL + "/releases")
        latest = tag.lstrip("v")
        cache["last_update_check"] = now.isoformat()
        cache["latest_version"] = latest
        cache["latest_url"] = url
        if _version_tuple(latest) > _version_tuple(APP_VERSION):
            if cache.get("update_installed_version") == latest:
                return None
            return {"version": latest, "url": url}
        return None
    except Exception as e:
        log.debug("Update check failed: %s", e)
        return None


def detect_lang():
    langs = NSLocale.preferredLanguages()
    for l in langs:
        if str(l).lower().startswith("fr"):
            return "fr"
    return "en"


SHORT_NAMES = {
    "claude-sonnet-4-6":          "Sonnet 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-opus-4-5-20251101":   "Opus 4.5",
    "claude-opus-4-6":            "Opus 4.6",
    "claude-haiku-4-5-20251001":  "Haiku 4.5",
}

NODE_BIN     = shutil.which("node")
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


SKIP_MODELS = {"<synthetic>", "unknown", ""}

def short_model(name):
    if not name or name in SKIP_MODELS:
        return None
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


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _scrub_secrets(obj):
    if isinstance(obj, dict):
        return {k: _scrub_secrets(v) for k, v in obj.items()
                if k not in ("accessToken", "refreshToken", "token")}
    if isinstance(obj, list):
        return [_scrub_secrets(i) for i in obj]
    return obj


def save_cache(cache):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        clean = _scrub_secrets(cache)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except OSError as e:
        log.warning("save_cache failed: %s", e)


def get_oauth_token():
    try:
        result = subprocess.run(
            [SECURITY_BIN, "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            log.warning("Keychain lookup failed (exit %d)", result.returncode)
            return None
        data = json.loads(result.stdout.strip())
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, KeyError) as e:
        log.warning("Keychain password parse error: %s", e)
        return None


def fetch_live_usage(cache):
    if not NODE_BIN:
        log.warning("Node.js not found in PATH — live usage data unavailable")
        return _live_fallback(cache)
    token = get_oauth_token()
    if not token:
        return _live_fallback(cache)
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
        data = json.loads(result.stdout)
        if not _validate_live_response(data):
            return _live_fallback(cache)
        cache["last_live"] = data
        cache["last_live_ts"] = datetime.now(timezone.utc).isoformat()
        return data
    except Exception as e:
        log.warning("fetch_live_usage failed: %s", e)
        return _live_fallback(cache)


def _validate_live_response(data):
    if not isinstance(data, dict):
        log.warning("API response is not a dict — format may have changed")
        return False
    if "five_hour" not in data and "seven_day" not in data:
        log.warning("API response missing expected keys (five_hour/seven_day) — format may have changed")
        return False
    return True


def _validate_jsonl_entry(d):
    if not isinstance(d, dict):
        return False
    if d.get("type") != "assistant":
        return False
    if not d.get("timestamp"):
        return False
    msg = d.get("message")
    if not isinstance(msg, dict) or "usage" not in msg:
        return False
    return True


def _live_fallback(cache):
    cached = cache.get("last_live")
    if cached:
        ts = cache.get("last_live_ts", "")
        log.debug("Using cached live data from %s", ts)
        cached["_stale"] = True
        cached["_stale_since"] = ts
    return cached


def request_notification_permission():
    if not _HAS_UN:
        return
    center = UNUserNotificationCenter.currentNotificationCenter()
    if _un_delegate:
        center.setDelegate_(_un_delegate)
    center.requestAuthorizationWithOptions_completionHandler_(
        0x06,
        lambda granted, error: log.debug("Notification permission granted=%s error=%s", granted, error),
    )


def play_alert_sound(sound_name):
    if not sound_name:
        return
    path = f"/System/Library/Sounds/{sound_name}.aiff"
    sound = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
    if sound:
        sound.play()


def send_threshold_notification(pct, threshold, sound_name):
    if _HAS_UN:
        try:
            center = UNUserNotificationCenter.currentNotificationCenter()
            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_("TokenBar")
            if threshold >= 95:
                content.setBody_(f"Utilisation critique : {round(pct)}% de la limite 5h atteinte")
            else:
                content.setBody_(f"Attention : {round(pct)}% de la limite 5h atteinte")
            if sound_name:
                content.setSound_(UNNotificationSound.defaultSound())
            request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
                f"tokenbar-threshold-{threshold}", content, None
            )
            center.addNotificationRequest_withCompletionHandler_(
                request,
                lambda error: log.warning("Notification error: %s", error) if error else None,
            )
        except Exception as e:
            log.warning("Failed to send notification: %s", e)
    play_alert_sound(sound_name)


def check_thresholds(cache, pct, config):
    if pct is None:
        return
    thresholds = config.get("alert_thresholds", [80, 95])
    sound_name = config.get("alert_sound", "Glass")
    notified = set(cache.get("notified_thresholds", []))
    last_pct = cache.get("last_threshold_pct")
    if last_pct is not None and last_pct - pct > RESET_DROP_THRESHOLD:
        notified = set()
    for t in sorted(thresholds):
        if pct >= t and t not in notified:
            send_threshold_notification(pct, t, sound_name)
            notified.add(t)
    cache["notified_thresholds"] = list(notified)
    cache["last_threshold_pct"] = pct


MAX_HISTORY_POINTS = 60
VELOCITY_WINDOW_MIN = 30
RESET_DROP_THRESHOLD = 20


def update_utilization_history(cache, pct):
    if pct is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    history = cache.get("utilization_history", [])
    if history:
        last_pct = history[-1].get("pct", 0)
        if last_pct - pct > RESET_DROP_THRESHOLD:
            history = []
    history.append({"ts": now, "pct": pct})
    if len(history) > MAX_HISTORY_POINTS:
        history = history[-MAX_HISTORY_POINTS:]
    cache["utilization_history"] = history


def estimate_time_remaining(cache, current_pct):
    if current_pct is None or current_pct <= 50:
        return None
    history = cache.get("utilization_history", [])
    if len(history) < 2:
        return None
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=VELOCITY_WINDOW_MIN)
    recent = [h for h in history if datetime.fromisoformat(h["ts"]) >= cutoff]
    if len(recent) < 2:
        recent = [history[0], history[-1]]
    first, last = recent[0], recent[-1]
    t0 = datetime.fromisoformat(first["ts"])
    t1 = datetime.fromisoformat(last["ts"])
    dt_min = (t1 - t0).total_seconds() / 60
    if dt_min < 1:
        return None
    dp = last["pct"] - first["pct"]
    if dp <= 0:
        return None
    velocity = dp / dt_min
    remaining_pct = 100 - current_pct
    remaining_min = remaining_pct / velocity
    if remaining_min > 600:
        return None
    return round(remaining_min)


def update_yesterday_summary(cache, today_stats):
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    last_date = cache.get("last_refresh_date")
    if last_date and last_date != today_str:
        pending = cache.get("pending_yesterday")
        if pending:
            cache["yesterday_summary"] = pending
    cache["pending_yesterday"] = {
        "date": today_str,
        "output": today_stats.get("output", 0),
        "messages": today_stats.get("messages", 0),
        "cost": today_stats.get("cost", 0.0),
    }
    cache["last_refresh_date"] = today_str


def compute_day_comparison(cache, today_stats):
    yesterday = cache.get("yesterday_summary")
    if not yesterday:
        return None
    y_out = yesterday.get("output", 0)
    t_out = today_stats.get("output", 0)
    if y_out == 0:
        return None
    delta_pct = round(((t_out - y_out) / y_out) * 100)
    return delta_pct


def empty_usage():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            "messages": 0, "cost": 0.0, "by_model": {}}


def add_message_to(bucket, usage, model, pricing=None):
    p = pricing or PRICING
    bucket["input"]        += usage.get("input_tokens", 0)
    bucket["output"]       += usage.get("output_tokens", 0)
    bucket["cache_read"]   += usage.get("cache_read_input_tokens", 0)
    bucket["cache_create"] += usage.get("cache_creation_input_tokens", 0)
    bucket["messages"]     += 1
    bucket["cost"] += (
        usage.get("input_tokens", 0)                * p["input"] +
        usage.get("output_tokens", 0)               * p["output"] +
        usage.get("cache_read_input_tokens", 0)     * p["cache_read"] +
        usage.get("cache_creation_input_tokens", 0) * p["cache_create"]
    )
    m = short_model(model)
    if not m:
        return
    if m not in bucket["by_model"]:
        bucket["by_model"][m] = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "messages": 0, "cost": 0.0}
    bm = bucket["by_model"][m]
    bm["input"]        += usage.get("input_tokens", 0)
    bm["output"]       += usage.get("output_tokens", 0)
    bm["cache_read"]   += usage.get("cache_read_input_tokens", 0)
    bm["cache_create"] += usage.get("cache_creation_input_tokens", 0)
    bm["messages"]     += 1
    bm["cost"] += (
        usage.get("input_tokens", 0)                * p["input"] +
        usage.get("output_tokens", 0)               * p["output"] +
        usage.get("cache_read_input_tokens", 0)     * p["cache_read"] +
        usage.get("cache_creation_input_tokens", 0) * p["cache_create"]
    )


def parse_sessions(pricing=None):
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
                        if not _validate_jsonl_entry(d):
                            continue
                        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                        if ts < window_7d:
                            continue
                        usage = d["message"].get("usage", {})
                        model = d["message"].get("model") or "unknown"
                        add_message_to(buckets["7d"], usage, model, pricing)
                        if ts >= today_start:
                            add_message_to(buckets["today"], usage, model, pricing)
                        if ts >= month_start:
                            add_message_to(buckets["month"], usage, model, pricing)
                    except Exception:
                        skipped_lines += 1
        except Exception as e:
            log.warning("parse_sessions: cannot read %s: %s", path, e)

    if skipped_lines:
        log.debug("parse_sessions: skipped %d malformed lines", skipped_lines)
    return buckets


def parse_history(pricing=None):
    p = pricing or PRICING
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    msgs = out = cost = 0
    skipped_lines = 0

    for path in glob.glob(os.path.join(CLAUDE_DIR, "projects", "**", "*.jsonl"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if not _validate_jsonl_entry(d):
                            continue
                        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                        if ts < cutoff:
                            continue
                        u   = d["message"].get("usage", {})
                        inp = u.get("input_tokens", 0)
                        o   = u.get("output_tokens", 0)
                        cr  = u.get("cache_read_input_tokens", 0)
                        cc  = u.get("cache_creation_input_tokens", 0)
                        msgs += 1
                        out  += o
                        cost += (inp * p["input"]       + o  * p["output"] +
                                 cr  * p["cache_read"]  + cc * p["cache_create"])
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
        try:
            action_type = body["type"] if hasattr(body, "__getitem__") else str(body)
        except (KeyError, TypeError):
            action_type = str(body)
        if action_type == "quit":
            NSApplication.sharedApplication().terminate_(None)
        elif action_type == "refresh":
            if self.delegate:
                self.delegate.start_refresh()
        elif action_type == "open_url":
            try:
                url = body["url"] if hasattr(body, "__getitem__") else ""
                if url:
                    subprocess.Popen(["open", str(url)])
            except Exception:
                pass
        elif action_type == "resize":
            try:
                h = int(body["height"]) if hasattr(body, "__getitem__") else 620
                h = max(150, min(700, h))
            except (KeyError, TypeError, ValueError):
                h = 620
            if self.delegate:
                self.delegate.resizePopover_(h)
        elif action_type == "run_update":
            if self.delegate:
                threading.Thread(target=self.delegate._do_brew_update, daemon=True).start()
        elif action_type == "restart_app":
            subprocess.Popen(["open", "/Applications/TokenBar.app"])
            NSApplication.sharedApplication().terminate_(None)


class AppDelegate(NSObject):

    def applicationDidFinishLaunching_(self, notification):
        self._cached_data = None
        self._webview = None
        self._popover = None
        self._lang = detect_lang()

        config = load_config()
        interval = config.get("refresh_interval", 60)

        self._setup_status_item()
        self._setup_webview()
        self._setup_popover()
        global _app_delegate_ref
        _app_delegate_ref = self
        request_notification_permission()
        self.start_refresh()

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            float(interval), self,
            objc.selector(self.timerFired_, signature=b'v@:@'),
            None, True
        )

    def _setup_status_item(self):
        bar = NSStatusBar.systemStatusBar()
        self._status_item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        btn = self._status_item.button()
        btn.setTitle_("◆ …")
        btn.setAction_(objc.selector(self.statusItemClicked_, signature=b'v@:@'))
        btn.setTarget_(self)
        btn.sendActionOn_(4 | 8)  # NSEventMaskLeftMouseUp | NSEventMaskRightMouseDown

    def _setup_webview(self):
        config = WKWebViewConfiguration.alloc().init()

        handler = ActionHandler.alloc().init()
        handler.delegate = self
        config.userContentController().addScriptMessageHandler_name_(handler, "action")
        self._handler = handler

        self._webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, 340, 590), config
        )
        self._webview.setAutoresizingMask_(18)

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
        self._popover.setContentSize_(NSMakeSize(340, 590))
        self._popover.setBehavior_(NSPopoverBehaviorTransient)

        vc = NSViewController.alloc().init()
        vc.setView_(self._webview)
        self._popover.setContentViewController_(vc)

    def statusItemClicked_(self, sender):
        event = NSApplication.sharedApplication().currentEvent()
        if event and event.type() == 3:  # NSEventTypeRightMouseDown
            self._show_context_menu()
        else:
            self.togglePopover_(sender)

    def _show_context_menu(self):
        s = MENU_STRINGS.get(self._lang, MENU_STRINGS["en"])
        menu = NSMenu.alloc().init()

        copy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            s["copy_stats"], objc.selector(self.copyStats_, signature=b'v@:@'), "")
        copy_item.setTarget_(self)
        copy_item.setEnabled_(self._cached_data is not None)
        menu.addItem_(copy_item)

        menu.addItem_(NSMenuItem.separatorItem())

        dash_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            s["dashboard"], objc.selector(self.openDashboard_, signature=b'v@:@'), "")
        dash_item.setTarget_(self)
        menu.addItem_(dash_item)

        prefs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            s["preferences"], objc.selector(self.openPreferences_, signature=b'v@:@'), "")
        prefs_item.setTarget_(self)
        menu.addItem_(prefs_item)

        menu.addItem_(NSMenuItem.separatorItem())

        about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            s["about"], objc.selector(self.showAbout_, signature=b'v@:@'), "")
        about_item.setTarget_(self)
        menu.addItem_(about_item)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            s["quit"], objc.selector(self.quitApp_, signature=b'v@:@'), "q")
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)

        self._status_item.popUpStatusItemMenu_(menu)

    def copyStats_(self, sender):
        data = self._cached_data
        if not data:
            return
        today = data.get("today", {})
        fh = (data.get("live") or {}).get("five_hour", {})
        pct = fh.get("utilization")
        out = today.get("output", 0)
        cost = today.get("cost", 0)
        out_str = f"{out/1e6:.1f}M" if out >= 1e6 else f"{round(out/1e3)}K" if out >= 1e3 else str(out)
        cost_str = f"${cost:.2f}" if cost >= 1 else f"${cost:.3f}"
        pct_str = f"{round(pct)}%" if pct is not None else "N/A"
        by_model = today.get("by_model", {})
        if by_model:
            top = max(by_model.items(), key=lambda x: x[1].get("output", 0))
            total = today.get("output", 1) or 1
            model_str = f"{top[0]} ({round(top[1].get('output',0)/total*100)}%)"
        else:
            model_str = "N/A"
        text = f"🔷 TokenBar — Today: {out_str} output, {cost_str} | 5h window: {pct_str} | Top model: {model_str}"
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)

    def showAbout_(self, sender):
        try:
            info = NSBundle.mainBundle().infoDictionary()
            version = str(info.get("CFBundleShortVersionString") or APP_VERSION)
        except Exception:
            version = APP_VERSION
        token = get_oauth_token()
        about = {
            "version": version,
            "oauth_ok": token is not None,
            "github_url": GITHUB_URL,
            "lang": self._lang,
        }
        if not self._popover.isShown():
            btn = self._status_item.button()
            self._popover.showRelativeToRect_ofView_preferredEdge_(
                btn.bounds(), btn, NSRectEdgeMinY
            )
        js = f"if(window.showAbout) window.showAbout({json.dumps(about)})"
        self._webview.evaluateJavaScript_completionHandler_(js, None)

    def openDashboard_(self, sender):
        subprocess.Popen(["open", "https://claude.ai"])

    def openPreferences_(self, sender):
        if not os.path.exists(CONFIG_FILE):
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        subprocess.Popen(["open", CONFIG_FILE])

    def quitApp_(self, sender):
        NSApplication.sharedApplication().terminate_(None)

    def resizePopover_(self, height):
        self._popover.setContentSize_(NSMakeSize(340, float(height)))

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
        config  = load_config()
        pricing = config["pricing"]
        cache   = load_cache()
        live    = fetch_live_usage(cache)
        local   = parse_sessions(pricing)
        history = parse_history(pricing)

        fh = (live or {}).get("five_hour", {})
        fh_pct_val = fh.get("utilization")
        update_utilization_history(cache, fh_pct_val)
        eta_min = estimate_time_remaining(cache, fh_pct_val)
        update_yesterday_summary(cache, local["today"])
        day_delta = compute_day_comparison(cache, local["today"])
        check_thresholds(cache, fh_pct_val, config)
        update_info = check_for_update(cache)

        data = {
            "live":           live,
            "today":          local["today"],
            "7d":             local["7d"],
            "month":          local["month"],
            "history":        history,
            "config":         {"currency": config["currency"]},
            "lang":           self._lang,
            "eta_min":        eta_min,
            "day_delta":      day_delta,
            "update_available":    update_info,
            "update_auto_approved": cache.get("update_auto_approved", False),
        }
        self._cached_data = data
        save_cache(cache)

        display = config["menubar_display"]
        fh_pct = fh_pct_val
        if display == "percent" and fh_pct is not None:
            title = f"◆ {round(fh_pct)}%"
        elif display == "cost_today":
            sym = config["currency"]
            title = f"◆ {sym}{local['today']['cost']:.2f}"
        elif display == "tokens_today":
            title = f"◆ {fmt_tokens(local['today']['output'])}"
        elif fh_pct is not None:
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

    def evalJS_(self, js_str):
        self._webview.evaluateJavaScript_completionHandler_(js_str, None)

    def _run_js_main(self, js):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            objc.selector(self.evalJS_, signature=b'v@:@'), js, False
        )

    def _do_brew_update(self):
        brew = shutil.which("brew")
        if not brew:
            self._run_js_main("if(window.updateDone) window.updateDone(false, 'Homebrew n\\'est pas installé')")
            return
        try:
            subprocess.run([brew, "tap", "clemships/tokenbar"], capture_output=True, timeout=30)
            result = subprocess.run(
                [brew, "upgrade", "--cask", "tokenbar"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                result = subprocess.run(
                    [brew, "install", "--cask", "--force", "tokenbar"],
                    capture_output=True, text=True, timeout=120
                )
            if result.returncode == 0:
                cache = load_cache()
                cache["update_auto_approved"] = True
                cache["update_installed_version"] = cache.get("latest_version", "")
                save_cache(cache)
                self._run_js_main("if(window.updateDone) window.updateDone(true, null)")
            else:
                err = (result.stderr or result.stdout or "Erreur inconnue").strip()
                self._run_js_main(f"if(window.updateDone) window.updateDone(false, {json.dumps(err)})")
        except subprocess.TimeoutExpired:
            self._run_js_main("if(window.updateDone) window.updateDone(false, 'Timeout — réessayez manuellement')")
        except Exception as e:
            self._run_js_main(f"if(window.updateDone) window.updateDone(false, {json.dumps(str(e))})")


if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()
