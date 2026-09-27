#!/usr/bin/env python3
"""Inko API command-line client (standard library only).

    python inko.py doctor                         check python, packages, key, network
    python inko.py auth                           save + verify an API key (reads it from stdin or --key)
    python inko.py account                        balance / key limits / 常用字迹 / favourites
    python inko.py models [--symbols]             models, pricing, math symbols Logic can write
    python inko.py styles [--model M] [--favorites] [--where "neat>=60 beauty>=50"] [--sort -beauty] [--limit 12]
    python inko.py default-style [CODE | --clear] show / set / clear the account's 常用字迹 (used when --style is omitted)
    python inko.py previews 12 37 88 [--model M]  download sample images (+ one contact sheet) to show the user
    python inko.py quote (--text T | --file F) [--model M] [--size S]           free
    python inko.py layout --spec layout.json [--model M] [--preview p.png]       free: validate + plan a custom layout
    python inko.py generate (--text T | --file F | --layout layout.json) [options] [--yes]
    python inko.py wait JOB_ID [--out DIR]        poll until done, then download
    python inko.py download JOB_ID [--out DIR]
    python inko.py jobs [--limit N]
    python inko.py rewrite JOB_ID [--yes]         one free re-write per finished job (new random seed)
    python inko.py cancel JOB_ID

logic-1 (quote / layout / generate): long =-chains inside $…$ are cut into pieces that can wrap, so they start right after
所以 / 得 like a student's (formula_splits; --keep-formulas keeps them whole), and math_style warns about typeset habits
($$ display formulas, full stops, 所以 / 得 left alone at the end of a line).

Every command prints one JSON object on stdout; progress goes to stderr.
Key lookup order: $INKO_API_KEY, ./.inko/key, ~/.config/inko/key (%APPDATA%/inko/key on Windows).
$INKO_API_BASE overrides the endpoint (default https://api.inkotype.com/v1).
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

VERSION = "1.2.0"
DEFAULT_BASE = "https://api.inkotype.com/v1"
UA = f"inko-skill/{VERSION} (+https://inkotype.com/developers)"
TERMINAL = ("succeeded", "failed", "canceled")
FACETS = ("neat", "beauty", "compact", "joined", "slant", "aspect", "round", "weight", "ink")


# ── small utils ─────────────────────────────────────────────────────────────

def emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    sys.stdout.flush()


def note(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def fail(code: str, message: str, exit_code: int = 1, **extra) -> None:
    emit({"ok": False, "error": {"code": code, "message": message, **extra}})
    sys.exit(exit_code)


def base_url() -> str:
    return os.environ.get("INKO_API_BASE", DEFAULT_BASE).rstrip("/")


def user_key_path() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "inko" / "key"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "inko" / "key"


def find_key() -> tuple[str | None, str]:
    k = os.environ.get("INKO_API_KEY", "").strip()
    if k:
        return k, "env INKO_API_KEY"
    for p in (Path.cwd() / ".inko" / "key", user_key_path()):
        try:
            if p.exists():
                k = p.read_text(encoding="utf-8").strip()
                if k:
                    return k, str(p)
        except OSError:
            pass
    return None, ""


def need_key() -> str:
    k, _ = find_key()
    if not k:
        fail("no_api_key", "No Inko API key found. Ask the user for their key (inkotype.com → 账户 → API key, starts with ink_live_), "
             "then run:  python inko.py auth  (paste the key on stdin), or set INKO_API_KEY.", 2)
    return k  # type: ignore[return-value]


# ── HTTP with retries ───────────────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str = "", retry_after: float | None = None):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message, self.request_id, self.retry_after = status, code, message, request_id, retry_after


# Many machines in China run a local proxy (HTTPS_PROXY=127.0.0.1:7890 …) that breaks TLS to Inko's Hong Kong servers
# (SSL: UNEXPECTED_EOF). When a request through the proxy fails at the network level, retry it directly and keep doing so.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_use_direct = False


def _urlopen(req, timeout: float):
    return _DIRECT.open(req, timeout=timeout) if _use_direct else urllib.request.urlopen(req, timeout=timeout)


def _proxy_fallback(url: str) -> bool:
    """Switch to direct connections once, if a proxy would be used for this URL. True if switched."""
    global _use_direct
    if _use_direct:
        return False
    host = urllib.parse.urlparse(url).hostname or ""
    proxies = urllib.request.getproxies()
    if not (proxies.get("https") or proxies.get("http")) or urllib.request.proxy_bypass(host):
        return False
    _use_direct = True
    note(f"… the proxy ({proxies.get('https') or proxies.get('http')}) failed to reach {host}; retrying without it "
         f"(set NO_PROXY={host} to skip the proxy from the start)")
    return True


def call(method: str, path: str, body=None, *, auth: bool = True, headers: dict | None = None, retries: int = 4, timeout: float = 60):
    url = path if path.startswith("http") else base_url() + path
    h = {"User-Agent": UA, "Accept": "application/json"}
    if auth:
        h["Authorization"] = f"Bearer {need_key()}"
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with _urlopen(req, timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                err = json.loads(raw).get("error", {})
            except ValueError:
                err = {}
            ra = e.headers.get("Retry-After")
            msg = err.get("message") or (f"HTTP {e.code} from {url} — not an Inko API answer (service down, a proxy, or a wrong "
                                         "INKO_API_BASE); try again later" if raw.lstrip()[:1] == b"<" else raw[:200].decode("utf-8", "replace"))
            ae = ApiError(e.code, err.get("code", f"http_{e.code}"), msg,
                          err.get("request_id", ""), float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
            retryable = e.code in (429, 500, 502, 503, 504) and ae.code not in ("daily_limit", "too_many_active", "free_paused", "insufficient_balance")
            if retryable and attempt < retries:
                wait = ae.retry_after or delay
                note(f"… {ae.code} (HTTP {e.code}), retrying in {wait:.0f}s")
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise ae
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < retries:
                if _proxy_fallback(url):
                    continue
                note(f"… network error ({e.__class__.__name__}: {str(e)[:80]}), retrying in {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise ApiError(0, "network", f"cannot reach {url}: {e}")
    raise ApiError(0, "network", "unreachable")


def api(method: str, path: str, body=None, **kw):
    try:
        return call(method, path, body, **kw)
    except ApiError as e:
        fail(e.code, e.message, 1, http=e.status, request_id=e.request_id, hint=HINTS.get(e.code, ""))


HINTS = {
    "unauthenticated": "The key is missing, mistyped or revoked. Ask the user for a fresh key from inkotype.com → 账户 → API key.",
    "insufficient_balance": "The balance doesn't cover this job (every job is paid from the balance; there is no membership or "
                            "quota). Tell the user the price; they top up on inkotype.com (价格 → 充值余额, "
                            "https://inkotype.com/pricing#topup), then submit again.",
    "invalid_text": "Run `inko.py quote` to see exactly which characters/symbols are the problem and rewrite them.",
    "content_blocked": "The text looks like a forged-document risk (IOU, receipt, contract, signature, certificate, leave note…) "
                       "or violates policy. Tell the user it can't be generated; do not try to work around it.",
    "style_model_mismatch": "That style doesn't support the chosen model. Logic only has 8 styles per account: `inko.py styles --model logic-1`.",
    "style_not_found": "Unknown style. List available ones with `inko.py styles`.",
    "style_not_ready": "That custom handwriting isn't finished yet (still being made on the website); pick another one.",
    "no_slot": "That custom handwriting isn't in one of the account's custom-handwriting seats any more (a seat was refunded "
               "or taken back). Pick another handwriting; to use it again the user buys a seat (¥19.9) or deletes another "
               "custom handwriting on inkotype.com.",
    "label_required": "label:none needs a custom-handwriting seat (专属字迹席位) bought on inkotype.com and the AI-labelling "
                      "agreement signed there (《AI 生成内容标识协议》). Use label visible.",
    "rate_limited": "Too many requests; wait and retry later.",
    "too_many_active": "The account already has the maximum number of running jobs; wait for them (inko.py jobs) and retry.",
    "daily_limit": "This key hit its daily spending cap; the user can raise it on the website or wait until tomorrow (Beijing time).",
    "queue_full": "Service is busy; retry in a few minutes.",
    "free_paused": "Generation on the free gift balance alone is paused at peak time; the user can top up or try later.",
    "link_expired": "Signed download links last 24h; run `inko.py download JOB_ID` to get fresh ones.",
    "no_rewrite": "Only one free rewrite per succeeded job, and rewrites can't be rewritten again.",
}


# ── local job log (so a crashed/forgetful agent can find its jobs again) ─────

def _plan_store(job_id: str) -> Path:
    return Path.cwd() / ".inko" / "plans" / f"{job_id}.json"


def remember_files(job_id: str, files: dict) -> None:
    """Keep what a submitted job was made from (layout.json + plan.json, or text.txt) so `wait` / `download` / a rewrite
    can drop it next to the pages later. Lives in ./.inko/plans/ (git-ignored)."""
    try:
        p = _plan_store(job_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def remember_plan(job_id: str, layout: dict, plan: dict) -> None:
    remember_files(job_id, {"layout.json": layout, "plan.json": plan})


def recall_plan(job_id: str) -> dict | None:
    try:
        return json.loads(_plan_store(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def log_job(job: dict, extra: dict | None = None) -> None:
    try:
        d = Path.cwd() / ".inko"
        d.mkdir(exist_ok=True)
        gi = d / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")            # never commit keys / job logs
        with open(d / "jobs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "id": job.get("id"), "status": job.get("status"),
                                "model": job.get("model"), "chars": job.get("chars"), **(extra or {})}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── commands ────────────────────────────────────────────────────────────────

def cmd_doctor(a) -> None:
    out: dict = {"python": sys.version.split()[0], "ok": True, "problems": []}
    try:
        import PIL
        import numpy
        out["pillow"], out["numpy"] = PIL.__version__, numpy.__version__
    except ImportError:
        out["ok"] = False
        out["problems"].append("Pillow/numpy missing (needed for post-processing): python -m pip install pillow numpy")
    try:
        import cv2  # noqa: F401
        out["opencv"] = "available (better sheet detection in photos)"
    except ImportError:
        out["opencv"] = "not installed (optional)"
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from _common import cjk_font  # noqa: WPS433
        f = cjk_font(20)
        out["cjk_font"] = getattr(f, "path", "Pillow default (no CJK glyphs; previews will show boxes; set INKO_FONT to a .ttf/.ttc)")
    except SystemExit:
        pass
    out["api_base"] = base_url()
    try:
        m = call("GET", "/models", auth=False, retries=1)
        out["api_reachable"] = True
        out["pricing"] = m.get("pricing")
    except ApiError as e:
        out["ok"] = False
        out["api_reachable"] = False
        out["problems"].append(f"cannot reach {base_url()}: {e.message}")
    k, where = find_key()
    if not k:
        out["key"] = "missing"
        out["problems"].append("no API key yet: ask the user, then run `inko.py auth`")
    else:
        out["key_source"] = where
        try:
            acc = call("GET", "/account", retries=1)
            name = (acc.get("key") or {}).get("name")
            out["key"] = "valid" + (f" (key “{name}”)" if name else "")
            out.update(_money(acc))
            if "default_style" in acc:                          # the user's own handwriting choices (website 字迹库)
                d = acc.get("default_style") or {}
                out["handwriting"] = {"default": d.get("label") and f"{d['label']} (ref {d['ref']}, models {', '.join(d.get('models') or []) or 'none right now'})",
                                      "favorites": len(acc.get("favorites") or [])}
        except ApiError as e:
            out["ok"] = False
            out["key"] = f"invalid: {e.code}"
            out["problems"].append(HINTS.get(e.code, e.message))
    emit(out)


def cmd_auth(a) -> None:
    key = (a.key or "").strip()
    if not key:
        if sys.stdin.isatty():
            import getpass
            key = getpass.getpass("Inko API key (ink_live_…): ").strip()
        else:
            key = sys.stdin.read().strip()
    if not re.fullmatch(r"ink_[A-Za-z0-9_\-]{10,120}", key):
        fail("bad_key_format", "That doesn't look like an Inko key (it starts with ink_live_). Nothing was saved.", 2)
    os.environ["INKO_API_KEY"] = key
    try:
        acc = call("GET", "/account", retries=2)
    except ApiError as e:
        fail(e.code, f"Key rejected by the API: {e.message}. Nothing was saved.", 1, hint=HINTS.get(e.code, ""))
    path = (Path.cwd() / ".inko" / "key") if a.project else user_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if a.project:
        gi = path.parent / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")
    emit({"ok": True, "saved_to": str(path), "key_prefix": acc["key"]["prefix"], "key_name": acc["key"]["name"],
          **_money(acc), "daily_limit_cents": acc["key"].get("daily_limit_cents")})


def cmd_account(a) -> None:
    emit(api("GET", "/account"))


def cmd_models(a) -> None:
    m = api("GET", "/models", auth=False)
    if not a.symbols:
        for d in m.get("data", []):
            d.pop("symbols", None)
    emit(m)


def _parse_where(expr: str) -> list[tuple[str, str, float]]:
    conds = []
    for part in re.split(r"[,\s]+", (expr or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"([a-z]+)(>=|<=|>|<|=)(\d+(?:\.\d+)?)", part)
        if not m or m.group(1) not in FACETS:
            fail("bad_where", f"can't parse '{part}': use facet>=N with facets {', '.join(FACETS)} (values are 0-100 percentiles)", 2)
        conds.append((m.group(1), m.group(2), float(m.group(3))))
    return conds


def _ok(v: float, op: str, x: float) -> bool:
    return {">=": v >= x, "<=": v <= x, ">": v > x, "<": v < x, "=": v == x}[op]


def cmd_styles(a) -> None:
    q = {}
    if a.model:
        q["model"] = a.model
    if a.favorites:
        q["favorites"] = "true"
    data = api("GET", "/styles" + ("?" + urllib.parse.urlencode(q) if q else ""))["data"]
    if a.favorites and data and "favorite" not in data[0]:
        fail("not_supported", "This Inko server doesn't report favourites yet; use `inko.py styles` with --where filters instead.", 1)
    conds = _parse_where(a.where)
    rows = []
    for s in data:
        f = s.get("facets") or {}
        if any(k not in f or not _ok(float(f[k]), op, x) for k, op, x in conds):
            continue
        if a.kind and s.get("kind") != a.kind:
            continue
        model = a.model or ("logic-1" if "logic-1" in s.get("models", []) else (s.get("models") or ["lyric-1"])[0])
        pv = (s.get("preview") or {}).get(model) or next(iter((s.get("preview") or {}).values()), {})
        rows.append({"style": s["id"], "label": s.get("label"), "kind": s.get("kind"), "models": s.get("models"),
                     **({"default": True} if s.get("default") else {}), **({"favorite": True} if s.get("favorite") else {}),
                     "facets": {k: f.get(k) for k in FACETS if k in f}, "neighbors": (s.get("neighbors") or [])[:5],
                     "preview_card": pv.get("card"), "preview_page": pv.get("page")})
    if a.sort:
        key = a.sort.lstrip("-+")
        if key not in FACETS:
            fail("bad_sort", f"sort by one of {', '.join(FACETS)}", 2)
        rows.sort(key=lambda r: r["facets"].get(key, -1), reverse=not a.asc)
    if not a.sort:                                               # the user's own choices first: 常用字迹, own handwritings, favourites
        rows.sort(key=lambda r: (not r.get("default"), r["kind"] != "custom", not r.get("favorite")))
    total = len(rows)
    extra = {"hint": "No favourites (收藏) on this account yet — choose by description (references/styles.md)."} if a.favorites and not total else {}
    emit({"count": total, "shown": min(total, a.limit), "styles": rows[:a.limit], **extra,
          "facets_help": "percentiles 0-100 among all styles: neat 潦草→工整, beauty 一般→好看(estimate), compact 舒展→紧凑, joined 一笔一划→连笔, "
                         "slant 正→右斜, aspect 扁→长, round 方折→圆润, weight 细→粗 (as written), ink 浅→深 (as written)"})


def cmd_default_style(a) -> None:
    """The account's 常用字迹 (default handwriting): used whenever a job has no --style, on the website too."""
    if a.clear:
        api("DELETE", "/styles/default")
        emit({"ok": True, "default_style": None, "note": "Cleared. Jobs without --style now use Inko's fallback handwriting."})
        return
    if a.ref:
        d = api("PUT", f"/styles/{urllib.parse.quote(str(a.ref).strip(), safe='')}/default").get("default_style") or {}
        models = ", ".join(d.get("models") or []) or "no model right now"
        emit({"ok": True, "default_style": d, "note": f"{d.get('label')} is now the account's 常用字迹 (the website uses it too); "
              f"jobs without --style use it for: {models}."})
        return
    acc = api("GET", "/account")
    if "default_style" not in acc:
        fail("not_supported", "This Inko server doesn't support 常用字迹 yet.", 1)
    emit({"default_style": acc.get("default_style"), "favorites": acc.get("favorites"),
          "note": "Jobs without --style use default_style when it supports the job's model (see its models)."})


