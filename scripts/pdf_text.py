"""从 PDF 中提取逐页文本，并生成目录页码映射.

用于给 Word 报告的目录补上真实页码：先渲染一次 PDF，把每个标题落在第几页
查出来，再带着这份映射重新生成一次文档。两次生成的排版完全一致（目录行数
不变），因此页码是准确的。

用法::

    python scripts/pdf_text.py report.pdf pages.json
    python scripts/pdf_text.py report.pdf toc.json --headings-from docs/report.md --offset 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zlib
from pathlib import Path

__all__ = ["extract_pages", "heading_pages"]


def _objects(data: bytes) -> dict:
    objs = {}
    for m in re.finditer(rb"(\d+)\s+0\s+obj", data):
        start = m.end()
        objs[int(m.group(1))] = data[start:data.find(b"endobj", start)]
    return objs


def _stream(body):
    if body is None:
        return None
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    start = m.end()
    raw = body[start:body.find(b"endstream", start)]
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw[-1:] in (b"\n", b"\r"):
        raw = raw[:-1]
    if b"/FlateDecode" in body[:m.start()]:
        # 用 decompressobj 容忍尾部多余字节（某些导出器会多写一个 EOL）
        try:
            return zlib.decompressobj().decompress(raw)
        except Exception:
            try:
                return zlib.decompress(raw)
            except Exception:
                return None
    return raw


def _balanced(buf: bytes, pos: int):
    i = pos + 2
    depth = 1
    while i < len(buf) and depth > 0:
        if buf[i:i + 2] == b"<<":
            depth += 1
            i += 2
        elif buf[i:i + 2] == b">>":
            depth -= 1
            i += 2
        else:
            i += 1
    return buf[pos + 2:i - 2], i


def _tounicode(b: bytes) -> dict:
    m = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", b, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            try:
                m[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be", "ignore")
            except Exception:
                pass
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", b, re.S):
        for g in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo, hi, base = int(g.group(1), 16), int(g.group(2), 16), int(g.group(3), 16)
            for i in range(min(hi - lo + 1, 65535)):
                try:
                    m[lo + i] = chr(base + i)
                except Exception:
                    pass
    return m


def _font_map(resources, objs, depth=0):
    if resources is None or depth > 3:
        return {}
    m = re.search(rb"/Font\s*(\d+\s+0\s+R|<<)", resources)
    if not m:
        return {}
    if m.group(1).endswith(b"R"):
        target = objs.get(int(re.match(rb"(\d+)", m.group(1)).group(1)))
        if target is None:
            return {}
        return {g.group(1).decode(): int(g.group(2))
                for g in re.finditer(rb"/(\w+)\s+(\d+)\s+0\s+R", target)}
    inner, _ = _balanced(resources, m.start(1))
    return {g.group(1).decode(): int(g.group(2))
            for g in re.finditer(rb"/(\w+)\s+(\d+)\s+0\s+R", inner)}


def _page_resources(body, objs):
    m = re.search(rb"/Resources\s*(\d+\s+0\s+R|<<)", body)
    if not m:
        return None
    if m.group(1).endswith(b"R"):
        return objs.get(int(re.match(rb"(\d+)", m.group(1)).group(1)))
    inner, _ = _balanced(body, m.start(1))
    return inner


def _decode(h: str, cm: dict) -> str:
    if not h:
        return ""
    if len(h) % 4 == 0:
        s = "".join(cm.get(int(h[i:i + 4], 16), "") for i in range(0, len(h), 4))
        if s:
            return s
    if len(h) % 2 == 0:
        return "".join(cm.get(int(h[i:i + 2], 16), "") for i in range(0, len(h), 2))
    return ""


def extract_pages(pdf_path) -> list:
    """返回逐页文本列表（索引 0 对应第 1 页）。"""
    data = Path(pdf_path).read_bytes()
    objs = _objects(data)

    font_tu = {}
    for n, body in objs.items():
        if b"/ToUnicode" in body:
            m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
            if m:
                s = _stream(objs.get(int(m.group(1))))
                if s:
                    font_tu[n] = _tounicode(s)

    pages = []
    for n, body in objs.items():
        if re.search(rb"/Type\s*/Page[^s]", body + b" "):
            m = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
            if m:
                pages.append((n, int(m.group(1))))
    pages.sort()

    out = []
    for pno, cnum in pages:
        st = _stream(objs.get(cnum))
        if not st:
            out.append("")
            continue
        pg = objs.get(pno)
        fmap = _font_map(_page_resources(pg, objs) if pg else None, objs)
        buf, cur = [], None
        for t in re.finditer(
            rb"/(\w+)\s+[\d\.]+\s+Tf"
            rb"|<([0-9A-Fa-f]+)>\s*Tj"
            rb"|\[((?:[^\[\]\\]|\\.)*)\]\s*TJ"
            rb"|\((?:[^()\\]|\\.)*\)\s*Tj"
            rb"|(TD|Td|T\*|TJ|Tj)", st, re.S):
            if t.group(1):
                cur = t.group(1).decode()
                continue
            cm = font_tu.get(fmap.get(cur, -1), {})
            if t.group(2) is not None:
                buf.append(_decode(t.group(2).decode(), cm))
            elif t.group(3) is not None:
                for hx in re.findall(rb"<([0-9A-Fa-f]+)>", t.group(3)):
                    buf.append(_decode(hx.decode(), cm))
                buf.append(" ")
            elif t.group(4) in (b"TD", b"Td", b"T*"):
                buf.append("\n")
        out.append("".join(buf))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def heading_pages(pdf_path, headings, offset=1):
    """把每个标题映射到它在 PDF 中出现的页码（已按 offset 换算成正文页码）。"""
    pages = extract_pages(pdf_path)
    norm_pages = [_norm(p) for p in pages]
    result = {}
    idx = 0
    for pno, text in enumerate(norm_pages, start=1):
        if text.count("……") >= 3 or text.count("....") >= 3:
            continue          # 跳过目录页
        while idx < len(headings):
            h = _norm(headings[idx])
            if h and h in text:
                result[headings[idx]] = pno - offset
                idx += 1
            else:
                break
    missing = [h for h in headings if h not in result]
    return result, missing, len(pages)


def _headings_from_md(path):
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            out.append(line[4:].strip())
        elif line.startswith("## "):
            out.append(line[3:].strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 文本提取与目录页码映射")
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--headings-from", help="从 Markdown 提取标题清单并输出页码映射")
    ap.add_argument("--offset", type=int, default=1,
                    help="物理页号 → 正文页码 的偏移（封面不计数时为 1）")
    args = ap.parse_args()

    if args.headings_from:
        heads = _headings_from_md(args.headings_from)
        mapping, missing, total = heading_pages(args.pdf, heads, args.offset)
        Path(args.out).write_text(json.dumps(mapping, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print("共 %d 页，标题 %d 个，成功定位 %d 个"
              % (total, len(heads), len(mapping)))
        if missing:
            print("未定位：%s" % "；".join(missing), file=sys.stderr)
    else:
        pages = extract_pages(args.pdf)
        Path(args.out).write_text(json.dumps(pages, ensure_ascii=False),
                                  encoding="utf-8")
        print("共 %d 页，%d 字符" % (len(pages), sum(len(p) for p in pages)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
