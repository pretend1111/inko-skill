#!/usr/bin/env python3
"""Inko API command-line client (standard library only).

    python inko.py doctor                         check python, packages, key, network
    python inko.py auth                           save + verify an API key (reads it from stdin or --key)
    python inko.py account                        balance / quota / key limits
    python inko.py models [--symbols]             models, pricing, math symbols Logic can write
    python inko.py styles [--model M] [--where "neat>=60 beauty>=50"] [--sort -beauty] [--limit 12]
    python inko.py previews 12 37 88 [--model M]  download sample images (+ one contact sheet) to show the user
    python inko.py quote (--text T | --file F) [--model M] [--size S]           free
    python inko.py layout --spec layout.json [--model M] [--preview p.png]       free: validate + plan a custom layout
    python inko.py generate (--text T | --file F | --layout layout.json) [options] [--yes]
    python inko.py wait JOB_ID [--out DIR]        poll until done, then download
    python inko.py download JOB_ID [--out DIR]
    python inko.py jobs [--limit N]
    python inko.py rewrite JOB_ID [--yes]         one free re-write per finished job (new random seed)
    python inko.py cancel JOB_ID

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

VERSION = "1.0.0"
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
            with urllib.request.urlopen(req, timeout=timeout) as r:
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
    "insufficient_balance": "Not enough balance/quota. The user must top up or subscribe on inkotype.com; don't retry.",
    "invalid_text": "Run `inko.py quote` to see exactly which characters/symbols are the problem and rewrite them.",
    "content_blocked": "The text looks like a forged-document risk (IOU, receipt, contract, signature, certificate, leave note…) "
                       "or violates policy. Tell the user it can't be generated; do not try to work around it.",
    "style_model_mismatch": "That style doesn't support the chosen model. Logic only has 8 styles per account: `inko.py styles --model logic-1`.",
    "style_not_found": "Unknown style. List available ones with `inko.py styles`.",
    "label_required": "label:none needs a membership/permanent custom style and a signed label agreement on the website. Use label visible.",
    "rate_limited": "Too many requests; wait and retry later.",
    "too_many_active": "The account already has the maximum number of running jobs; wait for them (inko.py jobs) and retry.",
    "daily_limit": "This key hit its daily spending cap; the user can raise it on the website or wait until tomorrow (Beijing time).",
    "queue_full": "Service is busy; retry in a few minutes.",
    "free_paused": "Free generation is paused at peak time; the user can top up or try later.",
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
            out["balance_cents"], out["quota_pages"] = acc.get("balance_cents"), acc.get("quota_pages")
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
          "balance_cents": acc.get("balance_cents"), "quota_pages": acc.get("quota_pages"),
          "daily_limit_cents": acc["key"].get("daily_limit_cents"), "membership": acc.get("membership")})


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
    data = api("GET", "/styles" + ("?" + urllib.parse.urlencode(q) if q else ""))["data"]
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
                     "facets": {k: f.get(k) for k in FACETS if k in f}, "neighbors": (s.get("neighbors") or [])[:5],
                     "preview_card": pv.get("card"), "preview_page": pv.get("page")})
    if a.sort:
        key = a.sort.lstrip("-+")
        if key not in FACETS:
            fail("bad_sort", f"sort by one of {', '.join(FACETS)}", 2)
        rows.sort(key=lambda r: r["facets"].get(key, -1), reverse=not a.asc)
    if not a.sort:
        rows.sort(key=lambda r: r["kind"] != "custom")           # the user's own handwritings first
    total = len(rows)
    emit({"count": total, "shown": min(total, a.limit), "styles": rows[:a.limit],
          "facets_help": "percentiles 0-100 among all styles: neat 潦草→工整, beauty 一般→好看(estimate), compact 舒展→紧凑, joined 一笔一划→连笔, "
                         "slant 正→右斜, aspect 扁→长, round 方折→圆润, weight 细→粗 (as written), ink 浅→深 (as written)"})


def _download(url: str, path: Path, timeout: float = 180) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
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


def _load_layout(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        fail("file_not_found", f"layout file not found: {p}", 2)
    try:
        spec = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        fail("bad_layout_json", f"{p} is not valid JSON: {e}", 2)
    if "layout" in spec and "text" not in spec and "boxes" not in spec:   # accept a full request body too
        spec = spec["layout"]
    for b in spec.get("boxes", []):
        if isinstance(b.get("text"), str):
            check_escapes(b["text"], f"{p} (box {b.get('id')} text)")
    spec = expand_layout(spec)
    check_escapes(spec.get("text", ""), f"{p} (layout.text)")
    for b in spec.get("boxes", []):
        if b.get("own"):
            check_escapes(b["own"], f"{p} (box {b.get('id')} own text)")
    return spec


def _quote(text: str, model: str, size: str, style: str | None = None) -> dict:
    body = {"text": text, "model": model, "size": size}
    if style and not style.isdigit():
        body["style"] = style                             # custom style: quote also flags chars it can't write
    return api("POST", "/quote", body)


def payment(chars: int, list_cents: int, account: dict | None) -> str:
    """How the job will be paid: member quota first (500 chars = 1 page), then gift / paid balance."""
    acc = account or {}
    need = math.ceil(max(100, chars or 0) / 5) / 100               # quota is charged in 0.01-page steps, rounded up
    quota = float(acc.get("quota_pages") or 0)
    bal = int(acc.get("balance_cents") or 0)
    if quota >= need:
        return f"{need:.2f} pages of member quota (worth ¥{list_cents / 100:.2f} at list price); cash balance untouched"
    if quota > 0:
        rest = -(-(max(100, chars) - int(quota * 500)) * 180 // 1000)
        return f"all {quota:.2f} remaining quota pages + about ¥{rest / 100:.2f} from the balance (¥{bal / 100:.2f} available)"
    return f"¥{list_cents / 100:.2f} from the balance (¥{bal / 100:.2f} available)"


def _summarize_quote(q: dict) -> dict:
    lc = (q.get("price") or {}).get("list_cents", 0)
    return {"ok": q.get("ok"), "chars": q.get("chars"), "formulas": q.get("formulas"), "pages_est": q.get("pages_est"),
            "price_cny": round(lc / 100, 2), "list_cents": lc, "payment": payment(q.get("chars") or 0, lc, q.get("account")),
            "account": q.get("account"),
            "errors": [{k: e.get(k) for k in ("kind", "symbol", "paragraph", "snippet", "message")} for e in q.get("errors", [])],
            "warnings": [{k: e.get(k) for k in ("kind", "symbol", "snippet", "message")} for e in q.get("warnings", [])]}


def pick_model(a, text: str) -> tuple[str, bool]:
    """--model if given; otherwise logic-1 when the text has formulas ($…$), else lyric-1 (more handwritings)."""
    if getattr(a, "model", None):
        return a.model, False
    return ("logic-1" if "$" in text else "lyric-1"), True


def cmd_quote(a) -> None:
    text = _read_text(a)
    model, auto = pick_model(a, text)
    out = _summarize_quote(_quote(text, model, a.size, a.style))
    out["model"] = model + (" (chosen automatically: " + ("formulas found" if model == "logic-1" else "no formulas") + ")" if auto else "")
    emit(out)


def _layout_text(spec: dict) -> str:
    return "\n".join([spec.get("text", "")] + [b.get("own", "") for b in spec.get("boxes", []) if b.get("own")])


def cmd_layout(a) -> None:
    spec = _load_layout(a.spec)
    a.model, _ = pick_model(a, _layout_text(spec))
    res = api("POST", "/layout", {"model": a.model, "layout": spec})
    out = {"pages": res.get("pages"), "chars": res.get("chars"), "formulas": res.get("formulas"), "unplaced": res.get("unplaced"),
           "warnings": res.get("warnings"), "price_cny_est": -(-max(100, res.get("chars") or 0) * 180 // 1000) / 100,
           "model": a.model}
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


def _finish(job: dict, a, extra: dict | None = None) -> None:
    if job.get("status") != "succeeded":
        log_job(job)
        emit({"ok": False, "job_id": job["id"], "status": job.get("status"), "error": job.get("error"),
              "note": "Failed or canceled jobs are refunded automatically."})
        sys.exit(1)
    out = _out_dir(a, job["id"])
    what = set((getattr(a, "what", None) or "png,pdf").split(","))
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
    emit({"ok": True, "job_id": job["id"], "status": "succeeded", "pages": job.get("pages"), "out_dir": str(out),
          "png": got["pages"], "pdf": got["pdf"], "cost_cny": round((job.get("cost_cents") or 0) / 100, 2),
          "quota_pages_used": job.get("quota_pages"), "style": job.get("style"), "params": job.get("params"),
          "rewrites_left": job.get("rewrites_left"), "is_rewrite": job.get("is_rewrite"), "files_expire_at": job.get("expires_at"),
          "next": nxt})


def cmd_generate(a) -> None:
    plan = None
    if a.layout:
        spec = _load_layout(a.layout)
        text = spec.get("text", "")
        a.model, model_auto = pick_model(a, _layout_text(spec))
        body: dict = {"model": a.model, "label": a.label, "layout": spec}
    else:
        text = _read_text(a)
        a.model, model_auto = pick_model(a, text)
        body = {"model": a.model, "size": a.size, "paper": a.paper, "label": a.label, "text": text}
    if a.style:
        body["style"] = str(a.style)
    if a.seed is not None:
        body["seed"] = a.seed
    pen = _pen(a)
    if pen:
        body["pen"] = pen
    # 1) always check first (free): unsupported symbols, price
    if a.layout:
        plan = api("POST", "/layout", {"model": a.model, "layout": body["layout"]})
        chars = plan.get("chars") or 0
        cents = -(-max(100, chars) * 180 // 1000)            # ¥1.8 / 1000 chars, min 100 chars, rounded up to the cent
        q = {"ok": not plan.get("unplaced"), "chars": chars, "pages_est": plan.get("pages"), "list_cents": cents,
             "price_cny": round(cents / 100, 2), "unplaced": plan.get("unplaced"), "warnings": plan.get("warnings")}
        issues = _summarize_quote(_quote(_layout_text(body["layout"]), a.model, a.size, a.style))
        q["errors"], q["symbol_warnings"] = issues["errors"], issues["warnings"]
        q["ok"] = q["ok"] and issues["ok"]
        q["account"] = issues.get("account")
        q["payment"] = payment(chars, cents, q["account"])
    else:
        q = _summarize_quote(_quote(text, a.model, a.size, a.style))
    q["model"] = a.model + (" (chosen automatically: " + ("formulas found" if a.model == "logic-1" else "no formulas") + ")" if model_auto else "")
    if not a.style:
        q["style_note"] = ("No --style given: Inko would use its default (Lyric No.001 — very casual and joined-up; Logic: the "
                           "lowest-numbered of your 8). Pick a handwriting on purpose (references/styles.md).")
    if not q.get("ok"):
        emit({"submitted": False, "reason": "text_not_writable" if q.get("errors") else "layout_problem", "quote": q,
              "next": "Fix the listed characters/symbols (see references/writing-math.md → substitutions) or the layout, then run again."})
        sys.exit(1)
    if a.max_cents is not None and (q.get("list_cents") or 0) > a.max_cents:
        emit({"submitted": False, "reason": "over_budget", "quote": q, "max_cents": a.max_cents})
        sys.exit(1)
    if not a.yes:
        emit({"submitted": False, "reason": "confirmation_required", "quote": q, "request": {k: v for k, v in body.items() if k not in ("text", "layout")},
              "next": "Tell the user the price and settings; after they agree, run the same command again with --yes."})
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
    note(f"submitted job {job['id']} ({job.get('chars')} chars, est. {job.get('pages_est')} page(s))")
    if a.no_wait:
        emit({"submitted": True, "job_id": job["id"], "status": job.get("status"), "queue_position": job.get("queue_position"),
              "next": f"python inko.py wait {job['id']}"})
        return
    _finish(_wait(job["id"], a.timeout, a.interval), a, extra)


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
    emit({"job_id": job["id"], "status": job["status"], "note": "Frozen balance/quota is refunded."})


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
    s.add_argument("--limit", type=int, default=20)
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
        x.add_argument("--style", help="preset code like 12, or a custom style id")

    s = sp.add_parser("quote")
    text_args(s)
    s = sp.add_parser("layout")
    s.add_argument("--spec", required=True, help="layout JSON (the `layout` object, or a whole request body)")
    s.add_argument("--model", choices=["lyric-1", "logic-1"], help="default: logic-1 if the text has $…$ formulas, else lyric-1")
    s.add_argument("--out", help="save the full plan JSON here")
    s.add_argument("--preview", help="render a local preview PNG (e.g. preview.png; multi-page adds -2, -3 …)")
    s.add_argument("--preview-scale", type=float, default=6.0, help="preview pixels per mm")

    s = sp.add_parser("generate")
    text_args(s)
    s.add_argument("--layout", help="layout JSON for custom placement (text comes from layout.text)")
    s.add_argument("--paper", default="white", choices=["white", "cream", "grid"], help="background for plain generation")
    s.add_argument("--label", default="visible", choices=["visible", "none"])
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
    s.add_argument("--what", default="png,pdf", help="files to download: png,pdf,webp")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("--interval", type=float, default=3)

    for name in ("wait", "download"):
        s = sp.add_parser(name)
        s.add_argument("job_id")
        s.add_argument("--out")
        s.add_argument("--what", default="png,pdf")
        s.add_argument("--timeout", type=float, default=1800)
        s.add_argument("--interval", type=float, default=3)
    s = sp.add_parser("jobs")
    s.add_argument("--limit", type=int, default=10)
    s = sp.add_parser("rewrite")
    s.add_argument("job_id")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("--out")
    s.add_argument("--what", default="png,pdf")
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
     "previews": cmd_previews, "quote": cmd_quote, "layout": cmd_layout, "generate": cmd_generate, "wait": cmd_wait,
     "download": cmd_download, "jobs": cmd_jobs, "rewrite": cmd_rewrite, "cancel": cmd_cancel}[a.cmd](a)


if __name__ == "__main__":
    main()
