"""Shared helpers for the Inko skill scripts.

- Image I/O that carries Inko's AIGC labels along: the implicit label (PNG iTXt "AIGC" + XMP, JPEG/WebP XMP + EXIF)
  and the visible corner label 「AI生成 · Inko」. Every script saves through save_image() so labels survive editing.
- Units (mm <-> px), CJK font lookup, small numpy image utilities.

Only Pillow and numpy are required. Nothing here talks to the network.
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    from PIL.PngImagePlugin import PngInfo
except ImportError:  # pragma: no cover - explained to the agent instead
    sys.stderr.write("error: this script needs Pillow and numpy.  Install them with:\n"
                     "    python -m pip install pillow numpy\n")
    sys.exit(2)

Image.MAX_IMAGE_PIXELS = 400_000_000       # scans and phone photos can be large; still bounded

if sys.platform == "win32":                  # JSON with Chinese on Windows consoles / pipes: always UTF-8
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

A4_MM = (210.0, 297.0)
PX_PER_MM_300 = 300 / 25.4                 # Inko pages are A4 at 300 dpi: 2480 x 3508 px
LABEL_TEXT = "AI生成 · Inko"
LABEL_MIN_FRAC = 0.05                      # GB 45438-2025: visible label text height >= 5% of the shortest image side
PRODUCER = "Inko (inkotype.com)"
XMP_NS = "http://www.tc260.org.cn/ns/AIGC/1.0/"
SKILL_TAG = "inko-handwriting skill"

PAPER_SIZES_MM = {                         # width x height, portrait
    "A3": (297, 420), "A4": (210, 297), "A5": (148, 210), "A6": (105, 148),
    "B4": (250, 353), "B5": (176, 250), "B6": (125, 176),
    "16K": (184, 260), "32K": (130, 184),
    "LETTER": (215.9, 279.4), "LEGAL": (215.9, 355.6),
}


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


def _jsonable(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def emit(obj) -> None:
    """Machine-readable result on stdout (agents parse this)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, default=_jsonable) + "\n")
    sys.stdout.flush()


def note(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def parse_size_mm(s: str) -> tuple[float, float]:
    """'A4' | 'b5' | 'letter' | '182x257' (mm) | '182x257mm' -> (w, h) in mm."""
    k = s.strip().upper().replace(" ", "")
    if k in PAPER_SIZES_MM:
        return tuple(map(float, PAPER_SIZES_MM[k]))  # type: ignore[return-value]
    m = re.fullmatch(r"([\d.]+)[X×\*]([\d.]+)(MM)?", k)
    if not m:
        die(f"paper size '{s}' not understood: use A4, B5, A5, Letter, 16K … or WxH in mm like 182x257")
    return float(m.group(1)), float(m.group(2))


# ── implicit label (metadata) ────────────────────────────────────────────────

def xmp_packet(fields: dict) -> str:
    js = html.escape(json.dumps(fields, ensure_ascii=False), quote=False)
    return ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmlns:TC260="{XMP_NS}" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<TC260:AIGC>{js}</TC260:AIGC><dc:creator><rdf:Seq><rdf:li>{PRODUCER}</rdf:li></rdf:Seq></dc:creator>'
            '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')


def _aigc_from_xmp(xmp) -> dict | None:
    if not xmp:
        return None
    if isinstance(xmp, bytes):
        xmp = xmp.decode("utf-8", "replace")
    m = re.search(r"<TC260:AIGC>(.*?)</TC260:AIGC>", xmp, re.S)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None


def read_meta(img: Image.Image) -> dict:
    """Everything the skill keeps across edits: AIGC fields, the visible-label crop, ink-layer info, dpi."""
    info = dict(getattr(img, "info", {}) or {})
    text = dict(getattr(img, "text", {}) or {}) if hasattr(img, "text") else {}
    info.update(text)
    aigc = None
    if info.get("AIGC"):
        try:
            aigc = json.loads(info["AIGC"])
        except (TypeError, ValueError):
            aigc = None
    aigc = aigc or _aigc_from_xmp(info.get("XML:com.adobe.xmp") or info.get("xmp"))
    if aigc is None:                                   # JPEG written by this skill: EXIF ImageDescription
        try:
            desc = img.getexif().get(0x010E)
            if isinstance(desc, str) and desc.startswith("AIGC:"):
                aigc = json.loads(desc[5:])
        except Exception:  # noqa: BLE001
            aigc = None
    ink = None
    if info.get("InkoInk"):
        try:
            ink = json.loads(info["InkoInk"])
        except ValueError:
            ink = None
    drift = None                                       # compose.py drift / lines --drift already moved the left edges
    if info.get("InkoDrift"):
        try:
            drift = json.loads(info["InkoDrift"])
        except ValueError:
            drift = {"raw": str(info["InkoDrift"])}
    return {"aigc": aigc, "label_png": info.get("InkoLabel"), "ink": ink, "dpi": info.get("dpi"), "drift": drift}


