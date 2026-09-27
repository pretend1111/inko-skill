#!/usr/bin/env python3
"""Tests for editable handwriting packages (inko-scene v1): scripts/_pen.py, scripts/scene.py, the editor server.
Offline, CPU only, no API key.

    python tests/test_scene.py                        # outputs in tests/_out/scene/
    python tests/test_scene.py --samples DIR          # also check against real worker packages: DIR/<case>/scene.zip +
                                                      # DIR/<case>/page-N.png (the server's pages for that package)
    python tests/test_scene.py --plain-samples DIR    # plain (Lyric page) packages: the --keep folder of the worker's
                                                      # test_scene_lyric.py (scene.zip + server-N.png + scene-N.png)

Without --samples (or $INKO_SCENE_SAMPLES) the test looks for the Inko worker next to this repo (../inkotype/worker, or
$INKOTYPE_WORKER) and runs its CPU-only `scene_sample.py` to make those packages; if that is not available the
server-parity checks are skipped (reported), everything else runs on a synthetic package built here.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import traceback
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
S = ROOT / "inko-handwriting" / "scripts"
SCENE = S / "scene.py"
OUT = HERE / "_out" / "scene"
sys.path.insert(0, str(S))

import numpy as np  # noqa: E402

from _common import Image, ImageDraw, ImageFilter, LABEL_MIN_FRAC, cjk_font, read_meta, text_height  # noqa: E402
import _pen as PEN  # noqa: E402
import scene as SC  # noqa: E402

PPM = 300 / 25.4
W, H = 2480, 3508
RESULTS: list[tuple[str, str, str]] = []            # (name, PASS | FAIL | SKIP, detail)
INFO: list[str] = []


class Skip(Exception):
    pass


def test(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, "PASS", ""))
        except Skip as e:
            RESULTS.append((name, "SKIP", str(e)))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, "FAIL", f"{e}\n{traceback.format_exc(limit=4)}"))
        return fn
    return deco


def run(*args, ok: bool = True) -> dict:
    r = subprocess.run([sys.executable, str(SCENE), *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if ok and r.returncode != 0:
        raise AssertionError(f"scene.py {' '.join(map(str, args))} failed ({r.returncode}):\n{r.stderr[-1500:]}")
    try:
        js = json.loads(r.stdout) if r.stdout.strip() else {}
    except ValueError:
        js = {"_stdout": r.stdout}
    js["_code"], js["_stderr"] = r.returncode, r.stderr
    return js


def compare(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    """(max abs diff, fraction of identical pixels) of two 8-bit images of the same mode."""
    assert a.shape == b.shape, (a.shape, b.shape)
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    same = (d == 0) if d.ndim == 2 else (d == 0).all(-1)
    return int(d.max()), float(same.mean())


# ── an independent reference renderer (numpy, no PIL transform) ──────────────────────────────────────────────────────────

def ref_place(ink: np.ndarray, spr: np.ndarray, m) -> None:
    """Page pixel centres -> sprite coordinates through inverse(m) -> bilinear sample (Pillow's edge rules: a sample point
    outside [0, w) x [0, h) is paper, neighbours are clamped) -> min into ink."""
    m3 = np.vstack([np.asarray(m, np.float64).reshape(2, 3), [0, 0, 1]])
    gh, gw = spr.shape
    c = np.array([[0, 0, 1], [gw, 0, 1], [0, gh, 1], [gw, gh, 1]], np.float64) @ m3.T
    x0, y0 = max(0, int(np.floor(c[:, 0].min())) - 1), max(0, int(np.floor(c[:, 1].min())) - 1)
    x1, y1 = min(ink.shape[1], int(np.ceil(c[:, 0].max())) + 1), min(ink.shape[0], int(np.ceil(c[:, 1].max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
    iv = np.linalg.inv(m3)
    u = iv[0, 0] * X + iv[0, 1] * Y + iv[0, 2]
    v = iv[1, 0] * X + iv[1, 1] * Y + iv[1, 2]
    inside = (u >= 0) & (u < gw) & (v >= 0) & (v < gh)
    fx, fy = u - 0.5, v - 0.5
    xi, yi = np.floor(fx).astype(int), np.floor(fy).astype(int)
    dx, dy = fx - xi, fy - yi
    xa, xb = np.clip(xi, 0, gw - 1), np.clip(xi + 1, 0, gw - 1)
    ya, yb = np.clip(yi, 0, gh - 1), np.clip(yi + 1, 0, gh - 1)
    Sf = spr.astype(np.float64)
    v1 = Sf[ya, xa] + (Sf[ya, xb] - Sf[ya, xa]) * dx
    v2 = Sf[yb, xa] + (Sf[yb, xb] - Sf[yb, xa]) * dx
    val = np.where(inside, v1 + (v2 - v1) * dy, 255.0)
    patch = (np.floor(val + 0.5).clip(0, 255) / 255.0).astype(np.float32)
    ink[y0:y1, x0:x1] = np.minimum(ink[y0:y1, x0:x1], patch)


# ── a synthetic package (same format as the worker writes) ───────────────────────────────────────────────────────────

def _kind(c: str) -> str:
    if c.isdigit():
        return "digit"
    cat = unicodedata.category(c)
    if cat[0] in "PSZ":
        return "punct"
    return "latin" if c.isascii() else "cn"


def _sprite(c: str, font) -> tuple[np.ndarray, int]:
    im = Image.new("L", (128, 128), 255)
    ImageDraw.Draw(im).text((64, 100), c, font=font, fill=18, anchor="ms")
    g = np.asarray(im.filter(ImageFilter.GaussianBlur(0.8)), np.float32) / 255.0
    ys, xs = np.nonzero(g < 0.82)
    crop = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return (np.clip(crop, 0, 1) * 255).astype(np.uint8), int(ys.min())


def _label_raster() -> tuple[Image.Image, tuple[int, int]]:
    need = int(math.ceil(LABEL_MIN_FRAC * min(W, H))) + 2
    size = int(1.1 * need)
    while True:
        f = cjk_font(size)
        bb = f.getbbox("AI生成 · Inko")
        if bb[3] - bb[1] >= need:
            break
        size += 4
    xy = (W - 150 - (bb[2] - bb[0]) - bb[0], H - 90 - (bb[3] - bb[1]) - bb[1])
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text(xy, "AI生成 · Inko", font=f, fill=255)
    box = mask.getbbox()
    rgba = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (128, 128, 128, 0))
    rgba.putalpha(mask.crop(box))
    return rgba, (box[0], box[1])


def _pack(sprites: list[np.ndarray]) -> tuple[np.ndarray, list[list[int]]]:
    gap, x, y, shelf, Wd = 4, 4, 4, 0, 1400
    rects = []
    for s in sprites:
        h, w = s.shape
        if x + w + gap > Wd:
            x, y, shelf = gap, y + shelf + gap, 0
        rects.append([x, y, w, h])
        x += w + gap
        shelf = max(shelf, h)
    atlas = np.full((y + shelf + gap, Wd), 255, np.uint8)
    for s, (x, y, w, h) in zip(sprites, rects):
        atlas[y:y + h, x:x + w] = s
    return atlas, rects


LINES = ["今天天气很好，我们一起去公园。", "Inko 2026 手写，第二行。", "第三行的字距要正常。", "4. 新的一题从这里开始。"]
OWN = ["姓名：张三"]


def make_package(folder: Path, label: bool = True, seed: int = 3) -> Path:
    """Synthetic single-page package: 4 text lines (one with word spaces) + a boxed name line; returns the folder."""
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "sprites").mkdir(parents=True)
    rng = np.random.default_rng(seed)
    font = cjk_font(92)
    glyphs, sprites, lines = [], [], []
    text = "\n".join(LINES)

    def place(ch, x, base, s, i, j, line, box):
        spr, top = _sprite(ch, font)
        gh, gw = spr.shape
        k = s / 92.0
        tw = gw * k
        w = s if unicodedata.east_asian_width(ch) in "FW" else tw + 0.14 * s
        left = x + (w - tw) / 2
        rot, jit = float(rng.uniform(-0.6, 0.6)), float(rng.uniform(-1.0, 1.0))
        r = math.radians(rot)
        R = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
        ax, ay = x, base + jit
        A = SC._T(ax, ay) @ R @ SC._T(-ax, -ay) @ SC._T(left, base + jit - (100 - top) * k) @ np.diag([k, k, 1.0])
        probe = np.ones((H, W), np.float32)
        ref_place(probe, spr, A[:2])
        ys, xs = np.nonzero(probe < 0.82)
        n = len(glyphs)
        glyphs.append({"id": f"p0g{n}", "c": ch, "i": i, "j": j, "kind": _kind(ch), "sprite": {"atlas": "sprites/page-1.png", "rect": None},
                       "m": [float(v) for v in A[:2].ravel()], "anchor": [round(ax, 3), round(ay, 3)], "size": round(s, 3),
                       "adv": [round(x, 3), round(x + w, 3)], "ink": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                       "line": line, "row": line, "box": box, "style": "59", "pen": None, "group": None, "layer": 0,
                       "rot": round(rot, 3), "jit": round(jit, 3)})
        sprites.append(spr)
        return w

    i = 0
    for li, line in enumerate(LINES):
        s, base, x = 7 * PPM, (40 + li * 11) * PPM, 24 * PPM
        ids = []
        for ch in line:
            if ch == " ":
                x += 0.32 * s
                i += 1
                continue
            ids.append(f"p0g{len(glyphs)}")
            x += place(ch, x, base, s, i, i + 1, li + 1, None) + 0.06 * s
            i += 1
        i += 1
        lines.append({"id": li + 1, "base": round(base, 3), "x0": 24 * PPM, "x1": x, "box": None, "rows": [li], "glyphs": ids})
    s, base, x = 5 * PPM, 24 * PPM, 130 * PPM
    ids = []
    for off, ch in enumerate(OWN[0]):
        ids.append(f"p0g{len(glyphs)}")
        x += place(ch, x, base, s, 10_000_000 + off, 10_000_000 + off + 1, 0, "b1") + 0.06 * s
    lines.insert(0, {"id": 0, "base": round(base, 3), "x0": 130 * PPM, "x1": x, "box": "b1", "rows": [9], "glyphs": ids})
    atlas, rects = _pack(sprites)
    for g, rc in zip(glyphs, rects):
        g["sprite"]["rect"] = rc
    Image.fromarray(atlas, "L").save(folder / "sprites" / "page-1.png")
    lab = {"image": None, "xy": None, "mode": "none"}
    if label:
        img, xy = _label_raster()
        img.save(folder / "label.png")
        lab = {"image": "label.png", "xy": [int(xy[0]), int(xy[1])], "mode": "visible"}
    doc = {"format": "inko-scene", "version": 1,
           "job": {"id": "synth-1", "model": "lyric-1", "style": {"ref": "59", "kind": "preset", "label": None}, "styles": {},
                   "created_at": "2026-09-28T00:00:00Z", "kind": "layout"},
           "text": text, "own_texts": OWN, "own_base": 10_000_000,
           "aigc": {"Label": "1", "ContentProducer": "Inko (inkotype.com)", "ContentPropagator": "inkotype.com", "ReservedCode2": ""},
           "px_per_mm": PPM, "pen_px_per_mm": 2480 / 210, "ink_threshold": 0.82, "natural": 0.4, "gray": True,
           "pen": dict(PEN.DEFAULT), "label": lab,
           "pages": [{"index": 0, "size": [W, H], "aigc_id": "synth-1-p1",
                      "paper": {"kind": "color", "rgb": [255, 254, 251], "id": "blank", "upload": False},
                      "layers": [{"key": "", "pen": dict(PEN.DEFAULT)}], "glyphs": glyphs, "lines": lines, "groups": []}]}
    (folder / "scene.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (folder / "README.txt").write_text("synthetic inko-scene package for tests\n", encoding="utf-8")
    return folder


def reference_page(folder: Path) -> Image.Image:
    """The page the server would deliver for the synthetic package (default pen), rendered without scene.py."""
    doc = json.loads((folder / "scene.json").read_text("utf-8"))
    page = doc["pages"][0]
    atlas = np.asarray(Image.open(folder / "sprites" / "page-1.png").convert("L"))
    ink = np.ones((H, W), np.float32)
    for g in page["glyphs"]:
        x, y, w, h = g["sprite"]["rect"]
        ref_place(ink, np.ascontiguousarray(atlas[y:y + h, x:x + w]), g["m"])
    region = np.asarray(Image.new("RGB", (W, H), tuple(page["paper"]["rgb"])), np.float32)
    img = Image.fromarray((region * ink[..., None]).round().clip(0, 255).astype(np.uint8), "RGB")
    if doc["label"]["mode"] != "none":
        lab = Image.open(folder / "label.png").convert("RGBA")
        img.paste(lab, tuple(doc["label"]["xy"]), lab)
    return img.convert("L")


def fresh(name: str, **kw) -> Path:
    return make_package(OUT / name, **kw)


def label_rows(img: Image.Image, xy, size) -> int:
    """Height (px) of the gray label text found in the label's box."""
    a = np.asarray(img.convert("RGB"), np.int16)[xy[1]:xy[1] + size[1], xy[0]:xy[0] + size[0]]
    gray = (a.max(-1) - a.min(-1) < 20) & (a.min(-1) < 200) & (a.min(-1) > 60)
    rows = np.flatnonzero(gray.sum(1) >= 2)
    return int(rows[-1] - rows[0] + 1) if len(rows) else 0


