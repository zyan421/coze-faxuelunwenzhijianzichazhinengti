#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 docx 文档抽取纯文本内容（不依赖 python-docx）。
使用标准库 zipfile + xml.etree.ElementTree 解析 word/document.xml。

用法:
    python3 extract-docx.py input.docx [output.txt]
    python3 extract-docx.py input.docx        # 不指定输出时打印到 stdout

特性:
  - 保留段落换行
  - 保留制表符
  - 支持提取脚注（word/footnotes.xml）
  - 支持提取尾注（word/endnotes.xml）
  - 尝试标注章节层级（通过 w:pStyle 中的 Heading 样式）
  - 脚注/尾注以 [脚注 N] / [尾注 N] 标记插入原位
"""

import sys
import os
import zipfile
import re
import xml.etree.ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _text_of_run(run):
    """提取一个 <w:r> 元素下的文本。"""
    chunks = []
    for child in run:
        tag = child.tag.split("}", 1)[-1]
        if tag == "t":
            chunks.append(child.text or "")
        elif tag == "tab":
            chunks.append("\t")
        elif tag == "br":
            chunks.append("\n")
        elif tag == "footnoteReference":
            fnid = child.attrib.get(f"{{{W_NS}}}id", "")
            if fnid:
                chunks.append(f"[脚注{fnid}]")
        elif tag == "endnoteReference":
            enid = child.attrib.get(f"{{{W_NS}}}id", "")
            if enid:
                chunks.append(f"[尾注{enid}]")
        elif tag == "drawing":
            chunks.append("[图片]")
    return "".join(chunks)


def _style_of_paragraph(p):
    """返回段落样式名（如 Heading1 / Heading2 / Normal）。"""
    pPr = p.find("w:pPr", NS)
    if pPr is None:
        return None
    pStyle = pPr.find("w:pStyle", NS)
    if pStyle is None:
        return None
    return pStyle.attrib.get(f"{{{W_NS}}}val")


def _extract_paragraphs(root):
    """按顺序提取正文所有段落文本。"""
    body = root.find("w:body", NS)
    if body is None:
        return []
    paragraphs = []
    for p in body.iter(f"{{{W_NS}}}p"):
        style = _style_of_paragraph(p)
        text = "".join(_text_of_run(r) for r in p.findall("w:r", NS))
        # 兼容嵌套 run 的情况
        if not text:
            all_t = p.findall(".//w:t", NS)
            text = "".join(t.text or "" for t in all_t)
        # 段落样式前缀
        if style and (style.startswith("Heading") or style.startswith("heading") or style in ("Title", "Subtitle")):
            m = re.match(r"[Hh]eading\s*(\d+)", style)
            level = int(m.group(1)) if m else 1
            prefix = "#" * (level + 1) + " "
            text = prefix + text
        paragraphs.append(text)
    return paragraphs


def _extract_notes(z, inner_path):
    """从 zip 内某一 xml 抽取脚注 / 尾注，返回 {id: text}。"""
    notes = {}
    if inner_path not in z.namelist():
        return notes
    try:
        with z.open(inner_path) as f:
            tree = ET.parse(f)
        root = tree.getroot()
    except Exception:
        return notes
    for note in root.iter(f"{{{W_NS}}}footnote"):
        _collect_note(note, notes)
    for note in root.iter(f"{{{W_NS}}}endnote"):
        _collect_note(note, notes)
    return notes


def _collect_note(note, notes):
    nid = note.attrib.get(f"{{{W_NS}}}id", "")
    ntype = note.attrib.get(f"{{{W_NS}}}type", "")
    # 跳过分隔符等特殊项
    if ntype in ("separator", "continuationSeparator"):
        return
    if not nid:
        return
    texts = []
    for p in note.iter(f"{{{W_NS}}}p"):
        t = "".join((t.text or "") for t in p.findall(".//w:t", NS))
        if t:
            texts.append(t)
    notes[nid] = "\n".join(texts).strip()


def extract(docx_path):
    """返回 (body_text, footnotes_dict, endnotes_dict)。"""
    if not os.path.isfile(docx_path):
        raise FileNotFoundError(docx_path)
    with zipfile.ZipFile(docx_path) as z:
        if "word/document.xml" not in z.namelist():
            raise ValueError("文件不是有效的 docx（缺 word/document.xml）")
        with z.open("word/document.xml") as f:
            tree = ET.parse(f)
        root = tree.getroot()
        paragraphs = _extract_paragraphs(root)
        footnotes = _extract_notes(z, "word/footnotes.xml")
        endnotes = _extract_notes(z, "word/endnotes.xml")
    body = "\n".join(p for p in paragraphs if p is not None)
    return body, footnotes, endnotes


def format_output(body, footnotes, endnotes):
    parts = ["=" * 40, "正文", "=" * 40, body]
    if footnotes:
        parts += ["", "=" * 40, "脚注", "=" * 40]
        for k in sorted(footnotes.keys(), key=lambda x: int(x) if x.isdigit() else 10**9):
            parts.append(f"[脚注{k}] {footnotes[k]}")
    if endnotes:
        parts += ["", "=" * 40, "尾注", "=" * 40]
        for k in sorted(endnotes.keys(), key=lambda x: int(x) if x.isdigit() else 10**9):
            parts.append(f"[尾注{k}] {endnotes[k]}")
    return "\n".join(parts)


def main():
    if len(sys.argv) < 2:
        print("用法: python3 extract-docx.py input.docx [output.txt]", file=sys.stderr)
        sys.exit(2)
    docx_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) >= 3 else None
    body, footnotes, endnotes = extract(docx_path)
    text = format_output(body, footnotes, endnotes)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入 {out_path}（{len(text)} 字符）", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
