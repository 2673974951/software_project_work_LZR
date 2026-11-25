"""把 docs/report.md 转成排版好的 Word 文档.

只支持本项目报告用到的 Markdown 子集：标题、段落、加粗、无序/有序列表、
缩进代码块、管道表格、图片与图题，以及 <!-- LANDSCAPE --> 横向页标记。

排版遵循中文学术报告惯例：
* 正文宋体小四（12pt），1.5 倍行距，首行缩进 2 字符；
* 标题黑体，一级三号（16pt）、二级四号（13.5pt）；
* 表格五号（9.5pt），表题居中加粗置于表上方，表头跨页重复；
* 图片居中，图题置于图下方。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
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


def set_run(run, *, size=12, bold=False, ea=SONG, latin=LATIN, color=None):
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = RGBColor(*color)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:ascii"), latin)
    rFonts.set(qn("w:hAnsi"), latin)
    rFonts.set(qn("w:eastAsia"), ea)


def add_rich(par, text, *, size=12, ea=SONG, bold=False):
    for part in re.split(r"(\*\*.+?\*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            run = par.add_run(part[2:-2])
            set_run(run, size=size, bold=True, ea=ea)
        else:
            run = par.add_run(part)
            set_run(run, size=size, bold=bold, ea=ea)


def body_par(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.first_line_indent = Pt(24)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    add_rich(p, text)
    return p


def caption_par(doc, text, *, keep_next=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.first_line_indent = Pt(0)
    pf.space_before = Pt(6 if keep_next else 2)
    pf.space_after = Pt(4 if keep_next else 8)
    pf.keep_with_next = keep_next
    add_rich(p, text, size=10.5, ea=HEI)
    for r in p.runs:
        r.bold = True
    return p


def heading(doc, text, level):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.first_line_indent = Pt(0)
    pf.keep_with_next = True
    if level == 2:
        pf.space_before = Pt(16)
        pf.space_after = Pt(8)
        size = 16
    else:
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
        size = 13.5
    run = p.add_run(text)
    set_run(run, size=size, bold=True, ea=HEI, latin=HEI)
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
        run = p.add_run(line if line else " ")
        set_run(run, size=9, ea=SONG, latin=MONO)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def add_page_number(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._element.addnext(fld)
    set_run(run, size=9)


def shade(cell, hexcolor="EDF2F7"):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def _disp_len(s):
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def _set_row_flags(row, header=False):
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))
    if header:
        trPr.append(OxmlElement("w:tblHeader"))


def add_table(doc, lines, total_cm=15.4):
    rows = []
    for ln in lines:
        if re.fullmatch(r"\|?[\s:\-|]+\|?", ln.strip()):
            continue
        rows.append([c.strip() for c in ln.strip().strip("|").split("|")])
    if not rows:
        return
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    weights = []
    for j in range(ncol):
        raw = max(_disp_len(r[j]) for r in rows)
        weights.append(max(min(raw, 26), 6))
    s = sum(weights)
    widths = [max(total_cm * w / s, 1.2) for w in weights]
    scale = total_cm / sum(widths)
    widths = [w * scale for w in widths]

    font = 9.0 if ncol >= 7 else 9.5
    table = doc.add_table(rows=len(rows), cols=ncol)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)

    for i, row in enumerate(rows):
        _set_row_flags(table.rows[i], header=(i == 0))
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.width = Cm(widths[j])
            cell.text = ""
            par = cell.paragraphs[0]
            pf = par.paragraph_format
            pf.first_line_indent = Pt(0)
            pf.space_before = Pt(1)
            pf.space_after = Pt(1)
            pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
            par.alignment = (WD_ALIGN_PARAGRAPH.CENTER if (i == 0 or j > 0)
                             else WD_ALIGN_PARAGRAPH.LEFT)
            run = par.add_run(val)
            set_run(run, size=font, bold=(i == 0), ea=HEI if i == 0 else SONG)
            if i == 0:
                shade(cell)
    for j in range(ncol):
        table.columns[j].width = Cm(widths[j])
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
            blocks.append(("hr", None))
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


def build():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.left_margin = sec.right_margin = Cm(2.8)
    add_page_number(sec)

    normal = doc.styles["Normal"]
    normal.font.name = LATIN
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)

    # ---------------- 封面
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p.add_run("《软件工程管理》课程作业"), size=16, bold=True, ea=HEI, latin=HEI)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("资源受限下软件项目调度的")
    set_run(r, size=24, bold=True, ea=HEI, latin=HEI)
    r.add_break()
    r2 = p.add_run("启发式算法设计与优化")
    set_run(r2, size=24, bold=True, ea=HEI, latin=HEI)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    r = p.add_run("——以“智慧校园综合服务平台 v2.0”为例")
    set_run(r, size=13, ea=HEI, latin=HEI)

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
    for i, (k, v) in enumerate(info):
        for j, val in enumerate((k, v)):
            cell = table.cell(i, j)
            cell.text = ""
            par = cell.paragraphs[0]
            par.paragraph_format.first_line_indent = Pt(0)
            par.alignment = (WD_ALIGN_PARAGRAPH.CENTER if j == 0
                             else WD_ALIGN_PARAGRAPH.LEFT)
            run = par.add_run(val + "：" if j == 0 else val)
            set_run(run, size=12, ea=HEI if j == 0 else SONG)
    doc.add_page_break()

    # ---------------- 正文
    blocks = parse(MD.read_text(encoding="utf-8"))
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
        if kind == "hr":
            continue
        if kind == "h1":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_run(p.add_run(payload), size=20, bold=True, ea=HEI, latin=HEI)
        elif kind == "h2":
            heading(doc, payload, 2)
        elif kind == "h3":
            heading(doc, payload, 3)
        elif kind == "marker":
            if "LANDSCAPE -->" in payload and payload.strip() == "<!-- LANDSCAPE -->":
                enter_landscape()
            elif payload.strip() == "<!-- /LANDSCAPE -->":
                leave_landscape()
        elif kind == "p":
            plain = payload.replace("**", "")
            is_bold = payload.startswith("**") and payload.endswith("**")
            if is_bold and re.match(r"^表\s*C?\d+", plain):
                caption_par(doc, payload, keep_next=True)
            elif plain.startswith("关键词"):
                pp = doc.add_paragraph()
                pp.paragraph_format.first_line_indent = Pt(0)
                pp.paragraph_format.space_before = Pt(10)
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                add_rich(pp, payload, size=12)
            else:
                body_par(doc, payload)
        elif kind == "ul":
            for item in payload:
                pp = doc.add_paragraph()
                pf = pp.paragraph_format
                pf.left_indent = Cm(0.75)
                pf.first_line_indent = Cm(-0.4)
                pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
                pf.space_before = Pt(0)
                pf.space_after = Pt(0)
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                set_run(pp.add_run("• "), size=12)
                add_rich(pp, item)
        elif kind == "ol":
            for k, item in enumerate(payload, start=1):
                pp = doc.add_paragraph()
                pf = pp.paragraph_format
                pf.left_indent = Cm(0.9)
                pf.first_line_indent = Cm(-0.55)
                pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
                pf.space_before = Pt(0)
                pf.space_after = Pt(0)
                pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                set_run(pp.add_run("%d. " % k), size=12)
                add_rich(pp, item)
        elif kind == "code":
            code_block(doc, payload)
        elif kind == "table":
            add_table(doc, payload, total_cm=25.0 if landscape["on"] else 15.4)
        elif kind == "image":
            alt, rel = payload
            add_image(doc, alt, rel, width_cm=22.0 if landscape["on"] else 15.2)
            n_img += 1

    OUT.mkdir(exist_ok=True)
    target = OUT / "软件工程管理课程作业_题目3_RCPSP.docx"
    doc.save(target)
    print("已生成：%s" % target)
    print("段落数：%d，图片数：%d" % (len(doc.paragraphs), n_img))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