def _download(url: str, path: Path, timeout: float = 180) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with _urlopen(req, timeout) as r, open(path, "wb") as f:
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
            return path
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ApiError(e.code, "link_expired", "download link expired or invalid")
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if _proxy_fallback(url):
                continue
        time.sleep(2 * (attempt + 1))
    raise ApiError(0, "network", f"download failed: {last}")


def cmd_previews(a) -> None:
    data = {str(s["id"]): s for s in api("GET", "/styles")["data"]}
    out_dir = Path(a.out)
    files = []
    for code in a.codes:
        s = data.get(str(code).lstrip("0") or "0") or data.get(str(code))
        if not s:
            files.append({"style": code, "error": "not available to this account"})
            continue
        model = a.model or ("logic-1" if "logic-1" in s.get("models", []) else s["models"][0])
        pv = (s.get("preview") or {}).get(model) or {}
        url = pv.get("page" if a.page else "card") or pv.get("card") or pv.get("page")
        if not url:
            files.append({"style": s["id"], "error": "no preview image"})
            continue
        ext = Path(urllib.parse.urlparse(url).path).suffix or ".webp"
        p = out_dir / f"style-{s['id']}-{model}{ext}"
        try:
            _download(url, p)
            files.append({"style": s["id"], "label": s.get("label"), "model": model, "file": str(p)})
        except ApiError as e:
            files.append({"style": s["id"], "error": e.message})
    sheet = None
    ok = [f for f in files if "file" in f]
    if ok and not a.no_sheet:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from _common import Image, ImageDraw, cjk_font  # noqa: WPS433
            thumbs = []
            for f in ok:
                im = Image.open(f["file"]).convert("RGB")
                im.thumbnail((720, 480))
                thumbs.append((f, im))
            cols = 2 if len(thumbs) > 1 else 1
            cw = max(t[1].width for t in thumbs)
            ch = max(t[1].height for t in thumbs) + 56
            rows_n = (len(thumbs) + cols - 1) // cols
            sheet_im = Image.new("RGB", (cols * cw + (cols + 1) * 16, rows_n * ch + (rows_n + 1) * 16), (240, 238, 232))
            d = ImageDraw.Draw(sheet_im)
            font = cjk_font(30)
            for i, (f, im) in enumerate(thumbs):
                x = 16 + (i % cols) * (cw + 16)
                y = 16 + (i // cols) * (ch + 16)
                sheet_im.paste(im, (x, y + 50))
                d.text((x, y + 6), f"{f['label'] or f['style']}  (style={f['style']})", fill=(30, 30, 30), font=font)
            sheet = out_dir / "contact-sheet.png"
            sheet_im.save(sheet)
        except Exception as e:  # noqa: BLE001
            note(f"(contact sheet skipped: {e})")
    emit({"files": files, "contact_sheet": str(sheet) if sheet else None,
          "tip": "Open the contact sheet (or the files) and show them to the user so they can pick by eye."})


_BAD_ESCAPES = {"\x08": "\\b (e.g. \\beta)", "\x0c": "\\f (e.g. \\frac)", "\x0b": "\\v (e.g. \\vec)", "\x07": "\\a (e.g. \\alpha)",
                "\t": "\\t (e.g. \\times, \\theta, \\tan)", "\r": "\\r (e.g. \\right, \\rho)"}


def check_escapes(text: str, where: str = "text") -> None:
    """LaTeX written inside JSON / shell strings often loses its backslash: "\\frac" -> form-feed + "rac"."""
    bad = [v for k, v in _BAD_ESCAPES.items() if k in text]
    if bad:
        fail("broken_backslash", f"The {where} contains control characters from swallowed backslashes: {', '.join(bad)}. "
             "In JSON every LaTeX backslash must be doubled (\"\\\\frac\"); safest is to write the text/layout file with your "
             "file-writing tool (not a shell heredoc) and pass --file / --layout.", 2)
    for line in text.split("\n"):
        if line.count("$") % 2 and re.match(r"^(eq|u\b|abla|e\b|ot|ewline|leq)", line.lstrip()):
            fail("broken_backslash", "A line starts with 'eq' / 'abla' / 'ot'… right after a line break — this is usually \\neq, \\nabla, \\not… "
                 "whose \\n became a newline. Double the backslashes in JSON or write the text with a file tool.", 2)


def _read_text(a) -> str:
    if getattr(a, "text", None):
        check_escapes(a.text)
        return a.text
    if getattr(a, "file", None):
        p = Path(a.file)
        if not p.exists():
            fail("file_not_found", f"text file not found: {p}", 2)
        t = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        check_escapes(t, str(p))
        return t
    fail("no_text", "give --text, --file or --layout", 2)
    return ""


def u16len(s: str) -> int:
    """Length in UTF-16 units — how the layout engine (JavaScript) counts character positions."""
    return len(s.encode("utf-16-le")) // 2


# ── math formatting (logic-1): long formulas continue like a student's, style lint ─────────────────────
# The layout engine treats an inline $…$ as one unbreakable block: a long chain after 所以 jumps whole to the next line
# (所以 left alone) and is squeezed. Students write the first part right after 所以 and continue on the next line, so
# long chains are cut at their top-level relations into several inline formulas joined by a space (a break opportunity).
# Widths are in character heights, measured on the engine (POST /v1/layout, 2026-09): letter/digit ≈ 0.54, operator or
# relation with its spacing ≈ 0.84, brackets 0.36, inline fractions small, scripts at ~70 %.

_W_CHAR, _W_OP, _W_BRACKET = 0.54, 0.84, 0.36
LONG_FORMULA, MIN_PIECE, LINE_W = 8.0, 2.0, 24.0    # split formulas wider than this; pieces at least this wide; default line (chars)
_REL_CHARS = set("=<>≠≤≥≈≡∼⇒⇔")
_REL_CMDS = {"neq", "ne", "le", "leq", "ge", "geq", "leqslant", "geqslant", "lt", "gt", "approx", "equiv", "sim", "simeq",
             "cong", "Rightarrow", "Leftrightarrow", "Longrightarrow", "Longleftrightarrow", "iff", "implies"}
_FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "log", "lg", "ln",
          "exp", "lim", "max", "min", "sup", "inf", "det", "gcd", "deg", "arg", "dim", "ker", "Pr"}
