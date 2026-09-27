#!/usr/bin/env python3
"""Editable handwriting packages (inko-scene v1, `scene.zip`): inspect, fix spacing / left edges / pen, re-render.
Everything runs locally and instantly: nothing is generated again, no API call, no cost.

    python scene.py inspect  scene.zip                      pages, lines, pens + anomalies (big gaps, odd stroke weight,
                                                             overlaps, glyphs off the paper / outside the margins, hidden)
    python scene.py render   scene.zip -o out/ [--pdf] [--pages 1,2] [--format png|jpg]
    python scene.py tighten  scene.zip [--line p0l3,p0l4 | all] [--max-gap 0.35]
    python scene.py drift    scene.zip [--mode natural|random] [--amount 1]
    python scene.py gap      scene.zip --a p0g12 --b p0g13 --mm 0.8          (or --size 0.12 = x glyph size)
    python scene.py align-baseline scene.zip [--line …] [--strength 1]
    python scene.py restyle  scene.zip [--ids …] --type gel --color blue --weight 0.3 --ink 0.2    (alias: pen)
    python scene.py move     scene.zip --ids p0g12,p0g13 --dx 1.2 [--dy 0]     (mm)
    python scene.py scale    scene.zip --ids … --k 1.1 [--about anchor|center|selection]
    python scene.py rotate   scene.zip --ids … --deg -2 [--about …]
    python scene.py hide | show scene.zip --ids …
    python scene.py note     scene.zip --text "…"
    python scene.py undo     scene.zip [--n 1]              removes the last n commands (a command = one batch of ops)
    python scene.py reset    scene.zip                      drops every edit (edits.json is backed up first)
    python scene.py editor   scene.zip [--port 0] [--no-browser]   local GUI on 127.0.0.1: drag / box-select / nudge /
                                                                   scale / pen / undo / save

The package: scene.json (every glyph as written + where it sits: affine m, anchor, size, advance box, ink box, line, pen),
sprites/page-N.png (glyph atlases, ink before any pen effect), paper/page-N.png, label.png, README.txt.
A scene.zip is extracted once to a sibling folder (scene.zip -> scene/) and used from there; a folder works directly.
Edits never touch scene.json: they are atomic ops appended to edits.json next to it (move / scale / rotate / pen / hide /
show / note); the high-level commands (tighten, drift, gap, align-baseline, restyle) compute atomic ops and append them as
one batch, so undo = drop the last batch. Ids: glyph "p0g12" (page index 0, glyph 12), line "p0l3"; a bare line number
means that line on --page (1-based, default 1). Lengths on the command line are millimetres.

Rendering = the server's own maths: paper -> one ink layer per pen (glyphs min-composited through their affine m with
bilinear sampling) -> the Inko pen (ported pen.py, noise anchored at the page origin) multiplied onto the paper -> the
visible label 「AI生成 · Inko」 pasted where the server put it (checked >= 5 % of the short side, else re-applied) ->
PNG/JPG/PDF with the AIGC metadata (exactly the server's TC260 fields, ProduceID = the page's id); once edits exist the
file also carries a separate marker InkoEdited=true (PNG tEXt chunk, JPEG comment, PDF Info /InkoEdited). An unedited package
renders the server's page to within rounding. There is no option to drop the label (only a job generated without a
visible label renders without one).
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import hashlib
import json
import math
import random
import re
import secrets
import shutil
import struct
import threading
import time
import unicodedata
import webbrowser
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np

from _common import (LABEL_MIN_FRAC, PRODUCER, SKILL_TAG, Image, apply_label, die, emit, label_to_b64, note, save_image,
                     text_height, xmp_packet)
import _pen as PEN

HERE = Path(__file__).resolve().parent
FORMAT, VERSION = "inko-scene", 1
OPS = ("move", "scale", "rotate", "pen", "hide", "show", "note")
FILE_RE = re.compile(r"^(scene\.json|README\.txt|label\.png|(sprites|paper)/[A-Za-z0-9._-]{1,80}\.png)$")
MAX_MEMBER = 512 << 20                     # zip-bomb guard per extracted file
WS = set(" \t\r\n　 ")
DEF_REF = {"cjk": 0.12, "latin": 0.07}     # natural ink gap / glyph size when a line has no normal pairs to learn from
REF_CLAMP = {"cjk": (0.05, 0.22), "latin": (0.02, 0.15)}
PUNCT_LIM, PUNCT_TGT = 0.30, 0.20          # after a full-width 。，、 the gap is naturally wider (its ink sits left)
PROBLEM_RE = re.compile(r"^\s*(?:\d+\s*[.、．:：)）]|[（(]\s*\d+\s*[)）]|第\s*\S{1,3}\s*[题问]|[一二三四五六七八九十]+\s*[、.．]|[A-Da-d]\s*[.、．])")


# ── package ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

class Package:
    """An extracted inko-scene package (folder with scene.json) + its edits.json."""

    def __init__(self, folder: Path):
        self.folder = folder
        try:
            self.doc = json.loads((folder / "scene.json").read_text("utf-8"))
        except (OSError, ValueError) as e:
            die(f"cannot read {folder / 'scene.json'}: {e}")
        if self.doc.get("format") != FORMAT:
            die(f"{folder / 'scene.json'} is not an inko-scene package (format={self.doc.get('format')!r})")
        if int(self.doc.get("version", 0)) > VERSION:
            note(f"warning: package version {self.doc.get('version')} is newer than this script ({VERSION}); update the skill")
        self.ppm = float(self.doc.get("px_per_mm") or 300 / 25.4)
        self.pen_ppm = float(self.doc.get("pen_px_per_mm") or 2480 / 210)
        self._atlas: dict[str, np.ndarray] = {}

    @property
    def edits_file(self) -> Path:
        return self.folder / "edits.json"

    def path(self, rel: str) -> Path:
        if not isinstance(rel, str) or not FILE_RE.fullmatch(rel):
            die(f"package file name not allowed: {rel!r}")
        return self.folder / rel

    def read_edits(self) -> list[dict]:
        f = self.edits_file
        if not f.exists():
            return []
        try:
            data = json.loads(f.read_text("utf-8"))
        except ValueError as e:
            die(f"{f} is not valid JSON ({e}); fix it or run `scene.py reset`")
        ops = data.get("edits") if isinstance(data, dict) else data
        if not isinstance(ops, list):
            die(f"{f}: expected {{\"edits\": [...]}}")
        return ops

    def edits_rev(self) -> str:
        f = self.edits_file
        return hashlib.sha256(f.read_bytes()).hexdigest()[:16] if f.exists() else "0"

    def write_edits(self, ops: list[dict]) -> None:
        tmp = self.edits_file.with_suffix(".json.part")
        tmp.write_text(json.dumps({"edits": ops}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.edits_file)

    def atlas(self, rel: str) -> np.ndarray:
        if rel not in self._atlas:
            with Image.open(self.path(rel)) as im:
                self._atlas[rel] = np.asarray(im.convert("L"), np.uint8)
        return self._atlas[rel]

    def sprite(self, g: "Glyph") -> np.ndarray:
        s = g.data["sprite"]
        x, y, w, h = (int(v) for v in s["rect"])
        return np.ascontiguousarray(self.atlas(s["atlas"])[y:y + h, x:x + w])

    def label_image(self) -> Image.Image | None:
        lab = self.doc.get("label") or {}
        rel = lab.get("image")
        if not rel or not FILE_RE.fullmatch(str(rel)) or not (self.folder / rel).exists():
            return None
        with Image.open(self.folder / rel) as im:
            return im.convert("RGBA")


def _safe_extract(zpath: Path, dest: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        if "scene.json" not in names:
            die(f"{zpath} has no scene.json: not an inko-scene package")
        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists():
            shutil.rmtree(tmp)
        for info in z.infolist():
            n = info.filename
            if info.is_dir():
                continue
            if not FILE_RE.fullmatch(n):                      # also rejects absolute paths, "..", backslashes
                note(f"skipping unexpected file in the package: {n!r}")
                continue
            if info.file_size > MAX_MEMBER:
                die(f"{n} in {zpath} is too large ({info.file_size} bytes)")
            out = tmp / n
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        tmp.replace(dest)


def open_package(path: str | Path) -> Package:
    """scene.zip | extracted folder | …/scene.json -> Package (a zip is extracted to a sibling folder on first use)."""
    p = Path(path)
    if p.is_dir():
        if not (p / "scene.json").exists():
            die(f"{p} has no scene.json (pass scene.zip or the folder it was extracted to)")
        return Package(p)
    if not p.exists():
        die(f"not found: {p}")
    if p.name == "scene.json":
        return Package(p.parent)
    if not zipfile.is_zipfile(p):
        die(f"{p} is neither a scene.zip nor a package folder")
    dest = p.with_suffix("") if p.suffix.lower() == ".zip" else p.with_name(p.name + "-scene")
    if (dest / "scene.json").exists():
        with zipfile.ZipFile(p) as z:
            same = z.read("scene.json") == (dest / "scene.json").read_bytes()
        if not same:
            die(f"{dest} already holds a different package; pass that folder, or move it away to extract {p.name} again")
        return Package(dest)
    if dest.exists() and any(dest.iterdir()):
        die(f"{dest} exists and is not an extracted package; move it away first")
    if dest.exists():
        dest.rmdir()
    _safe_extract(p, dest)
    note(f"extracted {p.name} -> {dest}")
    return Package(dest)


# ── glyph state: scene + edits ─────────────────────────────────────────────────────────────────────────────────────────

def _T(dx: float, dy: float) -> np.ndarray:
    return np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]], np.float64)


def _about(A2: np.ndarray, p) -> np.ndarray:
    """2x2 linear map A2 about point p (page px) as a 3x3 affine."""
    M = np.eye(3)
    M[:2, :2] = A2
    return _T(p[0], p[1]) @ M @ _T(-p[0], -p[1])


def _rot(deg: float) -> np.ndarray:
    r = math.radians(deg)                   # page y points down: positive = clockwise on screen (same as the layout's r)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s], [s, c]], np.float64)


def _box_of(pts: np.ndarray) -> np.ndarray:
    return np.array([pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()], np.float64)


def _corners(b) -> np.ndarray:
    x0, y0, x1, y1 = b
    return np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], np.float64)


class Glyph:
    __slots__ = ("id", "p", "data", "m0", "E", "hidden", "pen", "size0", "anchor0", "adv0", "ink0", "sbox")

    def __init__(self, p: int, data: dict):
        self.id, self.p, self.data = data["id"], p, data
        self.m0 = np.vstack([np.asarray(data["m"], np.float64).reshape(2, 3), [0, 0, 1]])
        self.E = np.eye(3)
        self.hidden = False
        self.pen: dict | None = None                     # pen override from edits (None = the package's pen)
        self.size0 = float(data.get("size") or 1.0)
        self.anchor0 = np.asarray(data.get("anchor") or self.m0[:2, 2], np.float64)
        adv = data.get("adv") or [self.anchor0[0], self.anchor0[0] + self.size0]
        self.adv0 = np.asarray(adv, np.float64)
        sw, sh = data["sprite"]["rect"][2:]
        quad = _box_of((_corners((0, 0, sw, sh)) @ self.m0.T)[:, :2])
        self.ink0 = np.asarray(data["ink"], np.float64) if data.get("ink") else quad
        # the ink box in sprite coordinates (to follow rotations / scales without growing)
        if abs(np.linalg.det(self.m0[:2, :2])) > 1e-12:
            sb = _box_of((_corners(self.ink0) @ np.linalg.inv(self.m0).T)[:, :2])
            self.sbox = np.clip(sb, [0, 0, 0, 0], [sw, sh, sw, sh])
        else:
            self.sbox = np.array([0, 0, sw, sh], np.float64)

    # current geometry
    @property
    def m(self) -> np.ndarray:
        return (self.E @ self.m0)[:2]

    @property
    def edited(self) -> bool:
        return not np.array_equal(self.E, np.eye(3))

    @property
    def anchor(self) -> np.ndarray:
        return (self.E @ np.array([*self.anchor0, 1.0]))[:2]

    @property
    def size(self) -> float:
        return self.size0 * math.sqrt(abs(np.linalg.det(self.E[:2, :2])))

    @property
    def adv(self) -> np.ndarray:
        y = self.anchor0[1]
        xs = (np.array([[self.adv0[0], y, 1], [self.adv0[1], y, 1]]) @ self.E.T)[:, 0]
        return np.sort(xs)

    @property
    def ink(self) -> np.ndarray:
        """[x0, y0, x1, y1] ink bounds on the page (x1 / y1 exclusive), following the edits."""
        if np.array_equal(self.E[:2, :2], np.eye(2)):
            t = self.E[:2, 2]
            return self.ink0 + np.array([t[0], t[1], t[0], t[1]])
        return _box_of((_corners(self.sbox) @ (self.E @ self.m0).T)[:, :2])

    @property
    def rot(self) -> float:
        """Current rotation of the glyph (degrees, + = clockwise): the layout's own plus any rotate edits."""
        e = self.E[:2, :2]
        return float(self.data.get("rot") or 0.0) + math.degrees(math.atan2(e[1, 0], e[0, 0]))

    @property
    def kind(self) -> str:
        return self.data.get("kind") or "cn"

    @property
    def c(self) -> str:
        return self.data.get("c") or ""