# ── the tests ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def worker_dir() -> Path | None:
    for c in (os.environ.get("INKOTYPE_WORKER"), ROOT.parent / "inkotype" / "worker"):
        if c and (Path(c) / "inko_worker" / "pen.py").exists():
            return Path(c)
    return None


def find_samples(arg: str | None) -> Path | None:
    for c in (arg, os.environ.get("INKO_SCENE_SAMPLES")):
        if c and Path(c).is_dir():
            return Path(c)
    wd = worker_dir()
    if wd is None or not (wd / "scene_sample.py").exists():
        return None
    dest = OUT / "worker_samples"
    zips = list(dest.glob("*/scene.zip"))
    newest_src = max(f.stat().st_mtime for f in (wd / "scene_sample.py", wd / "inko_worker" / "scene.py",
                                                  wd / "inko_worker" / "layout_compose.py") if f.exists())
    if zips and min(z.stat().st_mtime for z in zips) < newest_src:          # the worker changed: make the samples again
        shutil.rmtree(dest)
    if not any(dest.glob("*/scene.zip")):
        r = subprocess.run([sys.executable, str(wd / "scene_sample.py"), "--out", str(dest)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if r.returncode != 0 or not any(dest.glob("*/scene.zip")):
            INFO.append("worker scene_sample.py failed: " + r.stderr[-400:])
            return None
    return dest


def find_plain(arg: str | None) -> Path | None:
    for c in (arg, os.environ.get("INKO_SCENE_PLAIN_SAMPLES")):
        if c and Path(c).is_dir() and any(Path(c).glob("*/scene.zip")):
            return Path(c)
    return None


def page_file(case: Path, n: int) -> Path:
    """The server's page n of a sample: page-N.png (scene_sample.py) or server-N.png (test_scene_lyric.py --keep)."""
    for name in (f"page-{n}.png", f"server-{n}.png"):
        if (case / name).exists():
            return case / name
    raise AssertionError(f"{case}: no page-{n}.png / server-{n}.png")


def main() -> int:
    samples_arg = plain_arg = None
    if "--samples" in sys.argv:
        samples_arg = sys.argv[sys.argv.index("--samples") + 1]
    if "--plain-samples" in sys.argv:
        plain_arg = sys.argv[sys.argv.index("--plain-samples") + 1]
    OUT.mkdir(parents=True, exist_ok=True)

    @test("_pen: normalize / hash vectors / disk kernels (worker test_pen.py unit checks)")
    def _():
        p = PEN.normalize({"weight": "0.5", "ink": 7, "type": "pen", "color": ["x"]})
        assert p == {"weight": 0.5, "ink": 1.0, "type": "original", "color": "black"}, p
        assert PEN.normalize(None) == PEN.DEFAULT and PEN.normalize({"weight": float("nan"), "ink": True}) == PEN.DEFAULT
        assert PEN.normalize({"weight": 10 ** 400}) == PEN.DEFAULT
        assert PEN.is_default({"weight": -0.0, "type": "bogus"}) and not PEN.is_default({"color": "blue"})
        assert PEN.is_colored({"type": "pencil"}) and PEN.is_colored({"color": "blueblack"}) and not PEN.is_colored({"type": "gel"})
        i = np.array([0, 1, 7, 12345, 2 ** 20 + 3])[None, :]
        j = np.array([0, 1, 99, 54321])[:, None]
        got = PEN._hash(i, j)
        want = np.array([[(((a * 73856093) ^ (b * 19349663)) * 2654435761 % 2 ** 32) / 2 ** 32 for a in map(int, i[0])] for b in map(int, j[:, 0])])
        assert np.array_equal(got, want), (got, want)
        sizes = [sum(2 * PEN._half(r, dy) + 1 for dy in range(-r, r + 1)) for r in (1, 2, 3)]
        assert sizes == [5, 21, 37], sizes
        a = np.zeros((7, 7), np.float32)
        a[3, 3] = 1
        assert int(PEN._morph(a, 1, True).sum()) == 5 and int(PEN._morph(1 - a, 1, False).sum()) == 49 - 5
        n = PEN.noise((4, 5), 2480 / 210, 1.5, (100, 260))
        assert n.shape == (4, 5) and (n >= 0).all() and (n < 1).all()

    @test("_pen: identical to the worker's pen.py for every pen type x weight x ink (when the worker is next to this repo)")
    def _():
        wd = worker_dir()
        if wd is None:
            raise Skip("worker not found (set INKOTYPE_WORKER)")
        spec = importlib.util.spec_from_file_location("worker_pen", wd / "inko_worker" / "pen.py")
        wp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wp)
        rng = np.random.default_rng(1)
        g = np.clip(rng.random((90, 140)) * 1.6 - 0.3, 0, 1).astype(np.float32)
        g[20:40, 30:100] = 0.1
        n = 0
        for s in (2480 / 210, 1240 / 210, 20.0, 40.0):
            for t in PEN.TYPES:
                for w in (-1.0, -0.4, 0.0, 0.5, 1.0):
                    for ink in (-1.0, 0.0, 0.6):
                        pen = {"type": t, "weight": w, "ink": ink, "color": ("black", "blue", "blueblack")[n % 3]}
                        a1, a2 = PEN.apply(g, pen, s, (37, 260)), wp.apply(g, pen, s, (37, 260))
                        assert a1.dtype == a2.dtype == np.float32 and np.array_equal(a1, a2), (pen, s)
                        region = np.full((90, 140, 3), 240, np.float32)
                        assert np.array_equal(PEN.composite(region, a1, pen), wp.composite(region, a2, pen)), pen
                        n += 1
        INFO.append(f"_pen vs worker pen.py: {n} pens, all bit-identical")

    samples = find_samples(samples_arg)

    @test("render = the server's pages for the worker's sample packages (max diff <= 2, >= 99 % identical)")
    def _():
        if samples is None:
            raise Skip("no worker sample packages (pass --samples DIR, or keep ../inkotype/worker next to this repo)")
        cases = sorted(p.parent for p in samples.glob("*/scene.zip"))
        assert cases, f"no */scene.zip under {samples}"
        rep = []
        for case in cases:
            work = OUT / "parity" / case.name
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            shutil.copy(case / "scene.zip", work / "scene.zip")
            res = run("render", work / "scene.zip", "-o", work / "out")
            assert (work / "scene" / "scene.json").exists(), "scene.zip was not extracted to a sibling folder"
            for pg in res["pages"]:
                ref = Image.open(page_file(case, pg["page"]))
                got = Image.open(pg["file"])
                assert ref.mode == got.mode, (case.name, ref.mode, got.mode)
                mx, same = compare(np.asarray(ref), np.asarray(got))
                rep.append(f"{case.name} p{pg['page']}: max {mx}, identical {same * 100:.3f}%")
                assert mx <= 2 and same >= 0.99, rep[-1]
                meta = read_meta(got)["aigc"]
                refm = json.loads(ref.info["AIGC"])
                assert meta == refm, (meta, refm)                     # same AIGC fields as the server's page (not edited)
        INFO.append("parity vs server pages: " + "; ".join(rep))

    plain = find_plain(plain_arg)

    @test("render = plain (Lyric page) packages: same mode as the server, = the worker's own re-composition, close to the server")
    def _():
        if plain is None:
            raise Skip("no plain samples (pass --plain-samples DIR = the --keep folder of the worker's test_scene_lyric.py)")
        cases = sorted(p.parent for p in plain.glob("*/scene.zip"))
        assert cases, f"no */scene.zip under {plain}"
        rep = []
        for case in cases:
            work = OUT / "parity_plain" / case.name
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            shutil.copy(case / "scene.zip", work / "scene.zip")
            res = run("render", work / "scene.zip", "-o", work / "out")
            doc = json.loads((work / "scene" / "scene.json").read_text("utf-8"))
            assert doc["job"]["kind"] == "plain", doc["job"]
            for pg in res["pages"]:
                got = Image.open(pg["file"])
                srv = Image.open(page_file(case, pg["page"]))
                assert srv.mode == got.mode, (case.name, pg["page"], "server", srv.mode, "skill", got.mode)   # gray kept
                a, b = np.asarray(srv.convert("RGB"), np.int16), np.asarray(got.convert("RGB"), np.int16)
                mean = float(np.abs(a - b).mean())
                line = f"{case.name} p{pg['page']}: vs server max {int(np.abs(a - b).max())}, mean {mean:.3f}"
                wref = case / f"scene-{pg['page']}.png"                   # the worker's re-composition of the same package
                if wref.exists():
                    mx, same = compare(np.asarray(Image.open(wref)), np.asarray(got))
                    line += f"; vs worker re-composition max {mx}, identical {same * 100:.3f}%"
                    assert mx <= 2 and same >= 0.99, line
                rep.append(line)
                # the server resized the whole page once (LANCZOS): per-glyph placement is an approximation, but nothing
                # may be misplaced (a shifted glyph would push the mean far above this)
                assert mean < 1.0, line
                meta = read_meta(got)["aigc"]
                if "AIGC" in srv.info:                                    # the --keep copies may carry no metadata
                    assert meta == json.loads(srv.info["AIGC"]), case.name
                assert meta["ProduceID"] == doc["pages"][pg["page"] - 1]["aigc_id"] and "edited" not in meta, meta
        INFO.append("plain packages: " + "; ".join(rep))

    syn = fresh("synthetic")

    @test("render(synthetic package) = an independent numpy renderer of the same package (max diff <= 2, >= 99 %)")
    def _():
        res = run("render", syn, "-o", OUT / "synthetic_out")
        got = Image.open(res["pages"][0]["file"])
        ref = reference_page(syn)
        assert got.mode == "L", got.mode
        mx, same = compare(np.asarray(ref), np.asarray(got))
        INFO.append(f"synthetic vs numpy reference: max {mx}, identical {same * 100:.4f}%")
        assert mx <= 2 and same >= 0.99, (mx, same)
        assert res["edited"] is False and res["pages"][0]["label"]["mode"] == "package"
        again = run("render", syn, "-o", OUT / "synthetic_out2")                  # same package -> same bytes
        assert Path(again["pages"][0]["file"]).read_bytes() == Path(res["pages"][0]["file"]).read_bytes(), "render is not deterministic"

    @test("label >= 5 % and AIGC metadata on PNG / JPG / PDF; AIGC = the TC260 fields only; InkoEdited only with edits")
    def _():
        pkg = fresh("label")
        doc = json.loads((pkg / "scene.json").read_text("utf-8"))
        lab = Image.open(pkg / "label.png")
        need = LABEL_MIN_FRAC * min(W, H)
        assert text_height(lab.convert("RGBA")) >= need
        tc260 = {"Label", "ContentProducer", "ProduceID", "ReservedCode1", "ContentPropagator", "PropagateID", "ReservedCode2"}
        res = run("render", pkg, "-o", OUT / "label_png", "--pdf")
        img = Image.open(res["pages"][0]["file"])
        assert label_rows(img, doc["label"]["xy"], lab.size) >= need * 0.98
        m = read_meta(img)["aigc"]
        assert set(m) == tc260 and m["ProduceID"] == "synth-1-p1" and m["Label"] == "1", m
        assert m["ReservedCode1"] == hashlib.sha256(b"Inko (inkotype.com)|synth-1-p1").hexdigest()
        assert "InkoEdited" not in img.text
        pdf = Path(res["pdf"]).read_bytes()
        assert b"/AIGC" in pdf and b"/Metadata" in pdf and b"InkoEdited" not in pdf
        run("move", pkg, "--ids", "p0g1", "--dx", "0.5")
        res = run("render", pkg, "-o", OUT / "label_png2", "--pdf")                  # edited PNG: separate marker chunk
        img = Image.open(res["pages"][0]["file"])
        m = read_meta(img)["aigc"]
        assert set(m) == tc260 and m["ProduceID"] == "synth-1-p1", m                  # AIGC unchanged by edits
        assert img.info.get("InkoEdited") == "true" and img.text.get("InkoEdited") == "true", img.info.keys()
        img.load()                                                                    # the inserted chunk leaves the PNG valid
        assert label_rows(img, doc["label"]["xy"], lab.size) >= need * 0.98
        pdf = Path(res["pdf"]).read_bytes()
        assert b"/InkoEdited (true)" in pdf and b'"edited"' not in pdf, "PDF: /InkoEdited in Info, nothing added to /AIGC"
        res = run("render", pkg, "-o", OUT / "label_jpg", "--format", "jpg")
        img = Image.open(res["pages"][0]["file"])
        m = read_meta(img)["aigc"]
        assert img.format == "JPEG" and set(m) == tc260 and m["ProduceID"] == "synth-1-p1", m
        img.load()                                                                    # the inserted comment leaves the JPEG valid
        assert b"\xff\xfe\x00\x11InkoEdited=true" in Path(res["pages"][0]["file"]).read_bytes(), "JPEG: COM segment InkoEdited=true"
        assert label_rows(img, doc["label"]["xy"], lab.size) >= need * 0.98
        # label.png missing -> re-applied by the skill's rule, still >= 5 %
        (pkg / "label.png").unlink()
        res = run("render", pkg, "-o", OUT / "label_fallback")
        assert res["pages"][0]["label"]["mode"] == "reapplied"
        img = Image.open(res["pages"][0]["file"])
        from _common import find_label_bbox
        bb = find_label_bbox(np.asarray(img.convert("RGB")))
        assert bb is not None and (bb[3] - bb[1]) >= need, bb
        # a job generated without a visible label stays without one
        nol = fresh("nolabel", label=False)
        res = run("render", nol, "-o", OUT / "nolabel_out")
        assert res["pages"][0]["label"]["mode"] == "none"
        assert read_meta(Image.open(res["pages"][0]["file"]))["aigc"]["ProduceID"] == "synth-1-p1"

    def state(pkg: Path) -> SC.State:
        p = SC.open_package(pkg)
        return SC.State(p, p.read_edits())

    def render_arr(pkg: Path, tag: str) -> np.ndarray:
        """The rendered page as 8-bit gray (a blank page with black ink is saved gray; a coloured pen makes it RGB)."""
        res = run("render", pkg, "-o", OUT / tag)
        return np.asarray(Image.open(res["pages"][0]["file"]).convert("L")).astype(np.int16)

    def union(a, b):
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    def changed_box(a: np.ndarray, b: np.ndarray):
        ys, xs = np.nonzero(a != b)
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else None

    def inside(box, outer, pad=3):
        return box is None or (box[0] >= outer[0] - pad and box[1] >= outer[1] - pad and box[2] <= outer[2] + pad and box[3] <= outer[3] + pad)

    @test("atomic ops: move / scale / rotate / pen / hide / show / note change exactly what they should")
    def _():
        pkg = fresh("ops")
        sj = (pkg / "scene.json").read_bytes()
        base = render_arr(pkg, "ops_0")
        st0 = state(pkg)
        g0 = {k: (g.ink.copy(), g.anchor.copy(), g.size, g.rot) for k, g in st0.glyphs.items()}
        # move: only p0g3 moves, by exactly dx mm; pixels change only around its old + new place
        run("move", pkg, "--ids", "p0g3", "--dx", "1.5", "--dy", "-0.5")
        st = state(pkg)
        d = np.array([1.5, -0.5]) * PPM
        assert np.allclose(st.glyphs["p0g3"].ink, g0["p0g3"][0] + np.r_[d, d])
        assert np.allclose(st.glyphs["p0g3"].anchor, g0["p0g3"][1] + d)
        assert all(np.array_equal(g.ink, g0[k][0]) for k, g in st.glyphs.items() if k != "p0g3")
        after = render_arr(pkg, "ops_move")
        o, n = g0["p0g3"][0], st.glyphs["p0g3"].ink
        assert inside(changed_box(base, after), (min(o[0], n[0]), min(o[1], n[1]), max(o[2], n[2]), max(o[3], n[3])))
        run("undo", pkg)
        assert np.array_equal(render_arr(pkg, "ops_undo"), base), "undo did not restore the page"
        # scale about the anchor: size x k, anchor fixed
        run("scale", pkg, "--ids", "p0g5", "--k", "1.25")
        st = state(pkg)
        g = st.glyphs["p0g5"]
        assert abs(g.size - g0["p0g5"][2] * 1.25) < 1e-6 and np.allclose(g.anchor, g0["p0g5"][1])
        run("undo", pkg)
        # rotate: angle + deg, anchor fixed
        run("rotate", pkg, "--ids", "p0g6", "--deg", "-3")
        g = state(pkg).glyphs["p0g6"]
        assert abs(g.rot - (g0["p0g6"][3] - 3)) < 1e-6 and np.allclose(g.anchor, g0["p0g6"][1])
        after = render_arr(pkg, "ops_rot")
        assert inside(changed_box(base, after), union(g.ink, g0["p0g6"][0]), pad=4)
        run("undo", pkg)
        # pen: a blue gel layer for one line; the rest of the page stays as it was
        line2 = state(pkg).lines[0][2]["glyphs"]
        run("restyle", pkg, "--ids", ",".join(line2), "--type", "gel", "--color", "blue", "--weight", "0.4")
        st = state(pkg)
        pens = [(pen, len(gl)) for pen, gl in st.layers(0)]
        assert len(pens) == 2 and pens[1][0] == {"weight": 0.4, "ink": 0.0, "type": "gel", "color": "blue"} and pens[1][1] == len(line2), pens
        res = run("render", pkg, "-o", OUT / "ops_pen")
        img = Image.open(res["pages"][0]["file"])
        assert img.mode == "RGB", "a coloured pen cannot be saved as grayscale"
        boxes = np.array([st.glyphs[i].ink for i in line2])
        ub = (boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max())
        assert inside(changed_box(base, np.asarray(img.convert("L")).astype(np.int16)), ub, pad=4)
        rgb = np.asarray(img).astype(np.int16)
        blue = rgb[..., 2] - rgb[..., 0]
        assert blue.max() > 60
        run("undo", pkg)
        # hide / show
        run("hide", pkg, "--ids", "p0g7")
        st = state(pkg)
        assert st.glyphs["p0g7"].hidden and sum(g.hidden for g in st.pages[0]) == 1
        after = render_arr(pkg, "ops_hide")
        k = g0["p0g7"][0]
        assert inside(changed_box(base, after), k, pad=2)
        x0, y0, x1, y1 = (int(v) for v in k)
        assert after[y0:y1, x0:x1].min() >= 250, "hidden glyph still drawn"
        run("show", pkg, "--ids", "p0g7")
        assert np.array_equal(render_arr(pkg, "ops_show"), base)
        # note: recorded, changes nothing, does not mark the page as edited
        run("reset", pkg)
        run("note", pkg, "--text", "hello")
        assert SC.open_package(pkg).read_edits()[-1]["op"] == "note"
        res = run("render", pkg, "-o", OUT / "ops_note")
        assert res["edited"] is False and np.array_equal(np.asarray(Image.open(res["pages"][0]["file"]).convert("L")).astype(np.int16), base)
        assert (pkg / "scene.json").read_bytes() == sj, "scene.json was modified"

    @test("tighten: an artificially widened gap goes back to a natural one; word spaces are untouched")
    def _():
        pkg = fresh("tighten")
        st = state(pkg)
        line = st.lines[0][1]                                   # 今天天气很好，…
        gl = st.line_glyphs(0, line)
        k = 4                                                   # widen the gap between glyph 3 and 4 by 4 mm
        run("move", pkg, "--ids", ",".join(g.id for g in gl[k:]), "--dx", "4")
        ins = run("inspect", pkg)
        gaps = [a for a in ins["anomalies"] if a["type"] == "gap"]
        assert any(a["after"] == gl[k - 1].id and a["before"] == gl[k].id for a in gaps), gaps
        before = state(pkg)
        wide = float(before.glyphs[gl[k].id].ink[0] - before.glyphs[gl[k - 1].id].ink[2])
        # the Latin line has word spaces ("Inko 2026 手写"): remember those gaps
        lat = before.line_glyphs(0, before.lines[0][2])
        sp = {(a.id, b.id): float(b.ink[0] - a.ink[2]) for a, b in zip(lat, lat[1:]) if SC.is_space(before.doc, a, b)}
        assert len(sp) == 2, sp
        res = run("tighten", pkg, "--line", "all", "--max-gap", "0.35")
        assert res["added"] >= 2
        after = state(pkg)
        a, b = after.glyphs[gl[k - 1].id], after.glyphs[gl[k].id]
        gap = float(b.ink[0] - a.ink[2])
        s = (a.size + b.size) / 2
        assert gap < wide - 3 * PPM and 0 <= gap <= 0.35 * s, (wide / PPM, gap / PPM)
        for (ia, ib), g in sp.items():
            now = float(after.glyphs[ib].ink[0] - after.glyphs[ia].ink[2])
            assert abs(now - g) < 1e-6, ("word space changed", ia, ib, g, now)
        order = [after.glyphs[g.id].ink[0] for g in gl]
        assert order == sorted(order), "tighten changed the reading order"
        assert not [x for x in run("inspect", pkg)["anomalies"] if x["type"] == "gap"]
        again = run("tighten", pkg)
        assert again.get("added") == 0, again

    @test("drift: line starts differ, stay within bounds (<= 0.45 size, on the page), boxed line untouched, no double drift")
    def _():
        pkg = fresh("drift")
        st0 = state(pkg)
        box_ids = [g.id for g in st0.pages[0] if g.data.get("box")]
        run("drift", pkg, "--mode", "natural", "--seed", "11")
        st = state(pkg)
        starts, shifts = [], []
        for ln in st.lines[0]:
            if ln.get("box"):
                continue
            gl = st.line_glyphs(0, ln)
            x0 = min(g.ink[0] for g in gl)
            x00 = min(st0.glyphs[g.id].ink[0] for g in gl)
            starts.append(round(x0, 2))
            shifts.append(x0 - x00)
            s = float(np.median([g.size for g in gl]))
            assert -0.06 * s - 1e-6 <= x0 - x00 <= 0.45 * s + 1e-6, (ln["id"], (x0 - x00) / s)
            assert x0 >= 5 * PPM and max(g.ink[2] for g in gl) <= W - 5 * PPM
        assert len(set(starts)) == len(starts), f"line starts still aligned: {starts}"
        assert max(shifts) > 0.3 * PPM, shifts
        assert all(np.array_equal(st.glyphs[i].ink, st0.glyphs[i].ink) for i in box_ids), "boxed glyphs moved"
        again = run("drift", pkg, ok=False)
        assert again["_code"] != 0 and "already" in again["_stderr"]
        rnd = fresh("drift_random")
        run("drift", rnd, "--mode", "random", "--seed", "5")
        st = state(rnd)
        assert any(st.glyphs[g.id].edited for g in st.pages[0]), "random drift moved nothing"
        undo = run("undo", pkg)
        assert undo["edits"] == 0 and not any(g.edited for g in state(pkg).pages[0])

    @test("gap / align-baseline / undo --n / reset / dry-run")
    def _():
        pkg = fresh("misc")
        st = state(pkg)
        a, b = "p0g0", "p0g1"
        run("gap", pkg, "--a", a, "--b", b, "--mm", "2")
        st = state(pkg)
        assert abs((st.glyphs[b].ink[0] - st.glyphs[a].ink[2]) / PPM - 2) < 0.01          # ops keep 1e-4 mm
        line = st.line_glyphs(0, st.line_of(st.glyphs[b]))
        assert st.glyphs[line[-1].id].edited, "the rest of the line did not follow"
        run("align-baseline", pkg, "--line", "p0l1")
        st = state(pkg)
        ys = [g.anchor[1] for g in st.line_glyphs(0, st.lines[0][1])]
        assert max(ys) - min(ys) < 0.5, ys
        n0 = len(SC.open_package(pkg).read_edits())
        dry = run("tighten", pkg, "--line", "all", "--max-gap", "0.05", "--dry-run")
        assert dry["dry_run"] and len(SC.open_package(pkg).read_edits()) == n0
        run("move", pkg, "--ids", "p0g2", "--dx", "1")
        run("move", pkg, "--ids", "p0g2", "--dx", "1")
        r = run("undo", pkg, "--n", "2")
        assert r["edits"] == n0, r
        r = run("reset", pkg)
        assert r["edits"] == 0 and Path(r["backup"]).exists()

    def edit_doc(pkg: Path, fn) -> None:
        doc = json.loads((pkg / "scene.json").read_text("utf-8"))
        fn(doc)
        (pkg / "scene.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def two_pages(doc: dict) -> None:
        """Page 2 = a copy of page 1 (same atlas), ids p1g…, like a two-page job."""
        p2 = json.loads(json.dumps(doc["pages"][0]).replace('"p0g', '"p1g'))
        p2["index"], p2["aigc_id"] = 1, "synth-1-p2"
        doc["pages"].append(p2)

    @test("gray / RGB = the server's job-wide decision (scene.json gray): white paper kept gray, a coloured pen anywhere -> RGB")
    def _():
        pkg = fresh("gray")

        def white(doc):
            doc["pages"][0]["paper"]["id"] = "white"                  # a plain (Lyric page) job on white paper
            two_pages(doc)
        edit_doc(pkg, white)
        res = run("render", pkg, "-o", OUT / "gray_0")
        assert [pg["mode"] for pg in res["pages"]] == ["L", "L"], res["pages"]
        run("restyle", pkg, "--ids", "p0g0", "--color", "blue")          # one glyph on page 1 turns blue
        res = run("render", pkg, "-o", OUT / "gray_1")
        assert [Image.open(pg["file"]).mode for pg in res["pages"]] == ["RGB", "RGB"], "a coloured pen must make every page RGB"
        run("undo", pkg)
        edit_doc(pkg, lambda doc: doc.update(gray=False))                 # the server saved RGB (e.g. ruled paper)
        res = run("render", pkg, "-o", OUT / "gray_2")
        assert [pg["mode"] for pg in res["pages"]] == ["RGB", "RGB"], res["pages"]

    @test("restyle changes only the given fields of each glyph's own pen (a blue gel line made bolder stays blue gel)")
    def _():
        pkg = fresh("restyle")
        gel = {"weight": 0.3, "ink": 0.2, "type": "gel", "color": "blue"}

        def layers(doc):
            page = doc["pages"][0]
            page["layers"].append({"key": "gel|blue|0.3|0.2", "pen": gel})
            ids = set(page["lines"][2]["glyphs"])
            for g in page["glyphs"]:
                if g["id"] in ids:
                    g["layer"], g["pen"] = 1, gel
        edit_doc(pkg, layers)
        st = state(pkg)
        line = [g.id for g in st.line_glyphs(0, st.lines[0][2])]
        run("restyle", pkg, "--line", "p0l2", "--weight", "0.6")
        st = state(pkg)
        assert all(st.pen_of(st.glyphs[i]) == {**gel, "weight": 0.6} for i in line), st.pen_of(st.glyphs[line[0]])
        res = run("restyle", pkg, "--ids", "all", "--ink", "0.5")
        assert len(res["ops"]) == 2, res["ops"]
        st = state(pkg)
        assert st.pen_of(st.glyphs[line[0]]) == {**gel, "weight": 0.6, "ink": 0.5}
        assert st.pen_of(st.glyphs["p0g0"]) == {**PEN.DEFAULT, "ink": 0.5}
        assert run("restyle", pkg, "--ids", "p0g0", ok=False)["_code"] != 0, "restyle without any field must refuse"
        run("restyle", pkg, "--ids", ",".join(line), "--reset")
        st = state(pkg)
        assert st.pen_of(st.glyphs[line[0]]) == gel and any(pen == gel and len(g) == len(line) for pen, g in st.layers(0))

    @test("grid cells (稿纸 / 方格): tighten and drift leave glyphs placed in cells where they are")
    def _():
        pkg = fresh("grid")

        def cells(doc):                                               # line 3: anchored at the centre of its cell (plan cx)
            page = doc["pages"][0]
            ids = set(page["lines"][3]["glyphs"])
            for g in page["glyphs"]:
                if g["id"] in ids:
                    g["anchor"][0] = round((g["adv"][0] + g["adv"][1]) / 2, 3)
        edit_doc(pkg, cells)
        st = state(pkg)
        gl = st.line_glyphs(0, st.lines[0][3])
        assert all(SC.is_cell(g) for g in gl) and not any(SC.is_cell(g) for g in st.line_glyphs(0, st.lines[0][1]))
        run("move", pkg, "--ids", ",".join(g.id for g in gl[4:]), "--dx", "4")
        ins = run("inspect", pkg)
        assert not [a for a in ins["anomalies"] if a["type"] == "gap" and a["line"] == "p0l3"], ins["anomalies"]
        assert [ln["grid"] for ln in ins["lines"] if ln["ref"] == "p0l3"] == [True]
        before = {g.id: g.ink.copy() for g in state(pkg).pages[0]}
        run("tighten", pkg, "--line", "all", "--max-gap", "0.05")
        run("drift", pkg, "--seed", "3")
        st = state(pkg)
        assert all(np.array_equal(st.glyphs[g.id].ink, before[g.id]) for g in gl), "a glyph left its grid cell"
        assert any(not np.array_equal(st.glyphs[g.id].ink, before[g.id]) for g in st.line_glyphs(0, st.lines[0][1]))

    @test("grid cells: an explicit glyph \"cell\" (written by the worker) wins over the anchor-centre inference")
    def _():
        pkg = fresh("gridflag")

        def flags(doc):                                               # line 2: flagged, anchors left as running text;
            page = doc["pages"][0]                                    # line 3: centred anchors but flagged "cell": false
            l2, l3 = set(page["lines"][2]["glyphs"]), set(page["lines"][3]["glyphs"])
            for g in page["glyphs"]:
                if g["id"] in l2:
                    g["cell"] = True
                elif g["id"] in l3:
                    g["anchor"][0] = round((g["adv"][0] + g["adv"][1]) / 2, 3)
                    g["cell"] = False
        edit_doc(pkg, flags)
        st = state(pkg)
        l2, l3 = st.line_glyphs(0, st.lines[0][2]), st.line_glyphs(0, st.lines[0][3])
        assert all(SC.is_cell(g) for g in l2) and not any(SC.is_cell(g) for g in l3)
        before = {g.id: g.ink.copy() for g in st.pages[0]}
        run("move", pkg, "--ids", ",".join(g.id for g in l2[3:] + l3[3:]), "--dx", "4")
        run("tighten", pkg, "--line", "all")
        run("drift", pkg, "--seed", "3")
        st = state(pkg)
        d4 = np.array([4 * st.pkg.ppm, 0, 4 * st.pkg.ppm, 0])
        assert all(np.allclose(st.glyphs[g.id].ink, before[g.id] + (d4 if k >= 3 else 0)) for k, g in enumerate(l2)), \
            "tighten / drift moved a glyph flagged \"cell\": true"
        assert any(not np.allclose(st.glyphs[g.id].ink, before[g.id] + (d4 if k >= 3 else 0)) for k, g in enumerate(l3)), \
            "\"cell\": false was ignored (the line was neither tightened nor drifted)"

    @test("tighten keeps the gap inside a group (formula / joined glyphs) however wide it is")
    def _():
        pkg = fresh("group")

        def group(doc):
            for g in doc["pages"][0]["glyphs"]:
                if g["id"] in doc["pages"][0]["lines"][1]["glyphs"][2:4]:
                    g["group"] = "f1"
        edit_doc(pkg, group)
        st = state(pkg)
        gl = st.line_glyphs(0, st.lines[0][1])
        run("move", pkg, "--ids", ",".join(g.id for g in gl[3:]), "--dx", "4")       # widen the gap inside the group
        run("move", pkg, "--ids", ",".join(g.id for g in gl[6:]), "--dx", "4")       # and a normal gap
        st = state(pkg)
        inside_gap = float(st.glyphs[gl[3].id].ink[0] - st.glyphs[gl[2].id].ink[2])
        run("tighten", pkg, "--line", "p0l1")
        st = state(pkg)
        assert abs(float(st.glyphs[gl[3].id].ink[0] - st.glyphs[gl[2].id].ink[2]) - inside_gap) < 1e-6, "group gap changed"
        assert float(st.glyphs[gl[6].id].ink[0] - st.glyphs[gl[5].id].ink[2]) < 0.35 * gl[6].size, "normal gap not tightened"

    @test("inspect: gaps, stroke outliers, overlaps, off-paper, hidden are reported where they are")
    def _():
        pkg = fresh("inspect")
        ins = run("inspect", pkg)
        assert ins["pages"][0]["glyphs"] == len(json.loads((pkg / "scene.json").read_text("utf-8"))["pages"][0]["glyphs"])
        assert {ln["ref"] for ln in ins["lines"]} >= {"p0l0", "p0l1", "p0l2", "p0l3", "p0l4"}
        assert ins["lines"][2]["text"].startswith("Inko 2026"), ins["lines"][2]
        base = {a["type"] for a in ins["anomalies"]}
        assert not base & {"off_paper", "overlap", "hidden"}, ins["anomalies"]
        run("scale", pkg, "--ids", "p0g9", "--k", "1.7", "--about", "center")      # bolder + bigger
        run("move", pkg, "--ids", "p0g12", "--dx", "-5")                              # onto its neighbour
        run("move", pkg, "--ids", "p0g20", "--dx", "400")                             # off the paper
        run("hide", pkg, "--ids", "p0g21")
        ins = run("inspect", pkg)
        got = {(a["type"], a.get("id") or tuple(a.get("ids") or ())) for a in ins["anomalies"]}
        types = {t for t, _ in got}
        assert ("stroke", "p0g9") in got, ins["anomalies"]
        assert any(t == "overlap" and "p0g12" in ids for t, ids in got if isinstance(ids, tuple))
        assert ("off_paper", "p0g20") in got and "hidden" in types
        assert ins["edits"]["count"] == 4 and ins["hints"]

    @test("zip: extracted once to a sibling folder, unexpected / unsafe members are not written")
    def _():
        src = fresh("zipsrc")
        zdir = OUT / "zipcase"
        if zdir.exists():
            shutil.rmtree(zdir)
        zdir.mkdir(parents=True)
        zp = zdir / "scene.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(src).as_posix())
            z.writestr("../evil.txt", "x")
            z.writestr("sprites/../../evil2.txt", "x")
        r = run("inspect", zp)
        assert (zdir / "scene" / "scene.json").exists() and r["pages"][0]["glyphs"] > 10
        assert not (zdir / "evil.txt").exists() and not (OUT / "evil2.txt").exists() and not (zdir.parent / "evil.txt").exists()
        run("move", zp, "--ids", "p0g1", "--dx", "1")
        assert (zdir / "scene" / "edits.json").exists()
        r = run("inspect", zp)                                   # second use: same folder, edits kept
        assert r["edits"]["count"] == 1

    @test("editor: 127.0.0.1 only, serves the page + package, accepts a PUT of edits (token + host checked)")
    def _():
        pkg = fresh("editor")
        srv, _url = SC.make_server(SC.open_package(pkg))                       # the socket itself is bound to loopback
        try:
            assert srv.server_address[0] == "127.0.0.1", srv.server_address
        finally:
            srv.server_close()
        proc = subprocess.Popen([sys.executable, str(SCENE), "editor", str(pkg), "--no-browser"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        box: list[str] = []
        t = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
        t.start()
        t.join(20)
        try:
            assert box and box[0].strip(), "editor printed nothing"
            info = json.loads(box[0])
            url = info["url"]
            assert url.startswith("http://127.0.0.1:"), url
            assert "保存" in info["hint"] and "AI" in info["hint"], info                  # the one line a person may read
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def req(method, path, body=None, headers=None):
                r = urllib.request.Request(url.rstrip("/") + path, method=method, headers=headers or {},
                                           data=None if body is None else json.dumps(body).encode("utf-8"))
                try:
                    with opener.open(r, timeout=20) as resp:
                        return resp.status, resp.read()
                except urllib.error.HTTPError as e:
                    return e.code, e.read()
            code, html = req("GET", "/")
            html = html.decode("utf-8")
            assert code == 200 and "Inko 笔迹编辑器" in html and 'lang="zh-CN"' in html and "<canvas" in html
            assert "cdn" not in html.lower() and "https://" not in html, "the editor must work offline"
            token = re.search(r'const TOKEN = "([^"]+)"', html).group(1)
            assert token != "__INKO_TOKEN__"
            code, body = req("GET", "/pkg/scene.json")
            assert code == 200 and json.loads(body)["format"] == "inko-scene"
            code, body = req("GET", "/pkg/sprites/page-1.png")
            assert code == 200 and body[:4] == b"\x89PNG"
            assert req("GET", "/pkg/../scene.json")[0] == 404 and req("GET", "/pkg/edits.json")[0] == 404
            code, body = req("GET", "/api/edits")
            rev = json.loads(body)["rev"]
            ops = [{"op": "move", "ids": ["p0g1"], "dx": 0.7, "dy": 0.1, "batch": 1, "src": "editor"},
                   {"op": "pen", "ids": "all", "pen": {"type": "gel", "color": "blue", "weight": 0.2, "ink": 0}, "batch": 2}]
            hdr = {"Content-Type": "application/json", "X-Inko-Token": token}
            cjk = re.compile(r"[㐀-鿿]")

            def zh_err(res, code, err):                                            # what the page shows is Chinese
                body = json.loads(res[1])
                assert res[0] == code and body.get("code") == err and cjk.search(body.get("error", "")), (res, err)
            zh_err(req("PUT", "/api/edits", {"edits": ops, "rev": rev}, {"Content-Type": "application/json"}), 403, "bad_token")
            zh_err(req("PUT", "/api/edits", {"edits": ops, "rev": rev}, {**hdr, "Host": f"evil.example:{url.rsplit(':', 1)[1].strip('/')}"}),
                   403, "bad_host")
            zh_err(req("PUT", "/api/edits", {"edits": [{"op": "explode", "ids": "all"}], "rev": rev}, hdr), 400, "bad_edits")
            code, body = req("PUT", "/api/edits", {"edits": ops, "rev": rev}, hdr)
            assert code == 200 and json.loads(body)["count"] == 2, body
            saved = json.loads((pkg / "edits.json").read_text("utf-8"))["edits"]
            assert [o["op"] for o in saved] == ["move", "pen"] and saved[0]["dx"] == 0.7
            zh_err(req("PUT", "/api/edits", {"edits": [], "rev": rev}, hdr), 409, "conflict")        # stale rev refused
            code, body = req("POST", "/api/compute", {"edits": saved, "cmd": "drift", "params": {"mode": "natural"}}, hdr)
            assert code == 200 and any(o["op"] == "move" for o in json.loads(body)["ops"])
            code, body = req("POST", "/api/inspect", {"edits": saved}, hdr)
            assert code == 200 and json.loads(body)["pages"][0]["layers"][0]["pen"]["color"] == "blue"
            zh_err(req("POST", "/api/compute", {"edits": saved, "cmd": "drift", "params": "x"}, hdr), 400, "bad_request")
            zh_err(req("POST", "/api/compute", {"edits": saved, "cmd": "tighten", "params": {"lines": "p9l9"}}, hdr), 400, "failed")
            zh_err(req("GET", "/pkg/scene.json%0A"), 404, "not_found")
            good = (pkg / "edits.json").read_bytes()                                 # edits.json broken on disk (a bad hand
            (pkg / "edits.json").write_text('{"edits": [ {"op": "move", }', encoding="utf-8")   # edit): an answer the page
            try:                                                                     # can show, not a dropped connection
                zh_err(req("GET", "/api/edits"), 500, "failed")
                zh_err(req("POST", "/api/render", {"pdf": False}, hdr), 400, "failed")
            finally:
                (pkg / "edits.json").write_bytes(good)
            assert req("GET", "/")[0] == 200, "the server stopped answering after bad requests"
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ── report ──
    width = max(len(n) for n, _, _ in RESULTS)
    for name, status, detail in RESULTS:
        print(f"{status:4s}  {name.ljust(width)}")
        if status != "PASS" and detail:
            print("      " + detail.strip().replace("\n", "\n      ")[:2500])
    for line in INFO:
        print("info: " + line)
    fails = sum(s == "FAIL" for _, s, _ in RESULTS)
    skips = sum(s == "SKIP" for _, s, _ in RESULTS)
    print(f"\n{len(RESULTS) - fails - skips} passed, {fails} failed, {skips} skipped   (outputs in {OUT})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