_UNDER = {"lim", "max", "min", "sup", "inf"}               # their subscript goes underneath
_BIG = {"int": 0.92, "oint": 0.92, "iint": 1.4, "iiint": 1.9, "sum": 0.92, "prod": 0.92, "bigcup": 0.92, "bigcap": 0.92}
_SPACE_W = {",": 0.17, ":": 0.22, ";": 0.28, " ": 0.28, "!": 0.0, "quad": 1.0, "qquad": 2.0, "enspace": 0.5}
_FRACS = {"frac", "dfrac", "tfrac", "cfrac", "binom", "dbinom", "tbinom"}
_TEXTS = {"text", "textrm", "textbf", "textit", "mbox", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "mathbb", "mathcal",
          "boldsymbol", "operatorname"}
_ACCENTS = {"overline", "underline", "vec", "hat", "bar", "tilde", "dot", "ddot", "widehat", "widetilde", "overrightarrow",
            "overleftarrow", "check", "breve", "acute", "grave", "mathring"}
_ZERO = {"displaystyle", "textstyle", "scriptstyle", "limits", "nolimits", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
         "Bigl", "Bigr", "biggl", "biggr", "middle", "mathstrut", "strut", "nonumber", "notag"}
_GREEK = set("alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho varrho "
             "sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega".split())


def _cjk(ch: str) -> bool:
    return bool(ch) and ("⺀" <= ch <= "鿿" or "豈" <= ch <= "﫿" or "＀" <= ch <= "￯")


def _mtok(s: str, i: int) -> tuple[int, str, str]:
    """One LaTeX token at s[i] -> (end, kind, value): ("cmd", name) for \\name, ("sym", c) for \\c, ("grp", inner) for {…},
    ("chr", c) otherwise."""
    c = s[i]
    if c == "\\":
        j = i + 1
        while j < len(s) and s[j].isalpha():
            j += 1
        return (j, "cmd", s[i + 1:j]) if j > i + 1 else (min(i + 2, len(s)), "sym", s[i + 1:i + 2])
    if c == "{":
        depth, j = 1, i + 1
        while j < len(s) and depth:
            if s[j] == "\\":
                j += 2
                continue
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            j += 1
        j = min(j, len(s))
        return j, "grp", s[i + 1:j - 1] if depth == 0 else s[i + 1:j]
    return i + 1, "chr", c


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _arg(s: str, i: int) -> tuple[int, float, str]:
    """The argument starting at s[i] (a {group}, or one token with its own arguments: \\frac12, \\sqrt2) -> (end, width, source)."""
    i = _skip_ws(s, i)
    if i >= len(s):
        return i, 0.0, ""
    j, kind, val = _mtok(s, i)
    if kind == "grp":
        return j, formula_width(val), val
    j, w, _, _, _ = _atom(s, i)
    return j, w, s[i:j]


def _tok_w(kind: str, val: str) -> tuple[float, int, bool]:
    """Width, bracket-depth change and 'is a relation' of a token that takes no arguments."""
    if kind == "chr":
        if val in _REL_CHARS:
            return _W_OP, 0, True
        if val in "+-*/±×÷·":
            return _W_OP, 0, False
        if val in "([":
            return _W_BRACKET, 1, False
        if val in ")]":
            return _W_BRACKET, -1, False
        if val == "|":
            return _W_BRACKET, 0, False
        if val in ",;:!":
            return 0.3, 0, False
        if val == ".":
            return 0.5, 0, False
        if val == "'":
            return 0.22, 0, False
        if val.isspace():
            return 0.0, 0, False
        return (1.0 if _cjk(val) else _W_CHAR), 0, False
    if kind == "sym":
        if val == "{":
            return _W_OP, 1, False
        if val == "}":
            return _W_OP, -1, False
        if val in _SPACE_W:
            return _SPACE_W[val], 0, False
        if val == "|":
            return _W_BRACKET, 0, False
        return (0.0 if val in ("\\", "") else _W_CHAR), 0, False
    if kind == "grp":
        return formula_width(val), 0, False
    if val in _REL_CMDS:
        return _W_OP, 0, True
    if val in _GREEK:
        return 0.6, 0, False
    if val in _FUNCS:
        return 0.53 * len(val), 0, False
    if val in _BIG:
        return _BIG[val], 0, False
    if val in _SPACE_W:
        return _SPACE_W[val], 0, False
    if val in _ZERO:
        return 0.0, 0, False
    if val in ("langle", "lfloor", "lceil", "lvert", "lVert"):
        return _W_BRACKET, 1, False
    if val in ("rangle", "rfloor", "rceil", "rvert", "rVert"):
        return _W_BRACKET, -1, False
    return _W_OP, 0, False                                 # \infty \cdot \times \pm \to \ldots … are drawn ≈ 0.84 wide


def _atom(s: str, i: int) -> tuple[int, float, int, bool, bool]:
    """One atom at s[i] (a token plus the arguments its command takes; not its ^ _ scripts)
    -> (end, width, bracket-depth change, is a relation, scripts go underneath)."""
    n = len(s)
    j, kind, val = _mtok(s, i)
    if kind != "cmd":
        w, dd, rel = _tok_w(kind, val)
        return j, w, dd, rel, False
    if val in _FRACS:
        j, w1, _ = _arg(s, j)
        j, w2, _ = _arg(s, j)
        return j, 0.8 * max(w1, w2) + 0.25 + (0.72 if "binom" in val else 0), 0, False, False
    if val == "sqrt":
        k, wi = _skip_ws(s, j), 0.0
        if k < n and s[k] == "[":
            e = s.find("]", k)
            e = n - 1 if e < 0 else e
            wi, j = 0.7 * formula_width(s[k + 1:e]) + 0.5, e + 1
        j, w1, _ = _arg(s, j)
        return j, w1 + 0.54 + wi, 0, False, False
    if val in _TEXTS:
        j, w1, src = _arg(s, j)
        if val.startswith("text") or val == "mbox":            # text mode: Chinese is full width, spaces count
            inner = src[1:-1] if src.startswith("{") else src
            w1 = sum(1.0 if _cjk(ch) else 0.28 if ch.isspace() else _W_CHAR for ch in inner)
        return j, w1, 0, False, False
    if val in _ACCENTS:
        j, w1, _ = _arg(s, j)
        return j, max(w1, _W_CHAR), 0, False, False
    if val in ("left", "right"):                                 # \left( … \right): one bracket pair, one level deeper
        k, w = _skip_ws(s, j), 0.0
        if k < n:
            j, _, v2 = _mtok(s, k)
            w = 0.0 if v2 == "." else _W_BRACKET
        return j, w, (1 if val == "left" else -1), False, False
    if val == "begin":                                            # a whole environment (matrix, cases, array) is one atom
        j2, _, name = _arg(s, j)
        name = name.strip("{} ")
        end = _env_end(s, j2)
        if end is None:
            return j2, 0.0, 1, False, False
        body_start = j2
        if name == "array":                                       # \begin{array}{lcr}: skip the column spec
            body_start, _, _ = _arg(s, j2)
        return end[1], _env_width(name, s[body_start:end[0]]), 0, False, False
    if val == "end":
        j, _, _ = _arg(s, j)
        return j, 0.0, -1, False, False
    if val == "not":                                             # \not= is a relation
        k, rel = _skip_ws(s, j), False
        if k < n:
            j, k2, v2 = _mtok(s, k)
            rel = _tok_w(k2, v2)[2]
        return j, _W_OP, 0, rel, False
    w, dd, rel = _tok_w(kind, val)
    return j, w, dd, rel, val in _UNDER