def _finite(v, name: str, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f"{name} must be within [{lo}, {hi}]")
    return x


def check_op(op) -> dict:
    """Validate one atomic edit op; returns a clean copy (raises ValueError)."""
    if not isinstance(op, dict):
        raise ValueError("an edit must be an object")
    kind = op.get("op")
    if kind not in OPS:
        raise ValueError(f"unknown op {kind!r} (known: {', '.join(OPS)})")
    out: dict = {"op": kind}
    if kind != "note":
        ids = op.get("ids")
        if ids != "all":
            if not isinstance(ids, list) or not ids or len(ids) > 200_000 or not all(isinstance(i, str) and len(i) < 64 for i in ids):
                raise ValueError(f"{kind}: ids must be \"all\" or a non-empty list of glyph ids")
        out["ids"] = ids
    if kind == "move":
        out["dx"] = _finite(op.get("dx", 0), "dx", -2000, 2000)
        out["dy"] = _finite(op.get("dy", 0), "dy", -2000, 2000)
    elif kind in ("scale", "rotate"):
        if kind == "scale":
            out["k"] = _finite(op.get("k"), "k", 0.1, 10)
        else:
            out["deg"] = _finite(op.get("deg"), "deg", -360, 360)
        about = op.get("about", "anchor")
        if isinstance(about, list):
            if len(about) != 2:
                raise ValueError("about: [x, y] in page pixels")
            about = [_finite(v, "about", -1e6, 1e6) for v in about]
        elif about not in ("anchor", "center", "selection"):
            raise ValueError("about must be anchor | center | selection | [x, y]")
        out["about"] = about
    elif kind == "pen":
        pen = op.get("pen")
        if pen is not None and not isinstance(pen, dict):
            raise ValueError("pen: an object {type, color, weight, ink} or null (= back to the package's pen)")
        out["pen"] = None if pen is None else PEN.normalize(pen)
    elif kind == "note":
        t = op.get("text", "")
        if not isinstance(t, str) or len(t) > 4000:
            raise ValueError("note: text must be a string")
        out["text"] = t
    for k in ("batch",):
        if isinstance(op.get(k), int) and not isinstance(op.get(k), bool):
            out[k] = op[k]
    if isinstance(op.get("src"), str) and len(op["src"]) < 40:
        out["src"] = op["src"]
    return out


class State:
    """The package with its edits applied (never written back to scene.json)."""

    def __init__(self, pkg: Package, edits: list[dict] | None = None):
        self.pkg, self.doc = pkg, pkg.doc
        self.pages: list[list[Glyph]] = []
        self.glyphs: dict[str, Glyph] = {}
        self.lines: list[list[dict]] = []
        self.warnings: list[str] = []
        self.n_edits = 0
        for p, page in enumerate(self.doc.get("pages") or []):
            gl = [Glyph(p, g) for g in page.get("glyphs") or []]
            self.pages.append(gl)
            for g in gl:
                self.glyphs[g.id] = g
            self.lines.append(page.get("lines") or [])
        for op in edits or []:
            try:
                self.apply(check_op(op))
            except ValueError as e:
                self.warnings.append(f"edit skipped ({e}): {json.dumps(op, ensure_ascii=False)[:200]}")

    def resolve(self, ids) -> list[Glyph]:
        if ids == "all":
            return [g for gl in self.pages for g in gl]
        out, missing = [], []
        for i in ids:
            g = self.glyphs.get(i)
            (out if g else missing).append(g or i)
        if missing:
            self.warnings.append(f"unknown glyph ids ignored: {', '.join(missing[:8])}{' …' if len(missing) > 8 else ''}")
        return out

    def apply(self, op: dict) -> None:
        kind = op["op"]
        if kind == "note":
            return
        self.n_edits += 1
        gl = self.resolve(op["ids"])
        if kind == "move":
            A = _T(op["dx"] * self.pkg.ppm, op["dy"] * self.pkg.ppm)
            for g in gl:
                g.E = A @ g.E
        elif kind in ("scale", "rotate"):
            L = np.eye(2) * op["k"] if kind == "scale" else _rot(op["deg"])
            about = op.get("about", "anchor")
            if about == "selection" and gl:
                b = np.array([g.ink for g in gl])
                sel = ((b[:, 0].min() + b[:, 2].max()) / 2, (b[:, 1].min() + b[:, 3].max()) / 2)
            for g in gl:
                if isinstance(about, list):
                    pt = about
                elif about == "center":
                    k = g.ink
                    pt = ((k[0] + k[2]) / 2, (k[1] + k[3]) / 2)
                elif about == "selection":
                    pt = sel
                else:
                    pt = g.anchor
                g.E = _about(L, pt) @ g.E
        elif kind == "pen":
            for g in gl:
                g.pen = op["pen"]
        elif kind in ("hide", "show"):
            for g in gl:
                g.hidden = kind == "hide"

    # pens / layers
    def base_pen(self, g: Glyph) -> dict:
        layers = self.doc["pages"][g.p].get("layers") or []
        li = g.data.get("layer")
        if isinstance(li, int) and 0 <= li < len(layers):
            return PEN.normalize(layers[li].get("pen"))
        return PEN.normalize(g.data.get("pen") or self.doc.get("pen"))

    def pen_of(self, g: Glyph) -> dict:
        return g.pen if g.pen is not None else self.base_pen(g)

    def layers(self, p: int) -> list[tuple[dict, list[Glyph]]]:
        """Ink layers of page p in the order the server multiplies them (the package's order; new pens after it)."""
        groups = [[PEN.normalize(l.get("pen")), []] for l in self.doc["pages"][p].get("layers") or []]
        for g in self.pages[p]:
            if g.hidden:
                continue
            li = g.data.get("layer")
            if g.pen is None and isinstance(li, int) and 0 <= li < len(groups):
                groups[li][1].append(g)
                continue
            pen = self.pen_of(g)
            for grp in groups:
                if grp[0] == pen:
                    grp[1].append(g)
                    break
            else:
                groups.append([pen, [g]])
        return [(pen, gl) for pen, gl in groups if gl]

    def colored(self) -> bool:
        """Any visible glyph of the job (every page) inks in a colour: the server then saves every page as RGB."""
        if not hasattr(self, "_colored"):
            self._colored = any(PEN.is_colored(self.pen_of(g)) for gl in self.pages for g in gl if not g.hidden)
        return self._colored

    def visible(self, p: int) -> list[Glyph]:
        return [g for g in self.pages[p] if not g.hidden]

    def line_glyphs(self, p: int, line: dict, visible: bool = True) -> list[Glyph]:
        gl = [self.glyphs[i] for i in line.get("glyphs") or [] if i in self.glyphs]
        return [g for g in gl if not g.hidden] if visible else gl

    def line_of(self, g: Glyph) -> dict | None:
        lid = g.data.get("line")
        for ln in self.lines[g.p]:
            if ln.get("id") == lid:
                return ln
        return None


