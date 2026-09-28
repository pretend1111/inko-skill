#!/usr/bin/env python3
"""Images -> one PDF at real paper size, keeping Inko's AI-generation labels.

    python pdf.py page-1.png page-2.png -o homework.pdf
    python pdf.py final.jpg final-2.jpg -o notes.pdf --size B5 --margin 0
    python pdf.py page.jpg -o pages.pdf --size auto --jpeg 85

Page size: "auto" (default) uses the image's dpi (Inko pages: A4 at 300 dpi) and falls back to A4 in the image's
orientation; or A4 / A5 / B5 / Letter / 16K … / WxH in mm. Images are fitted inside the page (never cropped, so the
visible label 「AI生成 · Inko」 stays whole). The implicit label goes into the PDF's Info (/AIGC) and XMP metadata.
Inko's own `inko.pdf` (from `inko.py generate`) is already a finished PDF — use this for edited flat-page images.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True        # run from the skill folder without leaving __pycache__ in it

import argparse
import io
import json
import time
import uuid
import zlib
from pathlib import Path

from _common import PRODUCER, SKILL_TAG, Image, die, emit, open_image, parse_size_mm, xmp_packet

PT_PER_MM = 72 / 25.4


def _pdf_str(s: str) -> bytes:
    """PDF text string: plain ASCII in (), anything else as UTF-16BE hex with BOM."""
    if all(32 <= ord(c) < 127 for c in s):
        return b"(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii") + b")"
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def encode_image(img: Image.Image, jpeg_q: int, lossless: bool) -> tuple[bytes, dict]:
    gray = img.mode in ("L", "LA", "1") or (img.mode == "RGB" and _is_gray(img))
    im = img.convert("L" if gray else "RGB")
    if img.mode in ("RGBA", "LA", "P"):                                  # flatten transparency onto white
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").getchannel("A"))
        im = bg.convert("L" if gray else "RGB")
    cs = b"/DeviceGray" if gray else b"/DeviceRGB"
    if lossless:
        data = zlib.compress(im.tobytes(), 6)
        return data, {"filter": b"/FlateDecode", "cs": cs, "w": im.width, "h": im.height}
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=jpeg_q, subsampling=0 if jpeg_q >= 90 else 2, optimize=True)
    return buf.getvalue(), {"filter": b"/DCTDecode", "cs": cs, "w": im.width, "h": im.height}


def _is_gray(img: Image.Image) -> bool:
    small = img.convert("RGB").resize((min(256, img.width), min(256, img.height)))
    px = small.getdata()
    return all(max(p) - min(p) <= 3 for p in px)


def page_size_pt(img: Image.Image, meta: dict, size: str) -> tuple[float, float]:
    if size.lower() == "auto":
        dpi = meta.get("dpi") or img.info.get("dpi")
        if dpi and float(dpi[0]) > 50:
            w, h = img.width / float(dpi[0]) * 72, img.height / float(dpi[1] if len(dpi) > 1 else dpi[0]) * 72
            if 60 * PT_PER_MM <= max(w, h) <= 450 * PT_PER_MM:          # a real sheet size (not a 72-dpi camera default)
                return w, h
        w, h = 210.0, 297.0
        if img.width > img.height:
            w, h = h, w
        return w * PT_PER_MM, h * PT_PER_MM
    w, h = parse_size_mm(size)
    if (img.width > img.height) != (w > h):                              # follow the image's orientation
        w, h = h, w
    return w * PT_PER_MM, h * PT_PER_MM


def write_pdf(pages: list[tuple[bytes, dict, tuple[float, float], tuple[float, float, float, float]]], out: Path, info: dict, xmp: str | None) -> None:
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)
    cat_id = add(b"")                                                    # placeholders, filled below
    pages_id = add(b"")
    kids = []
    for data, im, (pw, ph), (x, y, w, h) in pages:
        img_id = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s /BitsPerComponent 8 /Filter %s /Length %d >>\nstream\n"
                     % (im["w"], im["h"], im["cs"], im["filter"], len(data)) + data + b"\nendstream")
        content = b"q %.3f 0 0 %.3f %.3f %.3f cm /Im0 Do Q" % (w, h, x, y)
        c_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.3f %.3f] /Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                        % (pages_id, pw, ph, img_id, c_id)))
    meta_id = None
    if xmp:
        xb = xmp.encode("utf-8")
        meta_id = add(b"<< /Type /Metadata /Subtype /XML /Length %d >>\nstream\n" % len(xb) + xb + b"\nendstream")
    objs[pages_id - 1] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)
    objs[cat_id - 1] = b"<< /Type /Catalog /Pages %d 0 R" % pages_id + (b" /Metadata %d 0 R" % meta_id if meta_id else b"") + b" >>"
    info_id = add(b"<< " + b" ".join(b"/" + k.encode("ascii") + b" " + _pdf_str(v) for k, v in info.items()) + b" >>")
    buf = io.BytesIO()
    buf.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(buf.tell())
        buf.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offs:
        buf.write(b"%010d 00000 n \n" % off)
    fid = uuid.uuid4().hex.upper().encode("ascii")
    buf.write(b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objs) + 1, cat_id, info_id, fid, fid, xref))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--size", default="auto", help="auto | A4 | A5 | B5 | Letter | 16K … | WxH (mm)")
    ap.add_argument("--margin", type=float, default=0.0, help="white margin around each image, mm")
    ap.add_argument("--fit", default="contain", choices=["contain", "stretch"])
    ap.add_argument("--jpeg", type=int, default=90, help="JPEG quality for embedded images")
    ap.add_argument("--lossless", action="store_true", help="embed pixels losslessly (bigger file)")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    pages, aigc = [], None
    for f in a.images:
        img, meta = open_image(f)
        aigc = aigc or meta.get("aigc")
        pw, ph = page_size_pt(img, meta, a.size)
        m = a.margin * PT_PER_MM
        bw, bh = pw - 2 * m, ph - 2 * m
        if bw <= 0 or bh <= 0:
            die("--margin is larger than the page")
        if a.fit == "stretch":
            w, h = bw, bh
        else:
            k = min(bw / img.width, bh / img.height)
            w, h = img.width * k, img.height * k
        x, y = (pw - w) / 2, (ph - h) / 2
        data, im = encode_image(img, a.jpeg, a.lossless)
        pages.append((data, im, (pw, ph), (x, y, w, h)))
    info = {"Title": a.title or Path(a.out).stem, "Creator": SKILL_TAG, "Producer": PRODUCER,
            "CreationDate": time.strftime("D:%Y%m%d%H%M%S")}
    if aigc:
        info["AIGC"] = json.dumps(aigc, ensure_ascii=False)
        info["Keywords"] = "AIGC; AI生成 · Inko"
    write_pdf(pages, Path(a.out), info, xmp_packet(aigc) if aigc else None)
    emit({"out": a.out, "pages": len(pages), "page_size_mm": [round(pages[0][2][0] / PT_PER_MM, 1), round(pages[0][2][1] / PT_PER_MM, 1)],
          "bytes": Path(a.out).stat().st_size, "aigc_metadata": bool(aigc)})


if __name__ == "__main__":
    main()