_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")
_ENV_DELIMS = {"pmatrix": 2, "bmatrix": 2, "vmatrix": 2, "Bmatrix": 2, "cases": 1, "dcases": 1}


def _env_end(s: str, i: int) -> tuple[int, int] | None:
    """(start of the matching \\end{…}, index after it) for an environment whose body starts at i; None if unclosed."""
    depth = 1
    for m in _ENV_RE.finditer(s, i):
        depth += 1 if m.group(1) == "begin" else -1
        if depth == 0:
            return m.start(), m.end()
    return None


def _split_top(body: str, sep: str) -> list[str]:
    """Split at sep outside {} and nested environments."""
    parts, depth, env, last, i = [], 0, 0, 0, 0
    while i < len(body):
        if body.startswith("\\begin", i):
            env += 1
        elif body.startswith("\\end", i):
            env -= 1
        c = body[i]
        if c == "\\" and body.startswith(sep, i) and sep == "\\\\" and depth == 0 and env == 0:
            parts.append(body[last:i])
            i += 2
            last = i
            continue
        if c == "\\":
            i += 2
            continue
        depth += 1 if c == "{" else -1 if c == "}" else 0
        if c == sep and depth == 0 and env == 0:
            parts.append(body[last:i])
            last = i + 1
        i += 1
    parts.append(body[last:])
    return parts


def _env_width(name: str, body: str) -> float:
    """Written width of a matrix / cases / array: widest cell per column + column gaps + delimiters (character heights)."""
    rows = [_split_top(r, "&") for r in _split_top(body, "\\\\") if r.strip()]
    if not rows:
        return 0.0
    ncol = max(len(r) for r in rows)
    cols = [max((formula_width(r[c]) for r in rows if c < len(r)), default=0.0) for c in range(ncol)]
    return sum(cols) + 1.0 * (ncol - 1) + _W_BRACKET * _ENV_DELIMS.get(name, 0)


def _scan(s: str) -> list[list]:
    """Top-level atoms of a formula: [start, end, width, top_level_relation, script_widths, under, separator].
    Scripts, command arguments and {…} groups belong to their atom; a relation inside (), [], \\{\\}, \\left…\\right or an
    environment is not top level."""
    out: list[list] = []
    i, n, depth = 0, len(s), 0
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "^_":
            j, w, _ = _arg(s, i + 1)
            if out:
                out[-1][1] = j
                out[-1][4].append(w)
            else:
                out.append([i, j, 0.7 * w, False, [], False, False, depth])
            i = j
            continue
        j, w, dd, rel, under = _atom(s, i)
        sep = depth == 0 and (c in ",;" or s.startswith(("\\quad", "\\qquad"), i))
        out.append([i, j, w, rel and depth == 0, [], under, sep, depth])
        depth = max(0, depth + dd)
        i = j
    return out


def _atom_w(a: list) -> float:
    if not a[4]:
        return a[2]
    return max(a[2], 0.6 * max(a[4])) if a[5] else a[2] + 0.7 * max(a[4])


def formula_width(latex: str) -> float:
    """Estimated written width of a formula, in character heights (the engine's own measure, ±15 %)."""
    return sum(_atom_w(a) for a in _scan(latex))


_LEFT_RE, _RIGHT_RE = re.compile(r"\\left(?![A-Za-z])"), re.compile(r"\\right(?![A-Za-z])")


def split_formula(latex: str, long_w: float = LONG_FORMULA, min_w: float = MIN_PIECE, line_w: float = LINE_W) -> list[str]:
    """Cut a long formula as FEW times as possible, so it can follow its lead-in (所以 / 得 …) and continue at the left of
    the next line the way a student writes it:
    1. a complete statement 'LHS = …' (not starting with a relation or + / −) is cut once before its first top-level
       relation: 'I(a)=∫…dx=∫…dt=I(-a)' -> ['I(a)', '=∫…dx=∫…dt=I(-a)'] — the left side goes after 所以, the rest
       starts the next line;
    2. a piece still longer than ~0.85 of a line (line_w, in character heights) is cut again, as late as possible, before
       a later relation, after a ',' / ';' / \\quad between statements, or before a top-level binary + / −.
    Pieces are never narrower than min_w (the left side of 1. may be as short as x_n). Every cut leaves a small gap on
    the page (each piece is written in its own box), hence as few as possible. Nothing inside {}, \\frac, \\sqrt,
    scripts, brackets, \\left…\\right or environments is ever cut. Formulas up to long_w wide, or with nothing top-level
    to cut at, come back unchanged, as [latex]."""
    atoms = _scan(latex)
    ws = [_atom_w(a) for a in atoms]
    total = sum(ws)
    if total <= long_w or not atoms:
        return [latex]
    cand = _cut_points(latex, atoms)
    bounds = [0]
    k = next((k for k, a in enumerate(atoms) if a[3]), 0)
    if k and not _is_op(latex, atoms[0]) and sum(ws[:k]) >= min_w / 4 and total - sum(ws[:k]) >= min_w:
        bounds.append(k)
    bounds.append(len(atoms))
    cap = max(long_w, 0.85 * line_w)
    cuts: list[int] = []
    for s0, e0 in zip(bounds, bounds[1:]):                 # 2. greedy: cut as late as possible inside each part
        start = s0
        while sum(ws[start:e0]) > cap:
            ok = [c for c in cand if start < c < e0 and min_w <= sum(ws[start:c]) <= cap and sum(ws[c:e0]) >= min_w]
            if not ok:
                break
            best = max(ok, key=lambda c: (cand[c], c))      # prefer: after ',' between statements > relation > + / −
            cuts.append(best)
            start = best
    edges = sorted(set(bounds[1:-1] + cuts))
    if not edges:
        return [latex]
    starts = [0] + [atoms[c][0] for c in edges] + [len(latex)]
    out = [latex[starts[i]:starts[i + 1]].strip() for i in range(len(starts) - 1)]
    if any(len(_LEFT_RE.findall(t)) != len(_RIGHT_RE.findall(t)) or t.count("\\begin") != t.count("\\end") for t in out):
        return [latex]
    return out


_PLUS = {"+", "-", "\\pm", "\\mp"}


def _is_op(latex: str, a: list) -> bool:
    """A relation or + / − (a piece starting with one is a continuation, never cut before its first relation)."""
    return bool(a[3]) or latex[a[0]:a[1]].strip() in _PLUS


def _cut_points(latex: str, atoms: list) -> dict[int, int]:
    """Atom indices where a new piece may start -> preference: 3 = the atom after a top-level ',' / ';' / \\quad (a new
    statement), 2 = a top-level relation ('<=' typed as two symbols stays whole), 1 = a top-level binary + / − (not a
    sign right after a relation or operator)."""
    out: dict[int, int] = {}
    for k in range(1, len(atoms)):
        a, p = atoms[k], atoms[k - 1]
        if p[6]:
            out[k] = 3
        elif a[3] and not p[3]:
            out[k] = 2
        elif latex[a[0]:a[1]].strip() in _PLUS and a[7] == 0 and not p[3] and latex[p[0]:p[1]].strip() not in _PLUS:
            out[k] = 1
    return out


def formula_spans(line: str) -> list[tuple[int, int, bool]]:
    """(start, end, display) of every $…$ / $$…$$ in one paragraph; escaped \\$ is text. end is past the closing $."""
    out, i, n = [], 0, len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if c != "$":
            i += 1
            continue
        display = line.startswith("$$", i)
        j = i + (2 if display else 1)
        while j < n and line[j] != "$":
            j += 2 if line[j] == "\\" else 1
        if j >= n:
            break                                          # unpaired $: the quote reports it
        end = j + (2 if display and line.startswith("$$", j) else 1)
        out.append((i, end, display))
        i = end
    return out


PLAIN_LINE_W = {"small": 28.0, "medium": 24.0, "large": 20.0}      # characters per line of plain pages (the worker's budget)
_PAPER_S = {"ruled8": 4.96, "ruled7": 4.34, "blank": 7.0, "letter": 5.6, "compo": 7.0, "tian": 9.0}   # default character mm


def layout_line_w(spec: dict, box: dict | None = None) -> float:
    """Rough characters per line of a layout's page text, or of one box (for split_formula's second rule)."""
    d = spec.get("d") or {}
    s = float(((box or {}).get("form") or {}).get("size") or d.get("size") or _PAPER_S.get(spec.get("paperId", "blank"), 7.0))
    if box is not None and box.get("w"):
        return float(box["w"]) / s
    m = d.get("margins") or []
    left = float(m[3]) if len(m) > 3 and m[3] is not None else 24.0
    right = float(m[1]) if len(m) > 1 and m[1] is not None else 16.0
    return max(8.0, (210.0 - left - right) / s)