def merge_meta(*metas: dict) -> dict:
    """First non-empty value wins (e.g. paper photo + Inko ink -> the ink's AIGC fields)."""
    out: dict = {"aigc": None, "label_png": None, "ink": None, "dpi": None}
    for m in metas:
        for k in out:
            if out[k] is None and m and m.get(k) is not None:
                out[k] = m[k]
    return out


def open_image(path: str | Path) -> tuple[Image.Image, dict]:
    p = Path(path)
    if not p.exists():
        die(f"file not found: {p}")
    try:
        img = Image.open(p)
        img.load()
    except Exception as e:  # noqa: BLE001
        die(f"cannot open image {p}: {e}")
    meta = read_meta(img)
    try:                                               # phone photos: honour EXIF rotation
        from PIL import ImageOps
        img2 = ImageOps.exif_transpose(img)
        if img2 is not None:
            img = img2
    except Exception:  # noqa: BLE001
        pass
    return img, meta


def save_image(img: Image.Image, path: str | Path, meta: dict | None = None, dpi: float | None = None, quality: int = 92) -> Path:
    """Save PNG / JPEG / WebP keeping the AIGC implicit label (and the skill's own metadata for PNG)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = meta or {}
    aigc = meta.get("aigc")
    ext = p.suffix.lower()
    kw: dict = {}
    if dpi:
        kw["dpi"] = (dpi, dpi)
    if ext == ".png":
        info = PngInfo()
        if aigc:
            info.add_itxt("AIGC", json.dumps(aigc, ensure_ascii=False))
            info.add_itxt("XML:com.adobe.xmp", xmp_packet(aigc))
        if meta.get("label_png"):
            info.add_text("InkoLabel", meta["label_png"])
        if meta.get("ink"):
            info.add_text("InkoInk", json.dumps(meta["ink"], ensure_ascii=False))
        if meta.get("drift"):
            info.add_text("InkoDrift", json.dumps(meta["drift"], ensure_ascii=True))
        info.add_text("Software", f"{PRODUCER} / {SKILL_TAG}")
        img.save(p, "PNG", pnginfo=info, compress_level=6, **kw)
    elif ext in (".jpg", ".jpeg"):
        im = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        exif = Image.Exif()
        exif[0x0131] = f"{PRODUCER} / {SKILL_TAG}"                      # Software
        if aigc:
            exif[0x010E] = "AIGC:" + json.dumps(aigc, ensure_ascii=True)  # ImageDescription (ASCII)
        extra = {"xmp": xmp_packet(aigc).encode("utf-8")} if aigc else {}
        try:
            im.save(p, "JPEG", quality=quality, subsampling=0 if quality >= 90 else 2, exif=exif, optimize=True, **extra, **kw)
        except TypeError:                                                 # older Pillow: no xmp= for JPEG
            im.save(p, "JPEG", quality=quality, exif=exif, optimize=True, **kw)
    elif ext == ".webp":
        extra = {"xmp": xmp_packet(aigc).encode("utf-8")} if aigc else {}
        img.save(p, "WEBP", quality=quality, method=4, **extra)
    else:
        die(f"unsupported output type '{ext}': use .png, .jpg or .webp (PDF: scripts/pdf.py)")
    return p


# ── visible label 「AI生成 · Inko」 ───────────────────────────────────────────

def find_label_bbox(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate Inko's gray corner label on a generated page (bottom-right). Returns (x0, y0, x1, y1) or None."""
    H, W = rgb.shape[:2]
    y0, x0 = int(H * 0.88), int(W * 0.40)
    reg = rgb[y0:, x0:].astype(np.int16)
    if reg.ndim == 2:
        reg = np.repeat(reg[..., None], 3, 2)
    mx, mn = reg.max(2), reg.min(2)
    gray = (mx - mn < 26) & (mn > 70) & (mx < 190)                        # neutral mid-gray (drawn at 128)
    rows = np.flatnonzero(gray.sum(1) >= 2)
    cols = np.flatnonzero(gray.sum(0) >= 2)
    if len(rows) < 8 or len(cols) < 20:
        return None
    # keep the largest run of rows (the label line), ignore stray gray pixels elsewhere
    runs, s = [], rows[0]
    for a, b in zip(rows, rows[1:]):
        if b - a > 12:
            runs.append((s, a))
            s = b
    runs.append((s, rows[-1]))
    r0, r1 = max(runs, key=lambda r: r[1] - r[0])
    sub = gray[r0:r1 + 1]
    cols = np.flatnonzero(sub.sum(0) >= 1)
    if len(cols) < 20:
        return None
    h, w = r1 - r0 + 1, cols[-1] - cols[0] + 1
    short = min(H, W)
    if not (0.03 * short <= h <= 0.09 * short and 3.0 <= w / max(h, 1) <= 11.0):
        return None
    pad = max(4, int(h) // 10)
    return (int(max(0, x0 + cols[0] - pad)), int(max(0, y0 + r0 - pad)), int(min(W, x0 + cols[-1] + 1 + pad)), int(min(H, y0 + r1 + 1 + pad)))


def _period(profile: np.ndarray, lo: int, hi: int) -> int:
    """Dominant period (px) of a 1-D profile, 0 if it isn't periodic (plain paper)."""
    p = np.asarray(profile, np.float64) - float(np.mean(profile))
    if hi <= lo + 2 or len(p) <= hi or not np.any(p):
        return 0
    ac = np.correlate(p, p, mode="full")[len(p) - 1:]
    if ac[0] <= 0:
        return 0
    j = lo + int(np.argmax(ac[lo:hi]))
    return j if ac[j] > 0.3 * ac[0] else 0


def erase_label(rgb: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    """Copy of rgb with the visible label replaced by the paper that would be under it. Candidates are copies of the
    same-size area further up or further left, shifted by whole ruled-line / grid periods; the one without handwriting
    whose surroundings best continue the label's surroundings wins — so grid squares continue under the label, and the
    empty bottom margin of a ruled page stays empty instead of getting lines copied into it."""
    x0, y0, x1, y1 = bbox
    h, w = y1 - y0, x1 - x0
    arr = np.asarray(rgb)
    Hh, Ww = arr.shape[:2]
    L = luminance(arr.astype(np.float32))
    top, left = max(0, y0 - 4 * h), max(0, x0 - 2 * w)
    paper_l = float(np.percentile(L[top:y1, left:x1], 90)) if y1 > top else 255.0
    vper = _period(L[top:y0, x0:x1].mean(1), 6, min((y0 - top) // 2, 3 * h)) if y0 - top > 12 else 0
    hper = _period(L[y0:y1, left:x0].mean(0), 6, min((x0 - left) // 2, w)) if x0 - left > 12 else 0
    vstep = int(np.ceil(h / vper) * vper) if vper else h
    hstep = int(np.ceil(w / hper) * hper) if hper else w
    b = 6

    def ring(x: int, y: int) -> dict:
        r = {}
        if y - b >= 0:
            r["t"] = L[y - b:y, x:x + w]
        if y + h + b <= Hh:
            r["b"] = L[y + h:y + h + b, x:x + w]
        if x - b >= 0:
            r["l"] = L[y:y + h, x - b:x]
        return r
    target = ring(x0, y0)
    cands = [(x0, y0 - k * vstep) for k in range(1, 40)] + [(x0 - k * hstep, y0) for k in range(1, 20)]
    best, best_score = None, 1e9
    for sx, sy in cands:
        if sx < 0 or sy < 0 or sx + w > Ww or sy + h > Hh:
            continue
        ink = float((L[sy:sy + h, sx:sx + w] < paper_l - 90).mean())       # handwriting, not printed lines
        src = ring(sx, sy)
        common = [k for k in target if k in src]
        edge = float(np.mean([np.abs(target[k] - src[k]).mean() for k in common])) if common else 50.0
        score = edge + 4000.0 * ink
        if score < best_score:
            best, best_score = (sx, sy), score
    out = arr.copy()
    if best is not None:
        sx, sy = best
        out[y0:y1, x0:x1] = arr[sy:sy + h, sx:sx + w]
    else:                                                                  # nowhere to copy from: plain paper colour
        ch = arr.shape[2] if arr.ndim == 3 else 1
        out[y0:y1, x0:x1] = np.median(arr[max(0, y0 - h):y0, x0:x1].reshape(-1, ch), 0) if y0 > 0 else paper_l
    return out


def label_from_page(rgb: np.ndarray, bbox: tuple[int, int, int, int], background: np.ndarray | None = None) -> Image.Image:
    """RGBA of the label alone (gray text on transparent), measured against the paper under it — so ruled lines or
    grid squares behind the label don't become part of it."""
    if background is None:
        background = erase_label(rgb, bbox)
    x0, y0, x1, y1 = bbox
    reg = np.asarray(rgb)[y0:y1, x0:x1].astype(np.float32)
    bg = np.asarray(background)[y0:y1, x0:x1].astype(np.float32)
    lum, lbg = luminance(reg), luminance(bg)
    a = np.clip((lbg - lum) / np.maximum(1.0, lbg - 128.0), 0, 1)
    if reg.ndim == 3:
        a[(reg.max(2) - reg.min(2)) > 40] = 0                            # coloured ink isn't the gray label
    a[a < 0.06] = 0
    out = np.zeros((*a.shape, 4), np.uint8)
    out[..., :3] = 128
    out[..., 3] = (a * 255).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def label_crop(rgb: np.ndarray, bbox: tuple[int, int, int, int]) -> Image.Image:
    """RGBA crop of the label: gray text on transparent (kept for older callers)."""
    return label_from_page(np.asarray(rgb), bbox)


def label_to_b64(label: Image.Image) -> str:
    buf = io.BytesIO()
    label.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def label_from_b64(s: str | None) -> Image.Image | None:
    if not s:
        return None
    try:
        return Image.open(io.BytesIO(base64.b64decode(s))).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def text_height(label: Image.Image) -> int:
    """Height of the label's letters (rows with solid strokes), in px."""
    a = np.asarray(label.getchannel("A"))
    rows = np.flatnonzero((a > 128).any(1))
    return int(rows[-1] - rows[0] + 1) if len(rows) else label.height


def default_label() -> Image.Image:
    """The label drawn from text (used when a derived image needs a label but no crop of the original is at hand)."""
    font = cjk_font(96)
    probe = ImageDraw.Draw(Image.new("L", (10, 10)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), LABEL_TEXT, font=font)
    im = Image.new("RGBA", (x1 - x0 + 16, y1 - y0 + 16), (128, 128, 128, 0))
    ImageDraw.Draw(im).text((8 - x0, 8 - y0), LABEL_TEXT, font=font, fill=(128, 128, 128, 255))
    return im


def apply_label(img: Image.Image, label: Image.Image | None = None, frac: float = LABEL_MIN_FRAC * 1.06, where: str = "br",
                margin: tuple[float, float] = (0.035, 0.022), color: tuple[int, int, int] | str | None = "auto",
                at: tuple[float, float] | None = None) -> Image.Image:
    """Put the visible label 「AI生成 · Inko」 on img: text height >= frac x the shortest side, in a corner (bottom-right default).
    color="auto" keeps it readable: mid-gray on light backgrounds, light gray on dark ones (a dark desk in a photo)."""
    label = label or default_label()
    W, H = img.size
    target = max(frac, LABEL_MIN_FRAC) * min(W, H)
    k = target / max(1, text_height(label))
    lab = label.resize((max(1, round(label.width * k)), max(1, round(label.height * k))), Image.LANCZOS)
    for _ in range(3):                                   # resampling can shave a row: re-check the legal minimum
        th = text_height(lab)
        if th >= LABEL_MIN_FRAC * min(W, H):
            break
        g = LABEL_MIN_FRAC * min(W, H) * 1.02 / max(1, th)
        lab = label.resize((max(1, round(lab.width * g)), max(1, round(lab.height * g))), Image.LANCZOS)
    mx, my = round(margin[0] * W), round(margin[1] * H)
    if at is not None:                                   # explicit bottom-right anchor (e.g. a sheet's corner in a photo)
        x, y = max(0, int(at[0] - lab.width)), max(0, int(at[1] - lab.height))
    else:
        x = max(0, W - mx - lab.width if "r" in where else mx)
        y = max(0, H - my - lab.height if "b" in where else my)
    if color == "auto":
        region = np.asarray(img.convert("L").crop((x, y, x + lab.width, y + lab.height)), np.float32)
        bg = float(np.median(region)) if region.size else 255.0
        color = (110, 110, 110) if bg >= 165 else (236, 236, 236) if bg < 110 else (40, 40, 40)
    if color is not None:
        c = Image.new("RGBA", lab.size, (*color, 255))
        c.putalpha(lab.getchannel("A"))
        lab = c
    base = img.convert("RGBA")
    base.alpha_composite(lab, (x, y))
    return base.convert(img.mode) if img.mode in ("RGB", "L") else base


def label_box(img_wh: tuple[int, int], label: Image.Image | None = None, frac: float = LABEL_MIN_FRAC * 1.06, where: str = "br",
              margin: tuple[float, float] = (0.035, 0.022)) -> tuple[int, int, int, int]:
    """Where apply_label() will put the label (x0, y0, x1, y1) — to keep handwriting out of that corner."""
    label = label or default_label()
    W, H = img_wh
    k = frac * min(W, H) / max(1, text_height(label))
    w, h = round(label.width * k), round(label.height * k)
    mx, my = round(margin[0] * W), round(margin[1] * H)
    x = max(0, W - mx - w if "r" in where else mx)
    y = max(0, H - my - h if "b" in where else my)
    return x, y, x + w, y + h


# ── fonts ───────────────────────────────────────────────────────────────────

_FONT_CANDIDATES = [
    os.environ.get("INKO_FONT", ""),
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simkai.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc", "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
]
_KAI_CANDIDATES = [os.environ.get("INKO_FONT_KAI", ""), "C:/Windows/Fonts/simkai.ttf", "/System/Library/Fonts/Supplemental/Kaiti.ttc",
                   "/usr/share/fonts/truetype/arphic/ukai.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]


def cjk_font(size: int, kai: bool = False) -> ImageFont.ImageFont:
    for f in (_KAI_CANDIDATES if kai else []) + _FONT_CANDIDATES:
        if f and Path(f).exists():
            try:
                return ImageFont.truetype(f, max(4, int(size)))
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=max(4, int(size)))
    except TypeError:
        return ImageFont.load_default()


# ── small numpy helpers ─────────────────────────────────────────────────────

def luminance(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim == 2:
        return rgb.astype(np.float32)
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _box1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    p = np.pad(a, pad, mode="edge")
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def gaussian(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur of a float32 2-D array (three box blurs per axis, O(N); no scipy needed)."""
    if sigma <= 0.3:
        return np.asarray(arr, np.float32)
    n = 3
    w = (12 * sigma * sigma / n + 1) ** 0.5
    wl = int(w) - (1 - int(w) % 2)                  # odd width below
    m = round((12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4))
    out = np.asarray(arr, np.float32)
    for i in range(n):
        r = ((wl if i < m else wl + 2) - 1) // 2
        out = _box1d(_box1d(out, r, 0), r, 1)
    return out


def value_noise(h: int, w: int, cell: float, rng: np.random.Generator) -> np.ndarray:
    """Smooth random field in [-1, 1] with feature size ~cell px (bicubic-upsampled random grid)."""
    gh, gw = max(2, int(h / cell) + 3), max(2, int(w / cell) + 3)
    g = rng.uniform(-1, 1, (gh, gw)).astype(np.float32)
    im = Image.fromarray(g, "F").resize((int(gw * cell), int(gh * cell)), Image.BICUBIC)
    a = np.asarray(im, np.float32)[:h, :w]
    if a.shape != (h, w):
        a = np.pad(a, ((0, h - a.shape[0]), (0, w - a.shape[1])), mode="edge")
    m = np.abs(a).max() or 1.0
    return a / m


def px_per_mm_of(img: Image.Image, meta: dict | None = None, default: float = PX_PER_MM_300) -> float:
    ink = (meta or {}).get("ink") or {}
    if ink.get("px_per_mm"):
        return float(ink["px_per_mm"])
    dpi = (meta or {}).get("dpi") or img.info.get("dpi")
    if dpi and float(dpi[0]) > 50:
        return float(dpi[0]) / 25.4
    if img.size in ((2480, 3508), (3508, 2480)):
        return PX_PER_MM_300
    return default