# ── rendering ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _region(m: np.ndarray, gw: int, gh: int, W: int, H: int) -> tuple[int, int, int, int]:
    """Page box covered by the sprite's corners, 1 px margin, clipped to the page (the worker's _place rule)."""
    corners = np.array([[0, 0, 1], [gw, 0, 1], [0, gh, 1], [gw, gh, 1]], np.float64) @ np.asarray(m, np.float64).T
    x0, y0 = max(0, int(math.floor(corners[:, 0].min())) - 1), max(0, int(math.floor(corners[:, 1].min())) - 1)
    x1, y1 = min(W, int(math.ceil(corners[:, 0].max())) + 1), min(H, int(math.ceil(corners[:, 1].max())) + 1)
    return x0, y0, x1, y1


def place_sprite(ink: np.ndarray, spr: np.ndarray, m) -> None:
    """uint8 sprite (255 = paper) through the affine m onto the ink layer (1.0 = white), min-composited: every page pixel
    is mapped back into the sprite and sampled bilinearly (outside = paper). Same maths as the worker's _place."""
    m = np.asarray(m, np.float64).reshape(2, 3)
    H, W = ink.shape
    gh, gw = spr.shape
    if gw < 1 or gh < 1:
        return
    x0, y0, x1, y1 = _region(m, gw, gh, W, H)
    if x1 <= x0 or y1 <= y0:
        return
    F = np.linalg.inv(np.vstack([m, [0, 0, 1]])) @ np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float64)
    out = Image.fromarray(spr, "L").transform((x1 - x0, y1 - y0), Image.AFFINE, data=tuple(F[:2].ravel()),
                                              resample=Image.BILINEAR, fillcolor=255)
    patch = np.asarray(out, np.float32) / 255.0
    ink[y0:y1, x0:x1] = np.minimum(ink[y0:y1, x0:x1], patch)


def _paper(pkg: Package, page: dict) -> Image.Image:
    W, H = (int(v) for v in page["size"])
    paper = page.get("paper") or {}
    if paper.get("kind") == "image" and paper.get("image"):
        with Image.open(pkg.path(paper["image"])) as im:
            pg = im.convert("RGB")
        if pg.size != (W, H):
            pg = pg.resize((W, H), Image.LANCZOS)
        return pg
    rgb = paper.get("rgb") or [255, 255, 255]
    return Image.new("RGB", (W, H), tuple(int(v) for v in rgb[:3]))


def render_page(st: State, p: int) -> tuple[Image.Image, dict]:
    """Page p with the edits: paper -> ink layers -> pens -> label. Returns (image, info)."""
    pkg, page = st.pkg, st.doc["pages"][p]
    pg = _paper(pkg, page)
    W, H = pg.size
    region = np.asarray(pg, np.float32)
    mixed = None
    layers = st.layers(p)
    for pen, gl in layers:
        ink = np.ones((H, W), np.float32)
        for g in gl:
            place_sprite(ink, pkg.sprite(g), g.m)
        if mixed is not None:
            region = mixed.astype(np.float32)
        if PEN.is_default(pen):
            mixed = (region * ink[..., None]).round().clip(0, 255).astype(np.uint8)
        else:
            mixed = PEN.composite(region, PEN.apply(ink, pen, pkg.pen_ppm, origin=(0, 0)), pen)
    img = Image.fromarray(mixed if mixed is not None else region.round().clip(0, 255).astype(np.uint8), "RGB")
    img, lab = put_label(pkg, img)
    colored = any(PEN.is_colored(pen) for pen, _ in layers) or st.colored()
    paper = page.get("paper") or {}
    # the server saves the whole job as grayscale only for blank (layout) / white (plain) paper with black ink everywhere
    # (no uploaded background, no coloured pen on any page) and records that decision as scene.json "gray"; an edit that
    # adds a coloured pen anywhere makes every page RGB, like the server would
    if "gray" in st.doc:
        gray = bool(st.doc.get("gray")) and not colored
    else:
        gray = (paper.get("id") in ("blank", "white") and paper.get("kind") == "color" and not paper.get("upload")
                and not colored)
    if gray:
        img = img.convert("L")
    return img, {"layers": [{"pen": pen, "glyphs": len(gl)} for pen, gl in layers], "label": lab, "mode": img.mode,
                 "glyphs": sum(len(gl) for _, gl in layers), "hidden": sum(g.hidden for g in st.pages[p])}


def put_label(pkg: Package, img: Image.Image) -> tuple[Image.Image, dict]:
    """The server's label raster at the server's place; re-applied by the skill's rule if it is missing or too small.
    Only a job generated without a visible label (label.mode == "none") stays without one."""
    lab = pkg.doc.get("label") or {}
    if lab.get("mode") == "none":
        return img, {"mode": "none"}
    W, H = img.size
    need = LABEL_MIN_FRAC * min(W, H)
    li = pkg.label_image()
    xy = lab.get("xy")
    if li is not None and isinstance(xy, list) and len(xy) == 2:
        x, y = int(xy[0]), int(xy[1])
        th = text_height(li)
        if x >= 0 and y >= 0 and x + li.width <= W and y + li.height <= H and th >= need:
            img.paste(li, (x, y), li)
            return img, {"mode": "package", "xy": [x, y], "text_height_px": th, "min_px": round(need, 1)}
    img = apply_label(img, li)
    return img, {"mode": "reapplied", "reason": "label.png missing, misplaced or smaller than 5 % of the short side"}


def aigc_fields(pkg: Package, produce_id: str) -> dict:
    """Exactly the server's TC260 AIGC fields (nothing of the skill's own: 'edited' is a separate InkoEdited marker)."""
    a = pkg.doc.get("aigc") or {}
    producer = a.get("ContentProducer") or PRODUCER
    return {"Label": a.get("Label", "1"), "ContentProducer": producer, "ProduceID": produce_id,
            "ReservedCode1": hashlib.sha256(f"{producer}|{produce_id}".encode()).hexdigest(),
            "ContentPropagator": a.get("ContentPropagator", "inkotype.com"), "PropagateID": produce_id,
            "ReservedCode2": a.get("ReservedCode2", "")}


EDITED_KEY = "InkoEdited"