def split_long_math(text: str, line_w: float = LINE_W) -> tuple[str, list[dict]]:
    """Split long inline formulas (split_formula) in every paragraph; $$…$$ and text outside formulas stay as they are.
    line_w = characters per line. Returns (new text, [{"paragraph": n (from 1, like the quote's errors), "pieces": k}, …])."""
    lines, splits = [], []
    for n, line in enumerate(text.split("\n")):
        parts, last = [], 0
        for a, b, display in formula_spans(line):
            if display:
                continue
            pieces = split_formula(line[a + 1:b - 1], line_w=line_w)
            if len(pieces) > 1:
                parts += [line[last:a], " ".join(f"${p}$" for p in pieces)]
                last = b
                splits.append({"paragraph": n + 1, "pieces": len(pieces)})
        lines.append("".join(parts) + line[last:])
    return "\n".join(lines), splits


_LEADS = ("所以", "可得", "解得", "于是", "得", "故", "即", "则", "∴", "：", "，", ":", ",")
_STOPS = "。．｡"


def lint_math(text: str, first: int = 1, box: str | None = None) -> list[dict]:
    """Things that make a Logic page look typeset rather than student-written. Warnings only.
    Paragraphs are numbered from `first` (1 = like the quote's errors)."""
    out: list[dict] = []
    paras = text.split("\n")
    where = {"box": box} if box else {}

    def add(kind, n, snippet, message, fix):
        out.append({"kind": kind, "paragraph": n + first, **where, "snippet": snippet, "message": message, "fix": fix})

    for n, line in enumerate(paras):
        spans = formula_spans(line)
        for a, b, display in spans:
            if display:
                add("display_formula", n, line[a:b][:48], "$$…$$ is written on a line of its own (centred in layouts, about two "
                    "ruled lines): textbook look; students write formulas inline and keep going after 所以 / 得.",
                    "Write it as $…$ right after the words before it (所以 $…$, 得 $…$); long =-chains are split so they "
                    "continue on the next line.")
        masked = list(line)
        for a, b, _ in spans:
            masked[a:b] = ["\x01"] * (b - a)
        m = "".join(masked)
        hits = [k for k, ch in enumerate(m) if ch in _STOPS
                and not re.fullmatch(r"\s*[(（]?\d+[)）]?", m[:k])                    # numbering: 1．
                and not (m[k - 1:k].isdigit() and m[k + 1:k + 2].isdigit())]       # decimal: 3．5
        st = m.rstrip()
        if st.endswith(".") and not st.endswith(".."):
            prev = st[:-1].rstrip()[-1:]
            if prev == "\x01" or _cjk(prev):
                hits.append(len(st) - 1)
        if hits:
            k = hits[0]
            add("full_stop", n, line[max(0, k - 14):k + 4], f"{len(hits)} full stop(s): students don't write 。 or . in math "
                "solutions.", "Use ， between clauses and nothing at the end of a line.")
        if n + 1 < len(paras):
            s, nxt = line.rstrip(), paras[n + 1].lstrip()
            lead = next((x for x in _LEADS if s.endswith(x)), "")
            if lead in ("，", ",") and s[:-1].rstrip().endswith("$"):
                lead = ""                                     # '$x=1$，' ⏎ '$y=2$' is one step per line, not a cut-off lead
            if lead and nxt.startswith("$") and not nxt.startswith("$$"):
                add("orphan_lead", n, s[-12:] + " ⏎ " + nxt[:24], f"The line ends with 「{lead}」 and its formula starts the next "
                    "paragraph; students write the formula right after it on the same line.",
                    f"Join paragraphs {n + first} and {n + first + 1} into one (… {lead} $…$); a long formula then continues on the "
                    "next line by itself.")
    return out


def math_style_note(items: list[dict]) -> str:
    """One line for next / attention."""
    names = {"display_formula": "$$ display formula", "full_stop": "paragraph with full stops", "orphan_lead": "所以/得/： line cut off from its formula"}
    counts: dict[str, int] = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    what = ", ".join(f"{v}× {names.get(k, k)}" for k, v in counts.items())
    return (f"Math style: {what} — typeset-looking, not how students write (see math_style). Fix the text first, unless the "
            "user wants it that way.")


SPLIT_NOTE = ("Long formulas were cut into as few pieces as possible ($I(a)$ $=\\int…$: the left side, then the rest; a piece "
              "longer than a line again at =, ≤ … or +) so they start right after 所以 / 得 and continue on the next line like "
              "a student's; formatting only (the text sent, text.txt and layout.json have the pieces). --keep-formulas keeps them whole.")


def _math_prep(a, text: str, model: str) -> tuple[str, dict]:
    """logic-1 plain text: split long formulas (unless --keep-formulas) and lint the style -> (text to send, output fields)."""
    fmt: dict = {}
    if model != "logic-1":
        return text, fmt
    lint = lint_math(text)                                  # on the text as written, so snippets match the agent's source
    if not getattr(a, "keep_formulas", False):
        text, splits = split_long_math(text, PLAIN_LINE_W.get(getattr(a, "size", "medium"), LINE_W))
        if splits:
            fmt.update(formula_splits=splits, formula_note=SPLIT_NOTE)
    if lint:
        fmt["math_style"] = lint
    return text, fmt


def _layout_texts(spec: dict) -> list[tuple[str, int, str, str, int]]:
    """(kind, box index, box id, text, number of its first paragraph) of a raw layout's texts: layout.text and box "text"
    are numbered on as one text (expand_layout joins them in this order), box "own" texts on their own."""
    out, first, boxes = [], 1, spec.get("boxes") or []
    if spec.get("text"):
        out.append(("text", -1, "", spec["text"], 1))
        first += spec["text"].count("\n") + 1
    for n, b in enumerate(boxes):
        if "text" in b:
            t = str(b["text"])
            out.append(("box", n, str(b.get("id") or f"X{n + 1}"), t, first))
            first += t.count("\n") + 1
    for n, b in enumerate(boxes):
        if isinstance(b.get("own"), str) and b["own"]:
            out.append(("own", n, str(b.get("id") or f"X{n + 1}"), b["own"], 1))
    return out


def _joined(spec: dict) -> str:
    """layout.text + box texts as expand_layout joins them (what match marks search)."""
    return "\n".join(([spec["text"]] if spec.get("text") else []) + [str(b["text"]) for b in spec.get("boxes") or [] if "text" in b])


def _count(text: str, sub: str) -> int:
    """Occurrences as expand_layout's match marks find them (overlapping)."""
    n, i = 0, text.find(sub) if sub else -1
    while i >= 0:
        n, i = n + 1, text.find(sub, i + 1)
    return n


def split_layout_math(spec: dict) -> tuple[dict, dict]:
    """split_long_math for a layout BEFORE expand_layout, so line / match marks still find their words: layout.text,
    box "text" and "own" texts. Raw start/end/range positions would shift, so a layout with them keeps its formulas
    (own texts are separate and are still split). Returns (new spec, output fields)."""
    new = json.loads(json.dumps(spec))
    raw = [f"mark {m.get('id', k + 1)}" for k, m in enumerate(new.get("marks") or []) if "start" in m or "end" in m]
    raw += [f"block {bl.get('id')}" for bl in new.get("blocks") or [] if "start" in bl or "end" in bl]
    raw += [f"box {b.get('id')} range" for b in new.get("boxes") or [] if "range" in b]
    splits, note = [], ""
    lw = lambda kind, n: layout_line_w(new, (new.get("boxes") or [])[n] if kind != "text" else None)   # noqa: E731
    parts = [(kind, n, bid, first, split_long_math(t, lw(kind, n))) for kind, n, bid, t, first in _layout_texts(new) if kind != "own"]
    if any(sp for *_, (_, sp) in parts):
        if raw:
            note = (f"Long formulas were NOT split: this layout positions text by raw numbers ({', '.join(raw[:4])}) that would "
                    "shift. Use line / match marks and box \"text\" instead, or split long =-chains yourself ($A=B$ $=C$).")
        else:
            trial = json.loads(json.dumps(new))
            for kind, n, _, _, (t2, _) in parts:
                if kind == "text":
                    trial["text"] = t2
                else:
                    trial["boxes"][n]["text"] = t2
            before, after = _joined(new), _joined(trial)
            for m in trial.get("marks") or []:
                if "match" not in m:
                    continue
                sub, nth = str(m["match"]), int(m.get("nth", 1))
                n0 = _count(before, sub)                      # all occurrences kept -> the nth one is still the same place
                if _count(after, sub) == n0 or n0 < max(1, nth):
                    continue
                alt = split_long_math(sub, lw("text", -1))[0]
                if alt != sub and _count(after, alt) == n0:
                    m["match"] = alt                          # the mark covered whole formulas: follow their pieces
                else:
                    note = (f"Long formulas were NOT split: the match mark '{sub[:30]}' runs across a split point. Match a whole "
                            "formula or other words, or split long =-chains yourself ($A=B$ $=C$).")
                    break
            if not note:
                new = trial
                for kind, _, bid, first, (_, sp) in parts:
                    splits += [{"paragraph": x["paragraph"] + first - 1, "pieces": x["pieces"], **({"box": bid} if bid else {})}
                               for x in sp]
    for kind, n, bid, t, _ in _layout_texts(new):
        if kind == "own":
            t2, sp = split_long_math(t, layout_line_w(new, new["boxes"][n]))
            if sp:
                new["boxes"][n]["own"] = t2
                splits += [{"paragraph": x["paragraph"], "pieces": x["pieces"], "box": bid} for x in sp]
    fmt: dict = {}
    if splits:
        fmt.update(formula_splits=splits, formula_note=SPLIT_NOTE)
    if note:
        fmt["formula_note"] = note + (" (Box own texts were split.)" if splits else "")
    return new, fmt


