"""把 docs/report.md 转成排版规范的 Word 文档.

支持的 Markdown 子集：标题、段落、无序/有序列表、缩进代码块、管道表格、
图片与图题，以及 <!-- LANDSCAPE --> 横向页标记。

排版约定（中文学术报告）
------------------------
* 正文：宋体 / Times New Roman 小四（12pt），1.5 倍行距，首行缩进 2 字符；
* 标题：黑体，一级三号（16pt）、二级四号（13.5pt）；
* 正文不使用加粗，强调一律通过字体（黑体）表达；
* 表格：三线表（顶线 1.5pt、表头下横线 1pt、底线 1.5pt），无底色、无竖线；
* 表题置于表上方、图题置于图下方，均居中、宋体五号（10.5pt）、不加粗；
* 封面单独成节，不编页码；目录与正文从第 1 页开始编号。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import (
    WD_ALIGN_PARAGRAPH,
    WD_LINE_SPACING,
    WD_TAB_ALIGNMENT,
    WD_TAB_LEADER,
)
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "report.md"
OUT = ROOT / "report"
FIGDIR = ROOT / "docs"

SONG = "宋体"
HEI = "黑体"
LATIN = "Times New Roman"
MONO = "Consolas"

BODY_PT = 12.0        # 小四
CAPTION_PT = 10.5     # 五号
TABLE_PT = 10.5       # 五号
CODE_PT = 9.5


# --------------------------------------------------------------- 基础工具
def set_run(run, *, size=BODY_PT, ea=SONG, latin=LATIN, bold=False):
    """统一设置字体；bold 仅在极端情况使用，正文一律传 False。"""
    run.font.size = Pt(size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), ea)


def add_text(par, text, *, size=BODY_PT, ea=SONG, latin=LATIN):
    """写入纯文本（自动去掉 Markdown 的加粗标记，正文不使用加粗）。"""
    clean = text.replace("**", "")
    if clean:
        set_run(par.add_run(clean), size=size, ea=ea, latin=latin)


def body_par(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.first_line_indent = Pt(BODY_PT * 2)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    add_text(p, text)
    return p


def caption_par(doc, text, *, keep_next=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.first_line_indent = Pt(0)
    pf.space_before = Pt(6 if keep_next else 2)
    pf.space_after = Pt(4 if keep_next else 8)
    pf.keep_with_next = keep_next
    add_text(p, text, size=CAPTION_PT)
    return p


def heading(doc, text, level, *, centered=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.first_line_indent = Pt(0)
    pf.keep_with_next = True
    pf.alignment = (WD_ALIGN_PARAGRAPH.CENTER if centered
                    else WD_ALIGN_PARAGRAPH.LEFT)
    if level == 1:
        pf.space_before = Pt(18 if centered else 16)
        pf.space_after = Pt(10 if centered else 8)
        size = 16
    else:
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
        size = 13.5
    set_run(p.add_run(text), size=size, ea=HEI, latin=HEI)
    return p


def code_block(doc, text):
    for line in text.split("\n"):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.left_indent = Cm(0.8)
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        set_run(p.add_run(line if line else " "), size=CODE_PT, ea=SONG, latin=MONO)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ------------------------------------------------------------- 页眉页脚
def _page_field(paragraph):
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "18")
    rPr.append(sz)
    r.append(rPr)
    fld.append(r)
    paragraph._p.append(fld)


def add_page_numbers(section, start_at=None):
    section.footer.is_linked_to_previous = False
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Pt(0)
    _page_field(p)
    if start_at is not None:
        sectPr = section._sectPr
        for old in sectPr.findall(qn("w:pgNumType")):
            sectPr.remove(old)
        pg = OxmlElement("w:pgNumType")
        pg.set(qn("w:start"), str(start_at))
        sectPr.append(pg)


# ----------------------------------------------------------------- 表格
def _three_line_borders(table):
    """三线表：仅保留顶线、底线与表头下横线，去掉所有竖线与底色。"""
    tbl = table._tbl
    tblPr = tbl.tblPr
    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    spec = {
        "top": ("single", 12),
        "left": ("none", 0),
        "bottom": ("single", 12),
        "right": ("none", 0),
        "insideH": ("none", 0),
        "insideV": ("none", 0),
    }
    for edge, (val, sz) in spec.items():
        el = OxmlElement("w:" + edge)
        el.set(qn("w:val"), val)
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000" if val == "single" else "auto")
        borders.append(el)
    tblPr.append(borders)

    for cell in table.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:tcBorders")):
            tcPr.remove(old)
        tcB = OxmlElement("w:tcBorders")
        b = OxmlElement("w:bottom")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "8")
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")
        tcB.append(b)
        tcPr.append(tcB)


def _disp_len(s):
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _row_flags(row, header=False):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))
    if header:
        trPr.append(OxmlElement("w:tblHeader"))


def add_table(doc, lines, total_cm=15.4):
    rows = []
    for ln in lines:
        if re.fullmatch(r"\|?[\s:\-|]+\|?", ln.strip()):
            continue
        rows.append([c.strip().replace("**", "") for c in ln.strip().strip("|").split("|")])
    if not rows:
        return
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    def _weight(j):
        content = max(_disp_len(r[j]) for r in rows)
        header = _disp_len(rows[0][j])
        return max(min(max(content, header), 22), 6)

    weights = [_weight(j) for j in range(ncol)]
    s = sum(weights)
    widths = [total_cm * w / s for w in weights]

    # 按"该列最长的不可断词（连续拉丁字符）"设定每列下限，
    # 避免 RANDOM、MSLK 这类单词被拆成两行；再从最宽的列里扣回总宽。
    def _min_cm(j):
        longest = 0
        for r in rows:
            for tok in re.findall(r"[A-Za-z0-9_.+\-/()]+", r[j]):
                longest = max(longest, len(tok))
        # 大写拉丁字母平均约 0.30cm/字符（10.5pt Times），再留 0.5cm 边距
        return max(1.3, 0.30 * longest + 0.5)

    floors = [_min_cm(j) for j in range(ncol)]
    widths = [max(w, floors[j]) for j, w in enumerate(widths)]
    excess = sum(widths) - total_cm
    guard = 0
    while excess > 1e-6 and guard < 200:
        guard += 1
        j = int(max(range(ncol), key=lambda k: widths[k] - floors[k]))
        room = widths[j] - floors[j]
        if room <= 1e-9:
            break
        take = min(room, excess)
        widths[j] -= take
        excess -= take
    scale = total_cm / sum(widths)
    widths = [w * scale for w in widths]

    table = doc.add_table(rows=len(rows), cols=ncol)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)

    for i, row in enumerate(rows):
        _row_flags(table.rows[i], header=(i == 0))
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.width = Cm(widths[j])
            cell.text = ""
            par = cell.paragraphs[0]
            pf = par.paragraph_format
            pf.first_line_indent = Pt(0)
            pf.space_before = Pt(2)
            pf.space_after = Pt(2)
            pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
            par.alignment = (WD_ALIGN_PARAGRAPH.CENTER if (i == 0 or j > 0)
                             else WD_ALIGN_PARAGRAPH.LEFT)
            # 表头用黑体，表体用宋体；均不加粗
            set_run(par.add_run(val), size=TABLE_PT, ea=HEI if i == 0 else SONG)
    for j in range(ncol):
        table.columns[j].width = Cm(widths[j])
    _three_line_borders(table)

    # 小表（不超过 12 行）整表保持在同一页，避免表头与正文被拆开
    if len(rows) <= 12:
        for row in table.rows[:-1]:
            for cell in row.cells:
                for par in cell.paragraphs:
                    par.paragraph_format.keep_with_next = True

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(2)
    spacer.paragraph_format.first_line_indent = Pt(0)


def add_image(doc, alt, rel, width_cm=15.2):
    path = FIGDIR / rel
    if not path.exists():
        print("  [warn] 图片缺失：%s" % path, file=sys.stderr)
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Pt(0)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    caption_par(doc, alt)


# ----------------------------------------------------------------- 解析
def parse(text):
    lines = text.splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        if s == "---":
            i += 1
            continue
        if s.startswith("<!--"):
            blocks.append(("marker", s))
            i += 1
            continue
        m = re.match(r"!\[(.*?)\]\((.*?)\)", s)
        if m:
            blocks.append(("image", (m.group(1), m.group(2))))
            i += 1
            continue
        if s.startswith("### "):
            blocks.append(("h3", s[4:]))
            i += 1
            continue
        if s.startswith("## "):
            blocks.append(("h2", s[3:]))
            i += 1
            continue
        if s.startswith("# "):
            blocks.append(("h1", s[2:]))
            i += 1
            continue
        if s.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i].strip())
                i += 1
            blocks.append(("table", tbl))
            continue
        if raw.startswith("    ") and s:
            code = []
            while i < len(lines):
                if lines[i].strip() == "":
                    j = i
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and lines[j].startswith("    "):
                        code.append("")
                        i += 1
                        continue
                    break
                if not lines[i].startswith("    "):
                    break
                code.append(lines[i][4:])
                i += 1
            blocks.append(("code", "\n".join(code)))
            continue
        if re.match(r"^- ", s):
            items = []
            while i < len(lines) and re.match(r"^\s*- ", lines[i]):
                items.append(re.sub(r"^\s*- ", "", lines[i]))
                i += 1
            blocks.append(("ul", items))
            continue
        if re.match(r"^\d+\. ", s):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                items.append(re.sub(r"^\s*\d+\. ", "", lines[i]))
                i += 1
            blocks.append(("ol", items))
            continue
        para = []
        while i < len(lines):
            cur = lines[i]
            cs = cur.strip()
            if not cs:
                break
            if (cur.startswith("    ") or cs.startswith(("#", "|", "![", "---", "<!--"))
                    or re.match(r"^(- |\d+\. )", cs)):
                break
            para.append(cs)
            i += 1
        if not para:
            i += 1
            continue
        blocks.append(("p", " ".join(para)))
    return blocks


def collect_headings(blocks):
    """按出现顺序收集目录条目：(层级, 文本)。"""
    out = []
    for kind, payload in blocks:
        if kind == "h2":
            if payload.strip() in ("摘要",):
                out.append((1, payload.strip()))
            else:
                out.append((1, payload.strip()))
        elif kind == "h3":
            out.append((2, payload.strip()))
    return out


def is_centered_heading(text: str) -> bool:
    """无编号的一级标题（摘要、参考文献、附录）居中，编号章节左对齐。"""
    return not re.match(r"^\d", text.strip())


# ----------------------------------------------------------------- 目录
def add_toc(doc, headings, pages):
    heading(doc, "目录", 1, centered=True)
    for level, text in headings:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.left_indent = Cm(0.0 if level == 1 else 0.9)
        pf.space_before = Pt(7 if level == 1 else 0)
        pf.space_after = Pt(1)
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
        pf.tab_stops.add_tab_stop(Cm(15.4), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        num = pages.get(text, "")
        set_run(p.add_run(text), size=BODY_PT, ea=HEI if level == 1 else SONG)
        set_run(p.add_run("\t%s" % num), size=BODY_PT)


# ----------------------------------------------------------------- 主流程
def build(toc_pages=None, out_name=None):
    doc = Document()

    cover = doc.sections[0]
    cover.page_width, cover.page_height = Cm(21.0), Cm(29.7)
    cover.top_margin = cover.bottom_margin = Cm(2.54)
    cover.left_margin = cover.right_margin = Cm(2.8)

    normal = doc.styles["Normal"]
    normal.font.name = LATIN
    normal.font.size = Pt(BODY_PT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)
    normal.paragraph_format.space_after = Pt(0)

    # ---------------- 封面（不编页码）
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p.add_run("《软件工程管理》课程作业"), size=16, ea=HEI, latin=HEI)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    r = p.add_run("资源受限下软件项目调度的")
    set_run(r, size=24, ea=HEI, latin=HEI)
    r.add_break()
    set_run(p.add_run("启发式算法设计与优化"), size=24, ea=HEI, latin=HEI)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    set_run(p.add_run("——以“智慧校园综合服务平台 v2.0”为例"), size=13, ea=HEI, latin=HEI)

    for _ in range(5):
        doc.add_paragraph()

    info = [
        ("课程名称", "软件工程管理"),
        ("作业题目", "题目 3：资源受限下软件项目调度 RCPSP 的启发式算法优化"),
        ("姓　　名", "__________________"),
        ("学　　号", "__________________"),
        ("专业班级", "__________________"),
        ("指导教师", "__________________"),
        ("完成日期", "2026 年"),
    ]
    table = doc.add_table(rows=len(info), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for i, (k, v) in enumerate(info):
        for j, val in enumerate((k + "：", v)):
            cell = table.cell(i, j)
            cell.width = Cm(4.2 if j == 0 else 9.0)
            cell.text = ""
            par = cell.paragraphs[0]
            par.paragraph_format.first_line_indent = Pt(0)
            par.paragraph_format.space_before = Pt(3)
            par.paragraph_format.space_after = Pt(3)
            par.alignment = (WD_ALIGN_PARAGRAPH.RIGHT if j == 0
                             else WD_ALIGN_PARAGRAPH.LEFT)
            set_run(par.add_run(val), size=BODY_PT, ea=HEI if j == 0 else SONG)

    # ---------------- 目录（从第 1 页开始编号）
    toc_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    toc_sec.page_width, toc_sec.page_height = Cm(21.0), Cm(29.7)
    toc_sec.top_margin = toc_sec.bottom_margin = Cm(2.54)
    toc_sec.left_margin = toc_sec.right_margin = Cm(2.8)
    add_page_numbers(toc_sec, start_at=1)

    blocks = parse(MD.read_text(encoding="utf-8"))
    headings = collect_headings(blocks)
    add_toc(doc, headings, toc_pages or {})
    doc.add_page_break()

    # ---------------- 正文
    started = False
    n_img = 0
    landscape = {"on": False}

    def enter_landscape():
        if landscape["on"]:
            return
        s = doc.add_section(WD_SECTION.NEW_PAGE)
        s.orientation = WD_ORIENT.LANDSCAPE
        s.page_width, s.page_height = Cm(29.7), Cm(21.0)
        s.top_margin = s.bottom_margin = Cm(2.0)
        s.left_margin = s.right_margin = Cm(2.0)
        landscape["on"] = True

    def leave_landscape():
        if not landscape["on"]:
            return
        s = doc.add_section(WD_SECTION.NEW_PAGE)
        s.orientation = WD_ORIENT.PORTRAIT
        s.page_width, s.page_height = Cm(21.0), Cm(29.7)
        s.top_margin = s.bottom_margin = Cm(2.54)
        s.left_margin = s.right_margin = Cm(2.8)
        landscape["on"] = False

    for kind, payload in blocks:
        if not started:
            if kind == "h2" and payload.strip() == "摘要":
                started = True
            else:
                continue
        if kind == "h1":
            heading(doc, payload, 1, centered=True)
        elif kind == "h2":
            heading(doc, payload, 1, centered=is_centered_heading(payload))
        elif kind == "h3":
            heading(doc, payload, 2, centered=False)
        elif kind == "marker":
            if payload.strip() == "<!-- LANDSCAPE -->":
                enter_landscape()
            elif payload.strip() == "<!-- /LANDSCAPE -->":
                leave_landscape()
        elif kind == "p":
            plain = payload.replace("**", "")
            if plain.startswith("关键词"):
                pp = doc.add_paragraph()
                pf = pp.paragraph_format
                pf.first_line_indent = Pt(0)
                pf.space_before = Pt(10)
                pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                label, _, rest = plain.partition("：")
                set_run(pp.add_run(label + "："), size=BODY_PT, ea=HEI)
                add_text(pp, rest)
            elif payload.startswith("**") and payload.endswith("**") and \
                    re.match(r"^表\s*C?\d+", plain):
                caption_par(doc, payload, keep_next=True)
            else:
                body_par(doc, payload)
        elif kind == "ul":
            for item in payload:
                pp = doc.add_paragraph()
                pf = pp.paragraph_format
                pf.left_indent = Cm(0.85)
                pf.first_line_indent = Cm(-0.42)
                pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
                pf.space_before = Pt(0)
                pf.space_after = Pt(0)
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                set_run(pp.add_run("• "), size=BODY_PT)
                add_text(pp, item)
        elif kind == "ol":
            for k, item in enumerate(payload, start=1):
                pp = doc.add_paragraph()
                pf = pp.paragraph_format
                pf.left_indent = Cm(0.95)
                pf.first_line_indent = Cm(-0.55)
                pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
                pf.space_before = Pt(0)
                pf.space_after = Pt(0)
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                set_run(pp.add_run("%d. " % k), size=BODY_PT)
                add_text(pp, item)
        elif kind == "code":
            code_block(doc, payload)
        elif kind == "table":
            add_table(doc, payload, total_cm=25.0 if landscape["on"] else 15.4)
        elif kind == "image":
            alt, rel = payload
            add_image(doc, alt, rel, width_cm=22.0 if landscape["on"] else 15.2)
            n_img += 1

    OUT.mkdir(exist_ok=True)
    target = OUT / (out_name or "软件工程管理课程作业_题目3_RCPSP.docx")
    doc.save(target)
    print("已生成：%s" % target)
    print("段落 %d，图片 %d，目录条目 %d" % (len(doc.paragraphs), n_img, len(headings)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown → Word 排版")
    ap.add_argument("--toc", help="目录页码映射 JSON（标题 -> 页码）")
    ap.add_argument("--out", help="输出文件名")
    args = ap.parse_args()
    pages = {}
    if args.toc and Path(args.toc).exists():
        pages = json.loads(Path(args.toc).read_text(encoding="utf-8"))
    return build(pages, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