def mark_edited(path: Path) -> None:
    """Add the edited marker to a written image without re-encoding it: PNG -> a tEXt chunk InkoEdited=true placed before
    the first IDAT; JPEG -> a COM segment "InkoEdited=true" placed before the scan. Other types are left as they are."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        pos = 8
        while pos + 8 <= len(data):
            n, typ = struct.unpack(">I4s", data[pos:pos + 8])
            if typ in (b"IDAT", b"IEND"):
                body = EDITED_KEY.encode("latin-1") + b"\0" + b"true"
                chunk = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
                path.write_bytes(data[:pos] + chunk + data[pos:])
                return
            pos += 12 + n
    elif data[:2] == b"\xff\xd8":
        pos = 2
        while pos + 4 <= len(data) and data[pos] == 0xFF:
            marker = data[pos + 1]
            if marker == 0xDA:                                      # start of scan: the comment goes right before it
                body = f"{EDITED_KEY}=true".encode("ascii")
                seg = b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
                path.write_bytes(data[:pos] + seg + data[pos:])
                return
            pos += 2 + struct.unpack(">H", data[pos + 2:pos + 4])[0]


def _page_numbers(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    out: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            die(f"--pages {spec!r}: use 1,2 or 1-3 (1-based)")
        for k in range(lo, hi + 1):
            if not 1 <= k <= n:
                die(f"--pages: page {k} does not exist (1..{n})")
            if k - 1 not in out:
                out.append(k - 1)
    return out


def render(pkg: Package, out_dir: Path, pages: str | None = None, fmt: str = "png", pdf: bool = False, quality: int = 92,
           pdf_jpeg: int = 0) -> dict:
    edits = pkg.read_edits()
    st = State(pkg, edits)
    edited = st.n_edits > 0
    out_dir.mkdir(parents=True, exist_ok=True)
    job = pkg.doc.get("job") or {}
    lab_img = pkg.label_image()
    lab_b64 = label_to_b64(lab_img) if lab_img is not None and (pkg.doc.get("label") or {}).get("mode") != "none" else None
    dpi = round(pkg.ppm * 25.4, 3)
    res, pdf_pages = [], []
    PDF = None
    if pdf:
        import pdf as PDF  # noqa: N811  (the skill's own PDF writer: keeps /AIGC + XMP)
    for p in _page_numbers(pages, len(st.pages)):
        page = pkg.doc["pages"][p]
        img, info = render_page(st, p)
        n = int(page.get("index", p)) + 1
        pid = page.get("aigc_id") or f"{job.get('id', 'inko')}-p{n}"
        f = out_dir / f"page-{n}.{'jpg' if fmt == 'jpg' else 'png'}"
        save_image(img, f, {"aigc": aigc_fields(pkg, pid), "label_png": lab_b64}, dpi=dpi, quality=quality)
        if edited:
            mark_edited(f)
        res.append({"page": n, "file": str(f), "size": list(img.size), **info})
        if PDF is not None:
            data, im = PDF.encode_image(img, pdf_jpeg or 90, lossless=not pdf_jpeg)
            pw, ph = PDF.page_size_pt(img, {"dpi": (dpi, dpi)}, "auto")
            pdf_pages.append((data, im, (pw, ph), (0.0, 0.0, pw, ph)))
        del img
    out = {"out": str(out_dir), "pages": res, "edited": edited, "edits": len(edits), "warnings": st.warnings}
    if PDF is not None and pdf_pages:
        aigc = aigc_fields(pkg, str(job.get("id") or "inko"))
        info = {"Title": "Inko 手写页" + ("（已修改）" if edited else ""), "Creator": SKILL_TAG, "Producer": PRODUCER,
                "CreationDate": time.strftime("D:%Y%m%d%H%M%S"), "AIGC": json.dumps(aigc, ensure_ascii=False),
                "Keywords": "AIGC; AI生成 · Inko"}
        if edited:
            info[EDITED_KEY] = "true"
        pf = out_dir / "inko.pdf"
        PDF.write_pdf(pdf_pages, pf, info, xmp_packet(aigc))
        out["pdf"] = str(pf)
    return out


# ── measuring: gaps, stroke weight, overlaps ─────────────────────────────────────────────────────────────────────────────

def _u16(s: str, i: int, j: int) -> str:
    b = s.encode("utf-16-le")
    return b[2 * max(0, i):2 * max(0, j)].decode("utf-16-le", "replace")


def text_between(doc: dict, a: Glyph, b: Glyph) -> str | None:
    """Source text between two glyphs (UTF-16 spans), None when unknown."""
    ai, aj, bi = a.data.get("i"), a.data.get("j"), b.data.get("i")
    if not all(isinstance(v, int) for v in (ai, aj, bi)) or bi < aj:
        return None
    base = int(doc.get("own_base") or 10_000_000)
    if aj <= base and bi < base:
        return _u16(doc.get("text") or "", aj, bi)
    if ai >= base and bi >= base:
        ka, oa = divmod(aj - base, 100_000)
        kb, ob = divmod(bi - base, 100_000)
        own = doc.get("own_texts") or []
        if ka == kb and 0 <= ka < len(own):
            return _u16(own[ka] or "", oa, ob)
    return None


def _wide(c: str) -> bool:
    return bool(c) and unicodedata.east_asian_width(c[0]) in "FW"


def is_space(doc: dict, a: Glyph, b: Glyph) -> bool:
    """A real word space between a and b (in the source text, or reserved by the layout between their advance boxes)."""
    t = text_between(doc, a, b)
    if a.kind == "math" and b.kind == "math" and not (t or "").strip():
        # two formulas with only a space between: inko.py cut a long formula into pieces ("$I(a)$ $=\int…$") so the
        # engine may break the line there — a line-break chance, not a word gap; each piece sits in its own (wider)
        # layout box, which is exactly the "大空隙" users see, so tighten may close it
        return False
    if t and any(ch in WS for ch in t):
        return True
    return b.adv0[0] - a.adv0[1] > 0.2 * (a.size0 + b.size0) / 2


def _pair_class(a: Glyph, b: Glyph) -> str:
    return "latin" if a.kind in ("latin", "digit") and b.kind in ("latin", "digit") else "cjk"


def is_cell(g: Glyph) -> bool:
    """Written in a grid cell (稿纸 / 田字格 / 方格): its place is fixed by the paper, so spacing tools (tighten, drift)
    leave it where it is. The package says so with glyph "cell" (the worker writes "cell": true for glyphs the layout
    centred in a cell); a package without the field is inferred: a cell glyph's anchor sits at the centre of its
    advance box (running text anchors at the box's left edge)."""
    if "cell" in g.data:
        return bool(g.data["cell"])
    a0, a1 = g.adv0
    return a1 - a0 > 1.0 and abs(float(g.anchor0[0]) - (a0 + a1) / 2) < 0.5


def line_pairs(st: State, gl: list[Glyph]) -> list[dict]:
    out = []
    for a, b in zip(gl, gl[1:]):
        s = (a.size + b.size) / 2 or 1.0
        gap = float(b.ink[0] - a.ink[2])
        grouped = (a.data.get("group") is not None and a.data.get("group") == b.data.get("group")) or is_cell(a) or is_cell(b)
        punct = a.kind == "punct" and _wide(a.c)
        out.append({"a": a, "b": b, "s": s, "gap": gap, "rel": gap / s, "cls": _pair_class(a, b),
                    "space": is_space(st.doc, a, b), "grouped": grouped, "punct": punct})
    return out


def _refs(pairs: list[dict], max_gap: float) -> dict:
    ref = {}
    for cls in ("cjk", "latin"):
        v = [q["rel"] for q in pairs if q["cls"] == cls and not (q["space"] or q["grouped"] or q["punct"]) and -0.05 <= q["rel"] <= max_gap]
        lo, hi = REF_CLAMP[cls]
        ref[cls] = min(hi, max(lo, float(np.median(v)))) if len(v) >= 2 else DEF_REF[cls]
    return ref


def _limit(q: dict, max_gap: float) -> float:
    return max_gap + (PUNCT_LIM if q["punct"] else 0.0)


def stroke_mm(pkg: Package, g: Glyph) -> float | None:
    """Stroke width of a glyph in mm: ink mass / half the outline length (sprite px), times the glyph's scale."""
    spr = pkg.sprite(g)
    mask = np.pad(spr < 128, 1)
    n = int(mask.sum())
    if n < 12:
        return None
    inner = mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:]
    edge = n - int(inner.sum())
    mass = float(((255.0 - spr.astype(np.float32)) / 255.0).sum())
    w = 2.0 * mass / max(edge, 1)
    scale = math.sqrt(abs(np.linalg.det(g.m[:, :2])))
    return w * scale / pkg.ppm


def _line_text(st: State, gl: list[Glyph]) -> str:
    out = []
    for k, g in enumerate(gl):
        if k and is_space(st.doc, gl[k - 1], g):
            out.append(" ")
        out.append(g.c if g.kind != "math" or len(g.c) < 40 else g.c[:37] + "…")
    return "".join(out)


def _lref(p: int, line: dict) -> str:
    return f"p{p}l{line.get('id')}"