def lint_layout(spec: dict) -> list[dict]:
    """lint_math over a raw layout's texts (paragraphs numbered as in _layout_texts; box texts carry their box id)."""
    out = []
    for _, _, bid, t, first in _layout_texts(spec):
        out += lint_math(t, first, bid or None)
    return out


def expand_layout(spec: dict) -> dict:
    """Skill shorthand -> the API's layout format (so nobody has to count character positions by hand).
    - a box may carry its own "text": it becomes a block of the page text, flowed into that box;
    - box fields may be omitted: kind "rect", page 0, rot 0, order 1, block null, form {};
    - a mark may say {"line": 0} (a whole paragraph, negative = from the end, or [first, last]) or
      {"match": "some words", "nth": 1} instead of start/end; "id" may be omitted.
    Without a top-level "text", the page text is just the boxes' texts and nothing else is written on the page."""
    spec = json.loads(json.dumps(spec))
    boxes = spec.get("boxes") or []
    body = spec.get("text") or ""
    blocks = list(spec.get("blocks") or [])
    parts = [body] if body else []
    pos = u16len(body)
    for n, b in enumerate(boxes):
        b.setdefault("id", f"X{n + 1}")
        b.setdefault("kind", "rect")
        b.setdefault("page", 0)
        b.setdefault("rot", 0)
        b.setdefault("order", 1)
        b.setdefault("form", {})
        for k in ("x", "y", "w", "h"):
            b.setdefault(k, 0)
        if "text" in b:
            t = str(b.pop("text"))
            if parts:
                pos += 1                                  # the line break joining it to what came before
            bid = f"B_{b['id']}"[:24]
            blocks.append({"id": bid, "start": pos, "end": pos + u16len(t)})
            parts.append(t)
            pos += u16len(t)
            b["block"] = bid
        b.setdefault("block", None)
    if boxes:
        spec["boxes"] = boxes
    if blocks:
        spec["blocks"] = blocks
    if not body and any(bl["id"].startswith("B_") for bl in blocks):
        spec.setdefault("d", {}).setdefault("fillRest", False)
        spec["d"].setdefault("indent", 0)                      # answers / captions: no paragraph indent
    text = "\n".join(parts) if parts else body
    spec["text"] = text
    paras = text.split("\n")
    starts, acc = [], 0
    for para in paras:
        starts.append(acc)
        acc += u16len(para) + 1
    marks = []
    for n, m in enumerate(spec.get("marks") or []):
        m.setdefault("id", f"M{n + 1}")
        m.setdefault("f", {})
        if "line" in m:
            ln = m.pop("line")
            a, z = (ln, ln) if isinstance(ln, int) else (ln[0], ln[-1])
            a, z = (a + len(paras) if a < 0 else a), (z + len(paras) if z < 0 else z)
            if not (0 <= a <= z < len(paras)):
                fail("bad_mark", f"mark {m['id']}: line {ln} doesn't exist (the text has {len(paras)} lines, counted from 0)", 2)
            m["start"], m["end"] = starts[a], starts[z] + u16len(paras[z])
        elif "match" in m:
            sub, nth = str(m.pop("match")), int(m.pop("nth", 1))
            i = -1
            for _ in range(max(1, nth)):
                i = text.find(sub, i + 1)
                if i < 0:
                    fail("bad_mark", f"mark {m['id']}: '{sub}' (occurrence {nth}) is not in the text", 2)
            m["start"], m["end"] = u16len(text[:i]), u16len(text[:i]) + u16len(sub)
        marks.append(m)
    if marks:
        spec["marks"] = marks
    return spec


def _load_layout(path: str, a) -> tuple[dict, bool, dict]:
    """Read a layout file, pick the model (sets a.model), split long formulas for logic-1 (before the shorthand is
    expanded, unless --keep-formulas), expand the shorthand and lint the math style.
    Returns (spec to send, model chosen automatically, output fields: formula_splits / formula_note / math_style)."""
    p = Path(path)
    if not p.exists():
        fail("file_not_found", f"layout file not found: {p}", 2)
    try:
        spec = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        fail("bad_layout_json", f"{p} is not valid JSON: {e}", 2)
    if "layout" in spec and "text" not in spec and "boxes" not in spec:   # accept a full request body too
        spec = spec["layout"]
    check_escapes(str(spec.get("text") or ""), f"{p} (layout.text)")
    for b in spec.get("boxes", []):
        if isinstance(b.get("text"), str):
            check_escapes(b["text"], f"{p} (box {b.get('id')} text)")
        if isinstance(b.get("own"), str):
            check_escapes(b["own"], f"{p} (box {b.get('id')} own text)")
    a.model, auto = pick_model(a, "\n".join(t for *_, t, _ in _layout_texts(spec)))
    fmt: dict = {}
    if a.model == "logic-1":
        lint = lint_layout(spec)                            # on the layout as written (snippets match the file)
        if not getattr(a, "keep_formulas", False):
            spec, fmt = split_layout_math(spec)
        if lint:
            fmt["math_style"] = lint
    return expand_layout(spec), auto, fmt


def _quote(text: str, model: str, size: str, style: str | None = None) -> dict:
    body = {"text": text, "model": model, "size": size}
    if style:
        body["style"] = str(style)                        # custom style: the quote also flags chars it can't write
    return api("POST", "/quote", body)


MIN_CHARS = 100                                                    # every job is billed as at least 100 characters


def price_cents(chars: int) -> int:
    """List price in cents: ¥0.002 per character (0.2 cents; ¥2 / 1000), at least 100 characters, rounded up to the cent.
    Same price on the website and the API since 2026-09-28."""
    return -(-max(MIN_CHARS, int(chars or 0)) // 5)


def quota_cents(d: dict | None) -> int:
    """Quota in cents (remaining on an account, used by a job) — only older servers have one. Inko is pure pay-as-you-go
    now: current servers send quota_cents = 0 (kept for old clients, to be dropped); servers from before 2026-09-28 only
    quota_pages (1 page = ¥1)."""
    d = d or {}
    if d.get("quota_cents") is not None:
        return int(d["quota_cents"])
    return int(round(float(d.get("quota_pages") or 0) * 100))


def _money(acc: dict | None) -> dict:
    """The account's money for doctor / auth: the balance; a quota only if an older server still reports one; whether
    label:none is allowed when the server says so."""
    acc = acc or {}
    out = {"balance_cents": acc.get("balance_cents")}
    q = quota_cents(acc)
    if q:
        out["quota_cents"] = q                                    # older server: used before the balance
    if "can_remove_label" in acc:
        out["can_remove_label"] = acc["can_remove_label"]
    return out


def payment(chars: int, list_cents: int, account: dict | None) -> str:
    """How the job will be paid. Pure pay-as-you-go: ¥0.002 per character from the account balance (the server uses the
    gift balance before topped-up money). An older server that still reports quota (quota_cents / quota_pages > 0)
    uses it first, at the same price."""
    acc = account or {}
    need = int(list_cents or 0) or price_cents(chars)
    quota = quota_cents(acc)
    if quota >= need:
        return f"¥{need / 100:.2f} of quota (¥{(quota - need) / 100:.2f} quota left after this job); balance untouched"
    if acc.get("balance_cents") is None:
        return (f"all ¥{quota / 100:.2f} remaining quota + ¥{(need - quota) / 100:.2f} from the balance" if quota > 0
                else f"¥{need / 100:.2f} from the balance")
    bal = int(acc.get("balance_cents") or 0)
    rest = need - quota
    head = (f"all ¥{quota / 100:.2f} remaining quota + ¥{rest / 100:.2f} from the balance" if quota > 0
            else f"¥{need / 100:.2f} from the balance")
    if bal < rest:
        return (f"{head}, but only ¥{bal / 100:.2f} is available: the user must top up at least ¥{(rest - bal) / 100:.2f} "
                "on inkotype.com first")
    return f"{head} (¥{bal / 100:.2f} available, ¥{(bal - rest) / 100:.2f} left after this job)"


def _summarize_quote(q: dict) -> dict:
    lc = (q.get("price") or {}).get("list_cents", 0)
    out = {"ok": q.get("ok"), "chars": q.get("chars"), "formulas": q.get("formulas"), "pages_est": q.get("pages_est"),
           "price_cny": round(lc / 100, 2), "list_cents": lc, "payment": payment(q.get("chars") or 0, lc, q.get("account")),
           "account": q.get("account"),
           "errors": [{k: e.get(k) for k in ("kind", "symbol", "paragraph", "snippet", "message")} for e in q.get("errors", [])],
           "warnings": [{k: e.get(k) for k in ("kind", "symbol", "snippet", "message")} for e in q.get("warnings", [])]}
    for k in ("style", "style_note"):                   # which handwriting the job will use (source: request / default / system)
        if q.get(k):
            out[k] = q[k]
    return out


def style_hint(q: dict) -> str:
    """No --style: say which handwriting Inko will use — the account's 常用字迹, or its fallback."""
    st = q.get("style") or {}
    if st.get("source") == "default":
        return (f"No --style: the user's 常用字迹 {st.get('label')} will be used (their saved default). Mention it; pass --style "
                "only if they want something else this time.")
    if st.get("source") == "system":
        why = f" ({q['style_note']})" if q.get("style_note") else " (the user hasn't set one)"
        return (f"No --style and no usable 常用字迹{why}: Inko would fall back to {st.get('label')}"
                + (", a very casual, joined-up hand" if st.get("label") == "No.001" else "")
                + ". Choose on purpose: the user's favourites first (`inko.py styles --favorites`), else by description "
                  "(references/styles.md).")
    return ("No --style given: Inko uses the account's 常用字迹 if one is set, else its default (Lyric No.001 — very casual "
            "and joined-up; Logic: the lowest-numbered of your 8). Pick a handwriting on purpose (references/styles.md).")


def pick_model(a, text: str) -> tuple[str, bool]:
    """--model if given; otherwise logic-1 when the text has formulas ($…$), else lyric-1 (more handwritings)."""
    if getattr(a, "model", None):
        return a.model, False
    return ("logic-1" if "$" in text else "lyric-1"), True


def cmd_quote(a) -> None:
    text = _read_text(a)
    model, auto = pick_model(a, text)
    text, fmt = _math_prep(a, text, model)                  # logic-1: long formulas split, math style lint
    out = _summarize_quote(_quote(text, model, a.size, a.style))
    out["model"] = model + (" (chosen automatically: " + ("formulas found" if model == "logic-1" else "no formulas") + ")" if auto else "")
    if not a.style:
        out["style_hint"] = style_hint(out)
    out.update(fmt)
    emit(out)


def _layout_text(spec: dict) -> str:
    return "\n".join([spec.get("text", "")] + [b.get("own", "") for b in spec.get("boxes", []) if b.get("own")])


def cmd_layout(a) -> None:
    spec, _, fmt = _load_layout(a.spec, a)
    res = api("POST", "/layout", {"model": a.model, "layout": spec})
    out = {"pages": res.get("pages"), "chars": res.get("chars"), "formulas": res.get("formulas"), "unplaced": res.get("unplaced"),
           "warnings": res.get("warnings"),
           "price_cny_est": ((res.get("price") or {}).get("list_cents") if (res.get("price") or {}).get("list_cents") is not None
                             else price_cents(res.get("chars") or 0)) / 100,
           "model": a.model, **fmt}
    issues = _summarize_quote(_quote(_layout_text(spec), a.model, "medium", getattr(a, "style", None)))   # free: unwritable symbols
    out["writable"] = issues["ok"]
    if issues["errors"] or issues["warnings"]:
        out["errors"], out["symbol_warnings"] = issues["errors"], issues["warnings"]
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        out["plan_file"] = a.out
    if a.preview:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import preview as PV  # noqa: WPS433
            files = PV.render(res, a.preview, scale=a.preview_scale, spec=spec)
            out["preview_files"] = files
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            out["preview_error"] = str(e)
    if out.get("unplaced"):
        out["attention"] = f"{out['unplaced']} characters don't fit anywhere — enlarge boxes, add pages/boxes, or shorten the text before generating."
    if not out["writable"]:
        out["attention"] = (out.get("attention", "") + " Some characters/symbols can't be written — see errors "
                            "(references/writing-math.md → substitutions).").strip()
    if fmt.get("math_style"):
        out["attention"] = (out.get("attention", "") + " " + math_style_note(fmt["math_style"])).strip()
    emit(out)


def _pen(a) -> dict | None:
    pen = {}
    if a.pen_type:
        pen["type"] = a.pen_type
    if a.pen_color:
        pen["color"] = a.pen_color
    if a.pen_weight is not None:
        pen["weight"] = a.pen_weight
    if a.pen_ink is not None:
        pen["ink"] = a.pen_ink
    return pen or None


def _out_dir(a, job_id: str) -> Path:
    if a.out:
        return Path(a.out)
    return Path.cwd() / "inko-output" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{job_id[:8]}"


def _save_files(job: dict, out: Path, what: set[str]) -> dict:
    files = job.get("files") or {}
    if files.get("expired"):
        fail("files_expired", "Result files are older than 30 days and were deleted.", 1)
    out.mkdir(parents=True, exist_ok=True)
    got = {"pages": [], "pdf": None}
    if "png" in what or "webp" in what:
        for p in files.get("pages", []):
            for kind in ("png", "webp"):
                if kind in what and p.get(kind):
                    path = out / f"page-{p['index'] + 1}.{kind}"
                    _download(p[kind], path)
                    got["pages"].append(str(path))
    if "pdf" in what and files.get("pdf"):
        path = out / "inko.pdf"
        _download(files["pdf"], path)
        got["pdf"] = str(path)
    got["scene"] = None
    if "scene" in what and files.get("scene"):              # editable glyph package (references/scene.md)
        path = out / "scene.zip"
        _download(files["scene"], path)
        got["scene"] = str(path)
    (out / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    return got


def _wait(job_id: str, timeout: float, interval: float) -> dict:
    deadline = time.time() + timeout
    last = ""
    while True:
        job = api("GET", f"/generations/{job_id}")
        st = job.get("status")
        if st in TERMINAL:
            return job
        pr = job.get("progress") or {}
        msg = f"{st}" + (f" · queue #{job.get('queue_position')}" if st == "queued" and job.get("queue_position") else "") \
              + (f" · {pr.get('stage')}" if pr.get("stage") else "") + (f" {pr.get('done')}/{pr.get('total')}" if pr.get("total") else "")
        if msg != last:
            note(f"[{dt.datetime.now():%H:%M:%S}] {msg}")
            last = msg
        if time.time() > deadline:
            fail("wait_timeout", f"job {job_id} still {st} after {timeout:.0f}s; run `inko.py wait {job_id}` again later", 3, job_id=job_id)
        time.sleep(interval)


def _finish(job: dict, a, extra: dict | None = None, info: dict | None = None) -> None:
    if job.get("status") != "succeeded":
        log_job(job)
        emit({"ok": False, "job_id": job["id"], "status": job.get("status"), "error": job.get("error"),
              "note": "Failed or canceled jobs are refunded automatically."})
        sys.exit(1)
    out = _out_dir(a, job["id"])
    what = set((getattr(a, "what", None) or "png,pdf,scene").split(","))
    got = _save_files(job, out, what)
    extra = extra or recall_plan(job["id"]) or recall_plan(job.get("rewrite_of") or "")
    for name, obj in (extra or {}).items():                  # layout.json + plan.json (compose.py) / text.txt (what was sent)
        (out / name).write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    log_job(job, {"out": str(out)})
    if job.get("rewrites_left"):
        nxt = ("Look at the PNG(s) before handing them over. If a few characters came out badly, `inko.py rewrite " + job["id"]
               + " --yes` re-writes the whole job once for free (new seed: other characters change too — compare both versions).")
    else:
        nxt = ("Look at the PNG(s) before handing them over. No free rewrite is left for this job (a rewrite can't be rewritten): "
               "keep whichever version is better, or generate again with another --seed (paid).")
    if got.get("scene"):
        nxt += (" Spacing / position / pen problems (not wrong characters): fix them locally without paying again — "
                "`scene.py inspect` / `tighten` / `drift` / `move` / `pen`, `scene.py editor` for dragging by hand, then "
                "`scene.py render` (references/scene.md).")
    if (info or {}).get("math_style"):
        nxt += " " + math_style_note(info["math_style"])
    emit({"ok": True, "job_id": job["id"], "status": "succeeded", "pages": job.get("pages"), "out_dir": str(out),
          "png": got["pages"], "pdf": got["pdf"], **({"scene": got["scene"]} if got.get("scene") else {}), "cost_cny": round((job.get("cost_cents") or 0) / 100, 2),
          **({"quota_used_cny": round(quota_cents(job) / 100, 2)} if quota_cents(job) else {}),     # older servers only
          "style": job.get("style"), "params": job.get("params"),
          **{k: v for k, v in (info or {}).items() if k != "style"},
          "rewrites_left": job.get("rewrites_left"), "is_rewrite": job.get("is_rewrite"), "files_expire_at": job.get("expires_at"),
          "next": nxt})


def cmd_generate(a) -> None:
    plan = None
    if a.layout:
        spec, model_auto, fmt = _load_layout(a.layout, a)     # logic-1: long formulas split, math style lint
        text = spec.get("text", "")
        body: dict = {"model": a.model, "label": a.label, "layout": spec}
    else:
        text = _read_text(a)
        a.model, model_auto = pick_model(a, text)
        text, fmt = _math_prep(a, text, a.model)
        body = {"model": a.model, "size": a.size, "paper": a.paper, "label": a.label, "text": text}
    if a.style:
        body["style"] = str(a.style)
    if a.seed is not None:
        body["seed"] = a.seed
    if not getattr(a, "no_scene", False):
        body["scene"] = True                                 # also deliver scene.zip (editable glyphs); servers without it ignore this
    pen = _pen(a)
    if pen:
        body["pen"] = pen
    # 1) always check first (free): unsupported symbols, price
    if a.layout:
        plan = api("POST", "/layout", {"model": a.model, "layout": body["layout"]})
        pr = plan.get("price") or {}                         # servers since 09-28: billed like the job (formulas by the symbols written)
        chars = pr.get("amount") if pr.get("amount") is not None else (plan.get("chars") or 0)
        cents = pr.get("list_cents") if pr.get("list_cents") is not None else price_cents(chars)   # ¥0.002 / char, min 100
        q = {"ok": not plan.get("unplaced"), "chars": chars, "pages_est": plan.get("pages"), "list_cents": cents,
             "price_cny": round(cents / 100, 2), "unplaced": plan.get("unplaced"), "warnings": plan.get("warnings")}
        page_style = (spec.get("d") or {}).get("style")               # a layout's page-wide handwriting beats the 常用字迹
        issues = _summarize_quote(_quote(_layout_text(body["layout"]), a.model, a.size, a.style or page_style))
        q["errors"], q["symbol_warnings"] = issues["errors"], issues["warnings"]
        q["ok"] = q["ok"] and issues["ok"]
        q["account"] = issues.get("account")
        q["payment"] = payment(chars, cents, q["account"])
        q.update({k: issues[k] for k in ("style", "style_note") if k in issues})
    else:
        page_style = None
        q = _summarize_quote(_quote(text, a.model, a.size, a.style))
    q["model"] = a.model + (" (chosen automatically: " + ("formulas found" if a.model == "logic-1" else "no formulas") + ")" if model_auto else "")
    if not a.style:
        q["style_hint"] = f"No --style: the layout's d.style {page_style} is used for the page." if page_style else style_hint(q)
    ms = (" " + math_style_note(fmt["math_style"])) if fmt.get("math_style") else ""
    if not q.get("ok"):
        emit({"submitted": False, "reason": "text_not_writable" if q.get("errors") else "layout_problem", "quote": q, **fmt,
              "next": "Fix the listed characters/symbols (see references/writing-math.md → substitutions) or the layout, then run again." + ms})
        sys.exit(1)
    if a.max_cents is not None and (q.get("list_cents") or 0) > a.max_cents:
        emit({"submitted": False, "reason": "over_budget", "quote": q, **fmt, "max_cents": a.max_cents})
        sys.exit(1)
    if not a.yes:
        emit({"submitted": False, "reason": "confirmation_required", "quote": q, **fmt,
              "request": {k: v for k, v in body.items() if k not in ("text", "layout")},
              "next": "Tell the user the price and settings; after they agree, run the same command again with --yes." + ms})
        note("NOT SUBMITTED: confirm the price with the user, then add --yes")
        return
    # 2) submit with an idempotency key (safe to retry)
    idem = a.idempotency_key or "skill-" + hashlib.sha256((json.dumps(body, sort_keys=True, ensure_ascii=False) + str(uuid.uuid4())).encode()).hexdigest()[:40]
    try:
        job = call("POST", "/generations", body, headers={"Idempotency-Key": idem})
    except ApiError as e:
        fail(e.code, e.message, 1, http=e.status, request_id=e.request_id, hint=HINTS.get(e.code, ""))
    log_job(job, {"idempotency_key": idem})
    extra = {"layout.json": body["layout"], "plan.json": plan} if plan else {"text.txt": text}
    remember_files(job["id"], extra)
    info = {**{k: job[k] for k in ("style", "style_source", "style_note") if job.get(k)}, **fmt}
    note(f"submitted job {job['id']} ({job.get('chars')} chars, est. {job.get('pages_est')} page(s))")
    if a.no_wait:
        emit({"submitted": True, "job_id": job["id"], "status": job.get("status"), "queue_position": job.get("queue_position"),
              **info, "next": f"python inko.py wait {job['id']}"})
        return
    _finish(_wait(job["id"], a.timeout, a.interval), a, extra, info)


def cmd_wait(a) -> None:
    _finish(_wait(a.job_id, a.timeout, a.interval), a)


def cmd_download(a) -> None:
    job = api("GET", f"/generations/{a.job_id}")
    if job.get("status") != "succeeded":
        fail("not_ready", f"job is {job.get('status')}; use `inko.py wait {a.job_id}`", 1)
    _finish(job, a)


def cmd_jobs(a) -> None:
    data = api("GET", f"/generations?limit={a.limit}")["data"]
    emit({"jobs": [{"id": j["id"], "status": j["status"], "model": j["model"], "chars": j.get("chars"), "pages": j.get("pages"),
                    "cost_cny": round((j.get("cost_cents") or 0) / 100, 2), "created_at": j.get("created_at"),
                    "rewrites_left": j.get("rewrites_left"), "text_preview": j.get("text_preview")} for j in data]})


def cmd_rewrite(a) -> None:
    if not a.yes:
        emit({"submitted": False, "reason": "confirmation_required",
              "next": "A rewrite is free (once per job) but replaces nothing — it creates a new job with a new seed. Re-run with --yes."})
        return
    job = api("POST", f"/generations/{a.job_id}/rewrite")
    log_job(job, {"rewrite_of": a.job_id})
    old = recall_plan(a.job_id) or {}
    remember_files(job["id"], {**old, "rewrite_of.txt": a.job_id})
    if a.no_wait:
        emit({"submitted": True, "job_id": job["id"], "status": job["status"], "rewrite_of": a.job_id})
        return
    _finish(_wait(job["id"], a.timeout, a.interval), a)


def cmd_cancel(a) -> None:
    job = api("POST", f"/generations/{a.job_id}/cancel")
    emit({"job_id": job["id"], "status": job["status"], "note": "The amount frozen for this job goes back to the balance."})


# ── argparse ────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(prog="inko.py", description="Inko handwriting API client", formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("doctor", help="check environment, key and connectivity")
    s = sp.add_parser("auth", help="save and verify an API key")
    s.add_argument("--key", help="the key (prefer piping it on stdin so it stays out of shell history)")
    s.add_argument("--project", action="store_true", help="store in ./.inko/key (git-ignored) instead of the user config dir")
    sp.add_parser("account")
    s = sp.add_parser("models")
    s.add_argument("--symbols", action="store_true", help="include Logic's stable/beta math symbol lists")
    s = sp.add_parser("styles")
    s.add_argument("--model", choices=["lyric-1", "logic-1"])
    s.add_argument("--where", default="", help='facet filters, e.g. "neat>=60 beauty>=50 joined<=40"')
    s.add_argument("--sort", help="facet to sort by, highest first (e.g. beauty, neat); add --asc for lowest first")
    s.add_argument("--asc", action="store_true", help="sort ascending")
    s.add_argument("--kind", choices=["preset", "custom"], help="custom = the user's own handwritings (made on the website)")
    s.add_argument("--favorites", action="store_true", help="only the handwritings the user saved as favourites (收藏)")
    s.add_argument("--limit", type=int, default=20)
    s = sp.add_parser("default-style", help="show / set / clear the account's 常用字迹 (used when --style is omitted)")
    s.add_argument("ref", nargs="?", help="preset code (e.g. 12) or custom style id to make the 常用字迹; omit to show it")
    s.add_argument("--clear", action="store_true", help="remove the 常用字迹")
    s = sp.add_parser("previews")
    s.add_argument("codes", nargs="+", help="style ids/codes, e.g. 12 37")
    s.add_argument("--model", choices=["lyric-1", "logic-1"])
    s.add_argument("--page", action="store_true", help="download the full-page sample instead of the card")
    s.add_argument("--out", default="inko-previews")
    s.add_argument("--no-sheet", action="store_true")

    def text_args(x):
        g = x.add_mutually_exclusive_group()
        g.add_argument("--text")
        g.add_argument("--file", help="UTF-8 text file")
        x.add_argument("--model", choices=["lyric-1", "logic-1"], help="default: logic-1 if the text has $…$ formulas, else lyric-1")
        x.add_argument("--size", default="medium", choices=["small", "medium", "large"])
        x.add_argument("--style", help="preset code like 12, or a custom style id (omit = the account's 常用字迹, else Inko's default)")
        x.add_argument("--keep-formulas", action="store_true", help="logic-1: don't split long =-chains into pieces that can wrap")

    s = sp.add_parser("quote")
    text_args(s)
    s = sp.add_parser("layout")
    s.add_argument("--spec", required=True, help="layout JSON (the `layout` object, or a whole request body)")
    s.add_argument("--model", choices=["lyric-1", "logic-1"], help="default: logic-1 if the text has $…$ formulas, else lyric-1")
    s.add_argument("--out", help="save the full plan JSON here")
    s.add_argument("--preview", help="render a local preview PNG (e.g. preview.png; multi-page adds -2, -3 …)")
    s.add_argument("--preview-scale", type=float, default=6.0, help="preview pixels per mm")
    s.add_argument("--keep-formulas", action="store_true", help="logic-1: don't split long =-chains into pieces that can wrap")

    s = sp.add_parser("generate")
    text_args(s)
    s.add_argument("--layout", help="layout JSON for custom placement (text comes from layout.text)")
    s.add_argument("--paper", default="white", choices=["white", "cream", "grid"], help="background for plain generation")
    s.add_argument("--label", default="visible", choices=["visible", "none"],
                   help="none = no visible AI label: only for accounts that bought a custom-handwriting seat and signed the "
                        "AI-labelling agreement on inkotype.com (else 403 label_required)")
    s.add_argument("--seed", type=int)
    s.add_argument("--pen-type", choices=["original", "gel", "ballpoint", "fountain", "pencil"])
    s.add_argument("--pen-color", choices=["black", "blue", "blueblack"])
    s.add_argument("--pen-weight", type=float, help="-1..1 (0 = as written)")
    s.add_argument("--pen-ink", type=float, help="-1..1 darkness (0 = as written)")
    s.add_argument("--yes", action="store_true", help="really submit (costs money); without it only the quote is shown")
    s.add_argument("--max-cents", type=int, help="refuse if the list price is above this")
    s.add_argument("--idempotency-key", help="reuse to retry a submit safely")
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("--out", help="download folder (default ./inko-output/<time>-<id>)")
    s.add_argument("--what", default="png,pdf,scene", help="files to download: png,pdf,webp,scene")
    s.add_argument("--no-scene", action="store_true", help="don't ask for scene.zip (the editable glyph package)")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("--interval", type=float, default=3)

    for name in ("wait", "download"):
        s = sp.add_parser(name)
        s.add_argument("job_id")
        s.add_argument("--out")
        s.add_argument("--what", default="png,pdf,scene")
        s.add_argument("--timeout", type=float, default=1800)
        s.add_argument("--interval", type=float, default=3)
    s = sp.add_parser("jobs")
    s.add_argument("--limit", type=int, default=10)
    s = sp.add_parser("rewrite")
    s.add_argument("job_id")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("--out")
    s.add_argument("--what", default="png,pdf,scene")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("--interval", type=float, default=3)
    s = sp.add_parser("cancel")
    s.add_argument("job_id")

    a = p.parse_args()
    if sys.platform == "win32":                                  # Chinese output on Windows consoles
        for st in (sys.stdout, sys.stderr):
            try:
                st.reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    {"doctor": cmd_doctor, "auth": cmd_auth, "account": cmd_account, "models": cmd_models, "styles": cmd_styles,
     "default-style": cmd_default_style, "previews": cmd_previews, "quote": cmd_quote, "layout": cmd_layout, "generate": cmd_generate, "wait": cmd_wait,
     "download": cmd_download, "jobs": cmd_jobs, "rewrite": cmd_rewrite, "cancel": cmd_cancel}[a.cmd](a)


if __name__ == "__main__":
    main()