def inspect(pkg: Package, edits: list[dict] | None = None, max_gap: float = 0.35) -> dict:
    edits = pkg.read_edits() if edits is None else edits
    st = State(pkg, edits)
    ppm = pkg.ppm
    mm = lambda v: round(float(v) / ppm, 2)  # noqa: E731
    anomalies: list[dict] = []
    pages_out, lines_out = [], []
    lab = pkg.doc.get("label") or {}
    li = pkg.label_image()
    for p, page in enumerate(pkg.doc.get("pages") or []):
        W, H = (int(v) for v in page["size"])
        vis = st.visible(p)
        n = int(page.get("index", p)) + 1
        # lines + gaps
        for line in st.lines[p]:
            gl = st.line_glyphs(p, line)
            if not gl:
                continue
            pairs = line_pairs(st, gl)
            ref = _refs(pairs, max_gap)
            big = [q for q in pairs if not q["space"] and not q["grouped"] and q["rel"] > _limit(q, max_gap)]
            inks = np.array([g.ink for g in gl])
            lines_out.append({"ref": _lref(p, line), "page": n, "glyphs": len(gl), "text": _line_text(st, gl),
                              "box": line.get("box"), "left_mm": mm(inks[:, 0].min()), "right_mm": mm(inks[:, 2].max()),
                              "base_mm": mm(np.median([g.anchor[1] for g in gl])), "size_mm": mm(np.median([g.size for g in gl])),
                              "big_gaps": len(big), "natural_gap": round(ref["cjk"], 3), "grid": all(is_cell(g) for g in gl)})
            for q in big:
                a, b = q["a"], q["b"]
                anomalies.append({"type": "gap", "page": n, "line": _lref(p, line), "after": a.id, "before": b.id,
                                  "chars": f"{a.c[:12]}|{b.c[:12]}", "gap_mm": mm(q["gap"]), "gap_size": round(q["rel"], 2),
                                  "natural_size": round(ref[q["cls"]] + (PUNCT_TGT if q["punct"] else 0), 2),
                                  "at_mm": [mm(a.ink[2]), mm((a.ink[1] + a.ink[3]) / 2)]})
        # stroke weight in mm (raw ink + the pen's weight), each glyph against the same writer's glyphs that were laid
        # out at about the same size (small boxed text is naturally thinner; a glyph scaled up by an edit is not)
        rows = []
        for g in vis:
            if g.kind not in ("cn", "latin", "digit"):
                continue
            w = stroke_mm(pkg, g)
            if w is not None:
                rows.append((g, w + 2 * PEN.WEIGHT_MM * st.pen_of(g)["weight"], str(g.data.get("style") or "")))
        strokes: dict[str, float] = {}
        if rows:
            ws = np.array([w for _, w, _ in rows])
            s0 = np.array([g.size0 for g, _, _ in rows])
            sty = np.array([s for _, _, s in rows])
            for s in sorted(set(sty.tolist())):
                strokes[s or "-"] = round(float(np.median(ws[sty == s])), 3)
            for k, (g, w, s) in enumerate(rows):
                peers = (sty == s) & (np.abs(s0 / g.size0 - 1) <= 0.25)
                if peers.sum() < 5:
                    continue
                med = float(np.median(ws[peers]))
                r = w / med if med > 0 else 1.0
                if (r > 1.3 or r < 0.75) and abs(w - med) >= 0.05:
                    anomalies.append({"type": "stroke", "page": n, "id": g.id, "c": g.c, "width_mm": round(w, 3),
                                      "typical_mm": round(med, 3), "ratio": round(r, 2), "style": s,
                                      "why": "bolder than the rest" if r > 1 else "thinner than the rest"})
        # overlaps
        if len(vis) > 1:
            B = np.array([g.ink for g in vis])
            area = np.maximum(1.0, (B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]))
            iw = np.clip(np.minimum(B[:, None, 2], B[None, :, 2]) - np.maximum(B[:, None, 0], B[None, :, 0]), 0, None)
            ih = np.clip(np.minimum(B[:, None, 3], B[None, :, 3]) - np.maximum(B[:, None, 1], B[None, :, 1]), 0, None)
            frac = iw * ih / np.minimum(area[:, None], area[None, :])
            ii, jj = np.nonzero(np.triu(frac > 0.3, 1))
            for i, j in zip(ii.tolist(), jj.tolist()):
                a, b = vis[i], vis[j]
                if a.data.get("group") is not None and a.data.get("group") == b.data.get("group"):
                    continue
                anomalies.append({"type": "overlap", "page": n, "ids": [a.id, b.id], "chars": f"{a.c[:12]}|{b.c[:12]}",
                                  "overlap": round(float(frac[i, j]), 2)})
        # off the paper, margins, under the label
        orig = [g for g in st.pages[p] if not g.data.get("box")]
        if orig:
            bx0 = min(g.ink0[0] for g in orig)
            bx1 = max(g.ink0[2] for g in orig)
        margin_x = 24 * ppm if (page.get("paper") or {}).get("id") in ("ruled8", "ruled7") else None
        lbox = None
        if lab.get("mode") != "none" and li is not None and lab.get("xy"):
            lbox = (lab["xy"][0], lab["xy"][1], lab["xy"][0] + li.width, lab["xy"][1] + li.height)
        edge = 5 * ppm
        for g in vis:
            k = g.ink
            if k[0] < 0 or k[1] < 0 or k[2] > W or k[3] > H:
                anomalies.append({"type": "off_paper", "page": n, "id": g.id, "c": g.c, "ink_mm": [mm(v) for v in k]})
                continue
            if min(k[0], k[1], W - k[2], H - k[3]) < edge:
                anomalies.append({"type": "near_edge", "page": n, "id": g.id, "c": g.c, "ink_mm": [mm(v) for v in k]})
            if lbox and k[0] < lbox[2] and k[2] > lbox[0] and k[1] < lbox[3] and k[3] > lbox[1]:
                anomalies.append({"type": "under_label", "page": n, "id": g.id, "c": g.c})
            if g.edited and not g.data.get("box"):
                if margin_x and k[0] < margin_x <= g.ink0[0]:
                    anomalies.append({"type": "outside_margin", "page": n, "id": g.id, "c": g.c, "side": "left",
                                      "by_mm": mm(margin_x - k[0]), "why": "crosses the red margin line"})
                elif orig and (k[0] < bx0 - 0.6 * g.size or k[2] > bx1 + 0.6 * g.size):
                    side = "left" if k[0] < bx0 - 0.6 * g.size else "right"
                    by = (bx0 - k[0]) if side == "left" else (k[2] - bx1)
                    anomalies.append({"type": "outside_margin", "page": n, "id": g.id, "c": g.c, "side": side, "by_mm": mm(by),
                                      "why": "outside the text block of the original layout"})
        hidden = [g.id for g in st.pages[p] if g.hidden]
        if hidden:
            anomalies.append({"type": "hidden", "page": n, "ids": hidden, "chars": "".join(st.glyphs[i].c for i in hidden)[:80]})
        lefts = [ln["left_mm"] for ln in lines_out if ln["page"] == n and not ln["box"] and not ln["grid"]]
        aligned = 0
        if len(lefts) >= 3:
            lm = float(np.median(lefts))
            aligned = sum(abs(v - lm) < 0.15 for v in lefts)
        pages_out.append({"page": n, "size_px": [W, H], "glyphs": len(st.pages[p]), "visible": len(vis), "hidden": len(hidden),
                          "lines": len(st.lines[p]), "paper": {k: v for k, v in (page.get("paper") or {}).items() if k in ("kind", "id", "upload")},
                          "layers": [{"pen": pen, "glyphs": len(gl)} for pen, gl in st.layers(p)],
                          "stroke_mm": strokes, "left_edges_aligned": aligned})
    counts: dict[str, int] = {}
    for a in anomalies:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    hints = []
    if counts.get("gap"):
        refs = sorted({a["line"] for a in anomalies if a["type"] == "gap"}, key=lambda r: tuple(map(int, re.findall(r"\d+", r))))
        hints.append(f"scene.py tighten <pkg> --line {','.join(refs[:12])}{',…' if len(refs) > 12 else ''}   (or --line all)")
    if any(p["left_edges_aligned"] >= 3 for p in pages_out):
        hints.append("left edges are ruler-straight: scene.py drift <pkg>")
    if counts.get("stroke"):
        hints.append("odd stroke weight: check those glyphs; a per-glyph pen (restyle --ids … --weight -0.3) evens them out")
    if counts.get("off_paper") or counts.get("outside_margin") or counts.get("under_label"):
        hints.append("some glyphs left the writing area: move them back (move --ids … --dx …) or undo")
    job = pkg.doc.get("job") or {}
    return {"package": str(pkg.folder), "job": {k: job.get(k) for k in ("id", "model", "kind", "created_at")},
            "pen": pkg.doc.get("pen"), "label": (pkg.doc.get("label") or {}).get("mode", "visible"),
            "edits": {"count": len(edits), "file": str(pkg.edits_file), "last": edits[-3:]},
            "pages": pages_out, "lines": lines_out, "anomalies": anomalies, "counts": counts, "hints": hints,
            "max_gap": max_gap, "warnings": st.warnings}


# ── high-level commands -> atomic ops ────────────────────────────────────────────────────────────────────────────────────

def _angle(gl: list[Glyph]) -> float:
    """Writing direction of a line (radians): the glyphs' median rotation when it is clearly not level (a rotated box)."""
    r = float(np.median([g.rot for g in gl])) if gl else 0.0
    return math.radians(r) if abs(r) > 0.3 else 0.0


def _moves(shifts: list[tuple[str, float]], theta: float, ppm: float, eps: float = 0.05) -> list[dict]:
    """[(id, shift px along the line)] -> move ops, one per distinct shift."""
    by: dict[float, list[str]] = {}
    for gid, s in shifts:
        if abs(s) >= eps:
            by.setdefault(round(s, 3), []).append(gid)
    ops = []
    for s, ids in by.items():
        ops.append({"op": "move", "ids": ids, "dx": round(s * math.cos(theta) / ppm, 4), "dy": round(s * math.sin(theta) / ppm, 4)})
    return ops


def select_lines(st: State, spec: str | None, page: int | None) -> list[tuple[int, dict]]:
    """'all' / None -> every line; 'p0l3,p1l2' or '3,4' (on --page, 1-based) -> those lines."""
    if not spec or spec == "all":
        if page is not None and not 1 <= page <= len(st.pages):
            die(f"--page {page}: the package has {len(st.pages)} page(s)")
        pages = range(len(st.pages)) if page is None else [page - 1]
        return [(p, ln) for p in pages for ln in st.lines[p]]
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        m = re.fullmatch(r"p(\d+)l(\d+)", part) or re.fullmatch(r"()(\d+)", part)
        if not m:
            die(f"line {part!r}: use p0l3 (page index 0, line 3) or a bare line number with --page")
        p = int(m.group(1)) if m.group(1) else (page or 1) - 1
        lid = int(m.group(2))
        if not 0 <= p < len(st.lines):
            die(f"line {part!r}: no page {p}")
        ln = next((x for x in st.lines[p] if x.get("id") == lid), None)
        if ln is None:
            die(f"line {part!r}: page {p} has lines 0..{len(st.lines[p]) - 1}")
        out.append((p, ln))
    return out


def op_tighten(st: State, lines: list[tuple[int, dict]], max_gap: float = 0.35, seed: int = 0, jitter: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Shrink ink gaps wider than max_gap x size to a natural gap (the line's own typical gap, +-jitter); everything after
    a shrunk gap moves left with it. Word spaces and gaps inside a group (formula) are never touched."""
    rng = random.Random(seed)
    jitter = max(0.0, min(0.5, float(jitter)))              # a larger jitter could make a gap negative (glyphs collide)
    ops, report = [], []
    for p, line in lines:
        gl = st.line_glyphs(p, line)
        if len(gl) < 2:
            continue
        pairs = line_pairs(st, gl)
        ref = _refs(pairs, max_gap)
        shift, shifts, fixed = 0.0, [], []
        for q in pairs:
            if not q["space"] and not q["grouped"] and q["rel"] > _limit(q, max_gap):
                tgt = (ref[q["cls"]] + (PUNCT_TGT if q["punct"] else 0.0)) * (1 + rng.uniform(-jitter, jitter))
                d = (tgt - q["rel"]) * q["s"]
                shift += d
                fixed.append({"after": q["a"].id, "before": q["b"].id, "chars": f"{q['a'].c[:12]}|{q['b'].c[:12]}",
                              "from_size": round(q["rel"], 2), "to_size": round(tgt, 2), "mm": round(d / st.pkg.ppm, 2)})
            if not is_cell(q["b"]):                            # a glyph in a grid cell stays in its cell
                shifts.append((q["b"].id, shift))
        if fixed:
            ops += _moves(shifts, _angle(gl), st.pkg.ppm)
            report.append({"line": _lref(p, line), "gaps": fixed})
    return ops, report


def _pitch(st: State, p: int) -> float:
    bases = sorted(float(np.median([g.anchor0[1] for g in st.line_glyphs(p, ln, False)])) for ln in st.lines[p]
                   if not ln.get("box") and st.line_glyphs(p, ln, False))
    d = [b - a for a, b in zip(bases, bases[1:]) if b - a > 1]
    return float(np.median(d)) if d else 0.0


def op_drift(st: State, mode: str = "natural", amount: float = 1.0, seed: int = 0) -> tuple[list[dict], list[dict]]:
    """Natural left edges: every written line (boxed ones excluded) moves sideways as a whole. natural = a slow creep to
    the right plus smoothed noise, capped at ~0.45 glyph size, partly reset at a blank line / new problem; random = the
    smoothed noise alone. The line never leaves the paper or crosses a ruled paper's margin line."""
    rng = random.Random(seed)
    ppm = st.pkg.ppm
    amount = max(0.0, min(2.0, float(amount)))
    creep = noise = 0.0
    prev: tuple[int, Glyph, float] | None = None
    ops, report = [], []
    for p in range(len(st.pages)):
        page = st.doc["pages"][p]
        W = int(page["size"][0])
        pitch = _pitch(st, p)
        ruled = (page.get("paper") or {}).get("id") in ("ruled8", "ruled7")
        orig = [g for g in st.pages[p] if not g.data.get("box")]
        right0 = max((g.ink0[2] for g in orig), default=W - 10 * ppm)
        order = sorted((ln for ln in st.lines[p] if not ln.get("box")), key=lambda ln: (ln.get("base", 0), ln.get("x0", 0)))
        for line in order:
            gl = [g for g in st.line_glyphs(p, line) if not g.data.get("box") and not is_cell(g)]
            if not gl:
                continue
            s = float(np.median([g.size for g in gl]))
            base = float(np.median([g.anchor[1] for g in gl]))
            if prev is not None:
                t = text_between(st.doc, prev[1], gl[0])
                blank = bool(t and re.search(r"\n\s*\n", t)) or (prev[0] == p and pitch and base - prev[2] > 1.7 * pitch)
                if blank or PROBLEM_RE.match(_line_text(st, gl)):
                    creep *= rng.uniform(0.15, 0.4)                    # a new problem / paragraph: the hand re-anchors
            cap = 0.45 * s * max(amount, 1e-9)
            noise = 0.6 * noise + rng.gauss(0.0, 0.05) * s * amount
            if mode == "natural":
                creep += rng.uniform(0.03, 0.08) * s * amount
                if creep > 0.85 * cap:                                 # noticed it and pulled back a little
                    creep = cap * rng.uniform(0.55, 0.8)
                d = min(cap, max(-0.06 * s * amount, creep + noise))
            else:
                d = min(0.6 * cap, max(-0.6 * cap, 1.8 * noise))
            inks = np.array([g.ink for g in gl])
            x0, x1 = float(inks[:, 0].min()), float(inks[:, 2].max())
            lo = 5 * ppm
            if ruled and x0 >= 24 * ppm:
                lo = 24 * ppm + 0.3 * ppm
            hi = min(W - 5 * ppm, max(right0 + 0.3 * s, x1))
            d = max(d, min(0.0, lo - x0))
            d = min(d, max(0.0, hi - x1))
            if abs(d) >= 0.02 * ppm:
                ops.append({"op": "move", "ids": [g.id for g in gl], "dx": round(d / ppm, 4), "dy": 0.0})
            report.append({"line": _lref(p, line), "dx_mm": round(d / ppm, 2), "text": _line_text(st, gl)[:24]})
            prev = (p, gl[-1], base)
    return ops, report


def op_gap(st: State, a_id: str, b_id: str, target_px: float, rest: bool = True) -> list[dict]:
    a, b = st.glyphs.get(a_id), st.glyphs.get(b_id)
    if a is None or b is None:
        die(f"unknown glyph id: {a_id if a is None else b_id}")
    d = target_px - float(b.ink[0] - a.ink[2])
    ids = [b.id]
    line = st.line_of(b)
    gl = st.line_glyphs(b.p, line) if line else [b]
    if rest and line and b in gl:
        ids = [g.id for g in gl[gl.index(b):]]
    return _moves([(i, d) for i in ids], _angle(gl), st.pkg.ppm, eps=1e-6)


def op_align(st: State, lines: list[tuple[int, dict]], strength: float = 1.0) -> list[dict]:
    """Put every glyph's baseline anchor on its line's (straight, possibly sloped) baseline."""
    ops = []
    for p, line in lines:
        gl = st.line_glyphs(p, line)
        if len(gl) < 2:
            continue
        slope = math.tan(_angle(gl))
        c = float(np.median([g.anchor[1] - slope * g.anchor[0] for g in gl]))
        for g in gl:
            dy = strength * (c + slope * g.anchor[0] - g.anchor[1])
            if abs(dy) >= 0.1:
                ops.append({"op": "move", "ids": [g.id], "dx": 0.0, "dy": round(dy / st.pkg.ppm, 4)})
    return ops


# ── CLI ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _next_batch(edits: list[dict]) -> int:
    return max([op["batch"] for op in edits if isinstance(op.get("batch"), int)] + [0]) + 1


def _append(pkg: Package, edits: list[dict], ops: list[dict], dry: bool, extra: dict | None = None) -> None:
    b = _next_batch(edits)
    ops = [{**check_op(op), "batch": b} for op in ops]
    if not dry and ops:
        pkg.write_edits(edits + ops)
    moved = sum(len(op["ids"]) if isinstance(op.get("ids"), list) else 0 for op in ops if op["op"] != "note")
    emit({"added": 0 if dry else len(ops), "dry_run": dry, "batch": b, "ops": ops, "glyph_ops": moved,
          "edits": len(edits) + (0 if dry else len(ops)), "file": str(pkg.edits_file), **(extra or {}),
          **({"next": "scene.py render <pkg> -o out/  (or inspect again)"} if ops and not dry else {})})


def _seed(pkg: Package, seed: int | None) -> int:
    return seed if seed is not None else zlib.crc32(str((pkg.doc.get("job") or {}).get("id", "")).encode()) & 0xFFFF


def _ids(st: State, a) -> list[str] | str:
    if getattr(a, "ids", None):
        if a.ids.strip() == "all":
            return "all"
        ids = [i.strip() for i in a.ids.split(",") if i.strip()]
        bad = [i for i in ids if i not in st.glyphs]
        if bad:
            die(f"unknown glyph ids: {', '.join(bad[:8])} (see `scene.py inspect`)")
        return ids
    if getattr(a, "line", None):
        ids = [g.id for p, ln in select_lines(st, a.line, getattr(a, "page", None)) for g in st.line_glyphs(p, ln, False)]
        if not ids:
            die("those lines have no glyphs")
        return ids
    if getattr(a, "page", None):
        if not 1 <= a.page <= len(st.pages):
            die(f"--page {a.page}: the package has {len(st.pages)} page(s)")
        return [g.id for g in st.pages[a.page - 1]]
    return "all" if getattr(a, "all_default", False) else die("say which glyphs: --ids p0g1,p0g2 | --line p0l3 | --page 1 | --ids all")


def cmd_inspect(a) -> None:
    pkg = open_package(a.scene)
    emit(inspect(pkg, max_gap=a.max_gap))


def cmd_render(a) -> None:
    pkg = open_package(a.scene)
    emit(render(pkg, Path(a.out), a.pages, a.format, a.pdf, a.quality, a.pdf_jpeg))


def cmd_atomic(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    st = State(pkg, edits)
    kind = a.cmd
    if kind == "note":
        _append(pkg, edits, [{"op": "note", "text": a.text}], False)
        return
    ids = _ids(st, a)
    if kind == "move":
        op = {"op": "move", "ids": ids, "dx": a.dx, "dy": a.dy}
    elif kind == "scale":
        op = {"op": "scale", "ids": ids, "k": a.k, "about": a.about}
    elif kind == "rotate":
        op = {"op": "rotate", "ids": ids, "deg": a.deg, "about": a.about}
    else:
        op = {"op": kind, "ids": ids}
    try:
        _append(pkg, edits, [op], a.dry_run)
    except ValueError as e:
        die(str(e))


def cmd_restyle(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    st = State(pkg, edits)
    a.all_default = True
    ids = _ids(st, a)
    if a.reset:
        ops = [{"op": "pen", "ids": ids, "pen": None}]
    else:
        # only the fields given change: every glyph keeps the rest of ITS current pen (a blue gel line made bolder stays
        # blue gel), so glyphs with different pens get one op each
        given = {k: v for k, v in (("type", a.type), ("color", a.color), ("weight", a.weight), ("ink", a.ink)) if v is not None}
        if not given:
            die("say what to change: --type / --color / --weight / --ink (or --reset for the package's own pen)")
        by: dict[str, tuple[dict, list[str]]] = {}
        for g in st.resolve(ids):
            pen = PEN.normalize({**st.pen_of(g), **given})
            by.setdefault(json.dumps(pen, sort_keys=True), (pen, []))[1].append(g.id)
        if not by:
            die("no glyphs selected")
        if ids == "all" and len(by) == 1:
            ops = [{"op": "pen", "ids": "all", "pen": next(iter(by.values()))[0]}]
        else:
            ops = [{"op": "pen", "ids": gl, "pen": pen} for pen, gl in by.values()]
    _append(pkg, edits, ops, a.dry_run,
            {"note": "pen texture / weight show in `render`; the editor previews the ink colour only"})


def cmd_tighten(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    st = State(pkg, edits)
    lines = select_lines(st, a.line, a.page)
    seed = _seed(pkg, a.seed)
    ops, rep = op_tighten(st, lines, a.max_gap, seed, a.jitter)
    if not ops:
        emit({"added": 0, "message": f"no ink gap wider than {a.max_gap} x glyph size on those lines", "lines": len(lines)})
        return
    head = {"op": "note", "text": f"tighten {a.line or 'all'} max_gap={a.max_gap} seed={seed} (auto)"}
    _append(pkg, edits, [head] + ops, a.dry_run, {"lines": rep})


def cmd_drift(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    if not a.again and any(op.get("op") == "note" and str(op.get("text", "")).startswith("drift ") for op in edits):
        die("a drift is already applied (see edits.json); `scene.py undo` it first, or pass --again to add another")
    st = State(pkg, edits)
    seed = _seed(pkg, a.seed)
    ops, rep = op_drift(st, a.mode, a.amount, seed)
    if not ops:
        emit({"added": 0, "message": "no line needed to move (amount 0, or every line is boxed / at the margin)", "lines": rep})
        return
    head = {"op": "note", "text": f"drift {a.mode} amount={a.amount} seed={seed} (auto)"}
    _append(pkg, edits, [head] + ops, a.dry_run, {"lines": rep})


def cmd_gap(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    st = State(pkg, edits)
    g = st.glyphs.get(a.a)
    if g is None or a.b not in st.glyphs:
        die(f"unknown glyph id: {a.a if g is None else a.b}")
    if (a.mm is None) == (a.size is None):
        die("give the gap as --mm 0.8 or as --size 0.12 (x glyph size)")
    tgt = a.mm * pkg.ppm if a.mm is not None else a.size * (g.size + st.glyphs[a.b].size) / 2
    _append(pkg, edits, op_gap(st, a.a, a.b, tgt, not a.only_b), a.dry_run)


def cmd_align(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    st = State(pkg, edits)
    ops = op_align(st, select_lines(st, a.line, a.page), a.strength)
    if not ops:
        emit({"added": 0, "message": "baselines already aligned"})
        return
    _append(pkg, edits, [{"op": "note", "text": f"align-baseline {a.line or 'all'} strength={a.strength} (auto)"}] + ops, a.dry_run)


def undo_edits(edits: list[dict], n: int, atomic: bool = False) -> int:
    """Index where the kept edits end after undoing the last n commands (batches) or n atomic ops."""
    if atomic:
        return max(0, len(edits) - n)
    keys = [op["batch"] if isinstance(op.get("batch"), int) else f"solo{i}" for i, op in enumerate(edits)]
    cut, seen = len(edits), 0
    while cut > 0 and seen < n:
        k = keys[cut - 1]
        while cut > 0 and keys[cut - 1] == k:
            cut -= 1
        seen += 1
    return cut


def cmd_undo(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    if not edits:
        emit({"removed": 0, "edits": 0, "message": "nothing to undo"})
        return
    cut = undo_edits(edits, max(1, a.n), a.ops)
    pkg.write_edits(edits[:cut])
    emit({"removed": len(edits) - cut, "edits": cut, "removed_ops": edits[cut:][-20:], "file": str(pkg.edits_file)})


def cmd_reset(a) -> None:
    pkg = open_package(a.scene)
    edits = pkg.read_edits()
    bak = None
    if pkg.edits_file.exists():
        bak = pkg.folder / "edits.bak.json"
        shutil.copyfile(pkg.edits_file, bak)
    pkg.write_edits([])
    emit({"removed": len(edits), "edits": 0, "backup": str(bak) if bak else None})


# ── editor (local web GUI) ───────────────────────────────────────────────────────────────────────────────────────────────

def compute(st: State, cmd: str, prm: dict) -> dict:
    """High-level command for the editor: ops computed on the editor's current (maybe unsaved) edits."""
    seed = prm.get("seed")
    seed = _seed(st.pkg, seed if isinstance(seed, int) else None)
    lines_spec = prm.get("lines") or "all"
    if isinstance(lines_spec, list):
        lines_spec = ",".join(str(x) for x in lines_spec) or "all"
    if cmd == "tighten":
        ops, rep = op_tighten(st, select_lines(st, lines_spec, None), float(prm.get("max_gap", 0.35)), seed)
        return {"ops": ([{"op": "note", "text": f"tighten {lines_spec} (editor)"}] + ops) if ops else [], "report": rep}
    if cmd == "drift":
        mode = prm.get("mode") if prm.get("mode") in ("natural", "random") else "natural"
        ops, rep = op_drift(st, mode, float(prm.get("amount", 1.0)), seed)
        return {"ops": ([{"op": "note", "text": f"drift {mode} seed={seed} (editor)"}] + ops) if ops else [], "report": rep}
    if cmd == "align-baseline":
        ops = op_align(st, select_lines(st, lines_spec, None), float(prm.get("strength", 1.0)))
        return {"ops": ([{"op": "note", "text": "align-baseline (editor)"}] + ops) if ops else [], "report": []}
    raise ValueError(f"unknown command {cmd!r}")


class _Stop(Exception):
    pass


# what the editor page shows the person when a request fails (the customers read Chinese; the codes stay machine-readable).
# The page puts its own "保存失败：" / "收紧间距没能完成：" … in front, so these say only why. Same texts as ERR_ZH in
# scene_editor.html (the page shows its own copy for a known code). "failed" = a die() inside a helper: in practice the
# package or edits.json on disk is broken (e.g. a hand edit left invalid JSON).
ERR_ZH = {
    "bad_host": "只能在本机浏览器里打开编辑器（地址要以 127.0.0.1 开头）。",
    "bad_token": "这个页面已经失效（编辑器可能重启过）。请刷新页面。",
    "not_found": "找不到要读取的内容。请刷新页面再试。",
    "bad_edits": "修改记录里有无法识别的内容。",
    "conflict": "修改记录在你打开编辑器之后被别处改过了（可能是 AI 同时在改）。请刷新页面再继续；这里还没保存的修改会丢失。",
    "bad_request": "请求的内容有误。",
    "failed": "笔迹包或修改记录（edits.json）读不出来，可能被改坏了。请回到对话里让 AI 检查一下。",
    "internal": "编辑器内部出错了。",
}


def make_server(pkg: Package, port: int = 0, verbose: bool = False) -> tuple[ThreadingHTTPServer, str]:
    html = (HERE / "scene_editor.html").read_text("utf-8")
    token = secrets.token_urlsafe(18)
    lock = threading.Lock()
    types = {".png": "image/png", ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8"}

    class H(BaseHTTPRequestHandler):
        server_version = "inko-scene-editor"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A002
            if verbose:
                note("editor: " + fmt % args)

        def _host_ok(self) -> bool:
            port_ = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)).encode("utf-8"),
                       "application/json; charset=utf-8")

        # error bodies: "error" is shown to the person using the editor (Chinese), "code" is for the page's own logic,
        # "detail" (English, optional) is for debugging only and never displayed. An error may answer before the request
        # body was read (wrong host / token / path): close the connection, or the unread body would be parsed as the next
        # keep-alive request (-> a 501 for the page's next save)
        def _err(self, status: int, code: str, detail: str | None = None) -> None:
            self.close_connection = True
            self._json(status, {"error": ERR_ZH.get(code, ERR_ZH["internal"]), "code": code, **({"detail": detail} if detail else {})})

        def _guard(self, write: bool) -> bool:
            if not self._host_ok():
                self._err(403, "bad_host")
                return False
            if write and not secrets.compare_digest(self.headers.get("X-Inko-Token", ""), token):
                self._err(403, "bad_token")
                return False
            return True

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= 64 << 20:
                raise ValueError("bad or too large Content-Length")
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        def do_GET(self):  # noqa: N802
            if not self._guard(False):
                return
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, html.replace("__INKO_TOKEN__", token).encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/edits":
                try:
                    with lock:
                        state = {"edits": pkg.read_edits(), "rev": pkg.edits_rev()}
                except SystemExit as e:              # edits.json on disk is not valid (its message went to stderr): answer,
                    self._err(500, "failed", f"edits.json unreadable (exit {e.code})")   # don't drop the connection
                    return
                self._json(200, state)
            elif path.startswith("/pkg/"):
                rel = unquote(path[5:])
                f = pkg.folder / rel
                if not FILE_RE.fullmatch(rel) or not f.is_file():
                    self._err(404, "not_found")
                    return
                self._send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream"))
            else:
                self._err(404, "not_found")

        def do_PUT(self):  # noqa: N802
            if not self._guard(True):
                return
            if urlparse(self.path).path != "/api/edits":
                self._err(404, "not_found")
                return
            try:
                body = self._body()
                ops = body.get("edits")
                if not isinstance(ops, list):
                    raise ValueError("edits must be a list")
                clean = [check_op(op) for op in ops]
            except ValueError as e:
                self._err(400, "bad_edits", str(e))
                return
            with lock:
                if body.get("rev") is not None and body["rev"] != pkg.edits_rev():
                    self._json(409, {"error": ERR_ZH["conflict"], "code": "conflict", "rev": pkg.edits_rev(),
                                     "detail": "edits.json changed on disk since the editor loaded it (another command?)"})
                    return
                pkg.write_edits(clean)
                self._json(200, {"ok": True, "count": len(clean), "rev": pkg.edits_rev()})

        def do_POST(self):  # noqa: N802
            if not self._guard(True):
                return
            path = urlparse(self.path).path
            try:
                body = self._body()
                if path == "/api/inspect":
                    self._json(200, inspect(pkg, [check_op(op) for op in body.get("edits") or []], float(body.get("max_gap", 0.35))))
                elif path == "/api/compute":
                    st = State(pkg, [check_op(op) for op in body.get("edits") or []])
                    res = compute(st, str(body.get("cmd")), body.get("params") or {})
                    res["ops"] = [check_op(op) for op in res["ops"]]
                    self._json(200, res)
                elif path == "/api/render":
                    with lock:
                        out = render(pkg, pkg.folder / "render", None, "jpg" if body.get("format") == "jpg" else "png", bool(body.get("pdf")))
                    self._json(200, out)
                else:
                    self._err(404, "not_found")
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                self._err(400, "bad_request", f"{type(e).__name__}: {e}")
            except SystemExit as e:                                   # die() inside a helper (its message went to stderr)
                self._err(400, "failed", f"exit {e.code}")
            except Exception as e:  # noqa: BLE001                    # never drop the connection without an answer
                note(f"editor: {path} failed: {type(e).__name__}: {e}")
                self._err(500, "internal", f"{type(e).__name__}: {e}")

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def cmd_editor(a) -> None:
    pkg = open_package(a.scene)
    srv, url = make_server(pkg, a.port, a.verbose)
    sys.stdout.write(json.dumps({"url": url, "folder": str(pkg.folder), "edits": str(pkg.edits_file),
                                 "message": "editor running on 127.0.0.1 only; press Ctrl+C to stop",
                                 "hint": "在浏览器里打开这个地址编辑；改完点「保存」，然后回到对话里告诉 AI。"},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, ids=False, lines=False, dry=True, aliases=()):
        s = sub.add_parser(name, help=help_, aliases=list(aliases))
        s.add_argument("scene", help="scene.zip, its extracted folder, or scene.json")
        if ids:
            s.add_argument("--ids", help="comma-separated glyph ids (p0g12,p0g13) or all")
        if ids or lines:
            s.add_argument("--line", help="line refs p0l3,p0l4 (or bare numbers on --page) or all")
            s.add_argument("--page", type=int, help="page number (1-based)")
        if dry:
            s.add_argument("--dry-run", action="store_true", help="print the ops without saving them")
        s.set_defaults(fn=fn)
        return s

    s = add("inspect", cmd_inspect, "pages, lines, pens and anomalies", dry=False)
    s.add_argument("--max-gap", type=float, default=0.35, help="flag ink gaps wider than this x glyph size (default 0.35)")
    s = add("render", cmd_render, "render pages (label + AIGC metadata always kept)", dry=False)
    s.add_argument("-o", "--out", required=True, help="output folder")
    s.add_argument("--pages", help="1,2 or 1-3 (default: all)")
    s.add_argument("--format", choices=["png", "jpg"], default="png")
    s.add_argument("--quality", type=int, default=92, help="JPEG quality")
    s.add_argument("--pdf", action="store_true", help="also write inko.pdf (lossless) with the AIGC metadata")
    s.add_argument("--pdf-jpeg", type=int, default=0, help="embed JPEG at this quality in the PDF instead of lossless")
    s = add("tighten", cmd_tighten, "shrink gaps that are too wide", lines=True)
    s.add_argument("--max-gap", type=float, default=0.35, help="gaps wider than this x glyph size are shrunk (default 0.35)")
    s.add_argument("--jitter", type=float, default=0.2, help="randomness of the new gaps (+-20 %% default)")
    s.add_argument("--seed", type=int)
    s = add("drift", cmd_drift, "natural left edges (line by line)")
    s.add_argument("--mode", choices=["natural", "random"], default="natural")
    s.add_argument("--amount", type=float, default=1.0, help="0.5 = half as much, 2 = twice (max)")
    s.add_argument("--seed", type=int)
    s.add_argument("--again", action="store_true", help="add a drift on top of an earlier one")
    s = add("gap", cmd_gap, "set the ink gap between two glyphs")
    s.add_argument("--a", required=True, help="left glyph id")
    s.add_argument("--b", required=True, help="right glyph id")
    s.add_argument("--mm", type=float)
    s.add_argument("--size", type=float, help="gap as a fraction of the glyph size")
    s.add_argument("--only-b", action="store_true", help="move only --b (default: --b and the rest of its line)")
    s = add("align-baseline", cmd_align, "put glyphs on a straight baseline", lines=True)
    s.add_argument("--strength", type=float, default=1.0, help="1 = fully aligned, 0.5 = halfway")
    s = add("restyle", cmd_restyle, "pen for all or some glyphs", ids=True, aliases=("pen",))
    s.add_argument("--type", choices=list(PEN.TYPES))
    s.add_argument("--color", choices=list(PEN.COLORS))
    s.add_argument("--weight", type=float, help="-1 (thinner) .. 1 (bolder)")
    s.add_argument("--ink", type=float, help="-1 (lighter) .. 1 (darker)")
    s.add_argument("--reset", action="store_true", help="back to the package's own pen")
    s = add("move", cmd_atomic, "move glyphs (mm)", ids=True)
    s.add_argument("--dx", type=float, default=0.0)
    s.add_argument("--dy", type=float, default=0.0)
    for name, key, hlp in (("scale", "--k", "scale factor"), ("rotate", "--deg", "degrees, + = clockwise")):
        s = add(name, cmd_atomic, f"{name} glyphs", ids=True)
        s.add_argument(key, type=float, required=True, help=hlp)
        s.add_argument("--about", default="anchor", choices=["anchor", "center", "selection"])
    add("hide", cmd_atomic, "hide glyphs", ids=True)
    add("show", cmd_atomic, "show hidden glyphs again", ids=True)
    s = add("note", cmd_atomic, "add a note to edits.json", dry=False)
    s.add_argument("--text", required=True)
    s = add("undo", cmd_undo, "undo the last command(s)", dry=False)
    s.add_argument("--n", type=int, default=1)
    s.add_argument("--ops", action="store_true", help="count atomic ops instead of commands")
    add("reset", cmd_reset, "drop all edits (backup kept)", dry=False)
    s = add("editor", cmd_editor, "local web editor on 127.0.0.1", dry=False)
    s.add_argument("--port", type=int, default=0, help="0 = a random free port")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if a.cmd == "pen":
        a.cmd = "restyle"
    if not hasattr(a, "dry_run"):
        a.dry_run = False
    a.fn(a)


if __name__ == "__main__":
    main()
