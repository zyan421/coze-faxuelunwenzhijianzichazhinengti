#!/usr/bin/env python3
"""
inject-docx-comments.py —— 把 issues.json 注入为 docx 的 Word 批注副本

用法:
    python3 inject-docx-comments.py <原.docx> <issues.json> <输出.annotated.docx>

特性:
    - 纯 Python 3.8+ 标准库（zipfile / xml.etree.ElementTree / copy / re /
      hashlib / json / os / sys / datetime / argparse）
    - 只用 Word 批注（Word Comments），不用修订模式（Track Changes）
    - 不修改原 docx；副本文件名为 `{原名}.annotated.docx`
    - 支持 comments.xml / commentsExtended.xml / commentsIds.xml 三件套，
      在 Word 2016+ / WPS 最新版本打开零 "此文档的批注已更新" 提示
    - 回读三不变式校验，失败时删除副本并返回失败 issue id 列表

参见:
    rules/docx-annotation.md —— 算法与 OOXML 规范
    rules/issues-schema.md   —— issues.json 数据契约
    rules/error-handling.md  —— 错误处理决策树
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 命名空间（OOXML）
# --------------------------------------------------------------------------- #

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14_NS = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15_NS = "{http://schemas.microsoft.com/office/word/2012/wordml}"
W16CID_NS = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# 注册命名空间的短前缀，让输出 XML 更干净
ET.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")
ET.register_namespace("w14", "http://schemas.microsoft.com/office/word/2010/wordml")
ET.register_namespace("w15", "http://schemas.microsoft.com/office/word/2012/wordml")
ET.register_namespace("w16cid", "http://schemas.microsoft.com/office/word/2016/wordml/cid")
ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")

# 需要重写或新建的 part 清单；其余 part 原样字节复制
PARTS_TO_REWRITE = {
    "word/document.xml",
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
    "word/comments.xml",
    "word/commentsExtended.xml",
    "word/commentsIds.xml",
}

# --------------------------------------------------------------------------- #
# 常量：issues.json schema 的枚举
# --------------------------------------------------------------------------- #

ENUM_SOURCE = {"thesis", "citation"}
ENUM_CATEGORY = {
    "structure", "argumentation", "literature-review",
    "empirical", "legal-norms", "language", "policy",
    "academic-integrity", "citation-format", "citation-missing-info",
}
ENUM_SEVERITY = {"fatal", "major", "minor"}
ENUM_SCOPE = {"document", "chapter", "paragraph", "sentence", "span"}
ID_PATTERN = re.compile(
    r"^(thesis|citation)-"
    r"(structure|argumentation|literature-review|empirical|legal-norms|"
    r"language|policy|academic-integrity|citation-format|citation-missing-info)"
    r"-\d{3}$"
)
GROUP_ID_PATTERN = re.compile(r"^g-\d{3,}$")
CELL_KEYS = {"table_index", "row", "col", "paragraph_index_in_cell"}

# category/severity 中文映射，用于批注正文模板
CATEGORY_CN = {
    "structure": "结构",
    "argumentation": "论证深度",
    "literature-review": "文献综述",
    "empirical": "实证",
    "legal-norms": "规范适用",
    "language": "语言",
    "policy": "对策",
    "academic-integrity": "学术不端线索",
    "citation-format": "引注格式",
    "citation-missing-info": "引注信息",
}
SEVERITY_CN = {"fatal": "致命", "major": "重要", "minor": "轻微"}
SEVERITY_RANK = {"fatal": 0, "major": 1, "minor": 2}

MAX_COMMENTS = 500
AUTHOR = "张老师的agent"
INITIALS = "ZA"


# --------------------------------------------------------------------------- #
# T2.2：issues.json 自校验（与 rules/issues-schema.md §6 双轨一致）
# --------------------------------------------------------------------------- #

def validate_issues_json(data: Any, *, input_is_pdf: bool = False) -> list[str]:
    """校验 issues.json，返回错误消息列表（空表示合规）。v2 含 anchor_text 字段必填。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["top-level must be JSON object"]
    if data.get("schema_version") != "1.0":
        errors.append(f"schema_version must be '1.0', got {data.get('schema_version')!r}")
    issues = data.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be an array")
        return errors

    seen_ids: set[str] = set()
    group_key_to_gid: dict[tuple, str] = {}
    gid_to_group_key: dict[str, tuple] = {}

    for idx, it in enumerate(issues):
        ctx = f"issues[{idx}]"
        if not isinstance(it, dict):
            errors.append(f"{ctx}: must be object")
            continue

        for field in (
            "id", "source", "category", "severity", "scope",
            "locator", "excerpt", "problem", "suggestion", "group_id",
            "anchor_text",  # v2 新增必填
        ):
            if field not in it:
                errors.append(f"{ctx}: missing required field '{field}'")

        if it.get("source") is not None and it["source"] not in ENUM_SOURCE:
            errors.append(f"{ctx}.source: {it['source']!r} not in {sorted(ENUM_SOURCE)}")
        if it.get("category") is not None and it["category"] not in ENUM_CATEGORY:
            errors.append(f"{ctx}.category: {it['category']!r} not in enum")
        if it.get("severity") is not None and it["severity"] not in ENUM_SEVERITY:
            errors.append(f"{ctx}.severity: {it['severity']!r} not in {sorted(ENUM_SEVERITY)}")
        if it.get("scope") is not None and it["scope"] not in ENUM_SCOPE:
            errors.append(f"{ctx}.scope: {it['scope']!r} not in {sorted(ENUM_SCOPE)}")

        if isinstance(it.get("id"), str):
            if not ID_PATTERN.match(it["id"]):
                errors.append(f"{ctx}.id: pattern mismatch, got {it['id']!r}")
            else:
                id_src = it["id"].split("-", 1)[0]
                id_cat = it["id"].rsplit("-", 1)[0].split("-", 1)[1]
                if it.get("source") and id_src != it["source"]:
                    errors.append(f"{ctx}.id: prefix {id_src!r} mismatches source {it['source']!r}")
                if it.get("category") and id_cat != it["category"]:
                    errors.append(f"{ctx}.id: middle {id_cat!r} mismatches category {it['category']!r}")

        if isinstance(it.get("id"), str):
            if it["id"] in seen_ids:
                errors.append(f"{ctx}.id: duplicate {it['id']!r}")
            seen_ids.add(it["id"])

        if it.get("scope") in ("document", "chapter"):
            if it.get("excerpt", "") != "":
                errors.append(f"{ctx}.excerpt: scope={it['scope']!r} requires empty string")

        if isinstance(it.get("excerpt"), str) and len(it["excerpt"]) > 60:
            errors.append(f"{ctx}.excerpt: length {len(it['excerpt'])} > 60")
        if isinstance(it.get("anchor_text"), str) and len(it["anchor_text"]) > 60:
            errors.append(f"{ctx}.anchor_text: length {len(it['anchor_text'])} > 60")
        if isinstance(it.get("problem"), str):
            if len(it["problem"]) == 0:
                errors.append(f"{ctx}.problem: must be non-empty")
            if len(it["problem"]) > 200:
                errors.append(f"{ctx}.problem: length {len(it['problem'])} > 200")

        sug = it.get("suggestion")
        if sug is not None:
            if not isinstance(sug, list):
                errors.append(f"{ctx}.suggestion: must be array")
            else:
                if not (1 <= len(sug) <= 5):
                    errors.append(f"{ctx}.suggestion: array length {len(sug)} not in [1,5]")
                for j, s in enumerate(sug):
                    if not isinstance(s, str):
                        errors.append(f"{ctx}.suggestion[{j}]: must be string")
                    elif len(s) > 500:
                        errors.append(f"{ctx}.suggestion[{j}]: length {len(s)} > 500")
                    elif len(s) == 0:
                        errors.append(f"{ctx}.suggestion[{j}]: must be non-empty")

        loc = it.get("locator")
        if not isinstance(loc, dict):
            errors.append(f"{ctx}.locator: must be object")
            continue

        chapter = loc.get("chapter")
        if not isinstance(chapter, str) or not chapter:
            errors.append(f"{ctx}.locator.chapter: must be non-empty string")
        pidx = loc.get("paragraph_index")
        if not isinstance(pidx, int) or isinstance(pidx, bool):
            errors.append(f"{ctx}.locator.paragraph_index: must be integer")
        elif pidx < -1:
            errors.append(f"{ctx}.locator.paragraph_index: {pidx} < -1")

        present_cell = CELL_KEYS & set(loc.keys())
        if present_cell and present_cell != CELL_KEYS:
            missing = CELL_KEYS - present_cell
            errors.append(f"{ctx}.locator: table-cell keys must appear together; missing {sorted(missing)}")
        if present_cell == CELL_KEYS:
            if loc.get("paragraph_index") != -1:
                errors.append(f"{ctx}.locator: table-cell locator requires paragraph_index == -1")
            for k in CELL_KEYS:
                v = loc.get(k)
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errors.append(f"{ctx}.locator.{k}: must be non-negative integer")

        if input_is_pdf and "page_number" not in loc:
            errors.append(f"{ctx}.locator.page_number: required for pdf input")
        if "page_number" in loc:
            pn = loc["page_number"]
            if not isinstance(pn, int) or isinstance(pn, bool) or pn < 1:
                errors.append(f"{ctx}.locator.page_number: must be integer >= 1")

        if "bbox" in loc:
            bb = loc["bbox"]
            if not isinstance(bb, list) or len(bb) != 4:
                errors.append(f"{ctx}.locator.bbox: must be array of length 4")
            else:
                for k, v in enumerate(bb):
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        errors.append(f"{ctx}.locator.bbox[{k}]: must be number")

        for k in ("sentence_index", "char_offset_in_paragraph"):
            if k in loc:
                v = loc[k]
                if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                    errors.append(f"{ctx}.locator.{k}: must be non-negative integer")

        gid = it.get("group_id")
        if isinstance(gid, str):
            if not GROUP_ID_PATTERN.match(gid):
                errors.append(f"{ctx}.group_id: pattern mismatch, got {gid!r}")
            gkey = (it.get("source"), it.get("category"),
                    loc.get("chapter"), loc.get("paragraph_index"))
            # 同一 (source, category, chapter, paragraph_index) 只能有一个 group_id
            if gkey in group_key_to_gid and group_key_to_gid[gkey] != gid:
                errors.append(
                    f"{ctx}.group_id: inconsistent; key {gkey} previously mapped to "
                    f"{group_key_to_gid[gkey]!r}, got {gid!r}"
                )
            else:
                group_key_to_gid[gkey] = gid
                gid_to_group_key[gid] = gkey

    return errors


# --------------------------------------------------------------------------- #
# T2.3：段落定位
# --------------------------------------------------------------------------- #

def _collect_body_paragraphs(body: ET.Element) -> list[ET.Element]:
    """v2.5: 递归收集 body 下的所有正文段（跳过表格内部段）。

    与 v2 的区别：v2 仅取 body 直接子节点的 <w:p>；v2.5 递归进入 <w:sdt>
    （结构化文档标签，例如 Word 自动生成的目录字段）等容器，但不进入 <w:tbl>。
    表格内段落另由 locate_paragraph_in_cell 处理。
    """
    out: list[ET.Element] = []

    def walk(elem: ET.Element, in_table: bool):
        for child in elem:
            tag = child.tag
            if tag == f"{W_NS}p" and not in_table:
                out.append(child)
            elif tag == f"{W_NS}tbl":
                # 表格内段不纳入主索引（与 extract-docx 一致）
                continue
            else:
                walk(child, in_table=in_table)

    walk(body, in_table=False)
    return out


def locate_paragraph(document_root: ET.Element, paragraph_index: int) -> ET.Element | None:
    """按 body 内非表格段的绝对顺序定位第 N 个段落。

    v2.5: 改用递归收集，能正确处理 <w:sdt>（如目录字段）包裹的段落。
    """
    body = document_root.find(f"{W_NS}body")
    if body is None:
        return None
    paragraphs = _collect_body_paragraphs(body)
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return None
    return paragraphs[paragraph_index]


def _para_text(p: ET.Element) -> str:
    """提取段内所有 <w:t> 拼接后的文本。"""
    return "".join((t.text or "") for t in p.iter(f"{W_NS}t"))


def _is_toc_entry(text: str) -> bool:
    """v2.5/v2.6: 判断段落是否为目录条目。

    目录条目典型形态：
    - 短文本（< 100 字）
    - 末尾以 1-4 位数字（页码）结尾
    - 数字前是字母 / 中文 / 制表符 / 空格

    v2.6 强化：允许"中文+数字直接相邻"形式（如"...制度构造31"），
    因为部分 Word 自动生成的 TOC 在中文与页码之间不插入空白。
    """
    if not text or len(text) > 100:
        return False
    stripped = text.strip()
    import re as _re
    # 末尾以页码（1-4 位数字）结尾
    if _re.search(r"\d{1,4}\s*$", stripped):
        # 但要排除"年份 / 法条号"等真正含数字的正文
        # 启发式：短文本（< 80 字）+ 以数字结尾 = 目录条目
        # 真正的正文段几乎不会以裸数字结尾
        if len(stripped) < 80:
            return True
    return False


def locate_by_anchor_text(document_root: ET.Element, anchor_text: str) -> ET.Element | None:
    """v2.5: 用 anchor_text 在文档中文本搜索定位段落。

    搜索策略：
    1. 把 anchor 长度归一化到 ≤ 30 字符
    2. 优先匹配"非目录条目"的段落（避免目录中同名标题误命中）
    3. 如果所有命中都是目录条目，再退回首个命中
    4. 找不到，返回 None
    """
    if not anchor_text:
        return None
    body = document_root.find(f"{W_NS}body")
    if body is None:
        return None
    needle = anchor_text.strip()[:30]
    if not needle:
        return None
    paragraphs = _collect_body_paragraphs(body)
    non_toc_hit = None
    any_hit = None
    for p in paragraphs:
        text = _para_text(p)
        if needle in text:
            if any_hit is None:
                any_hit = p
            if not _is_toc_entry(text):
                non_toc_hit = p
                break  # 找到正文中的，立即返回
    return non_toc_hit if non_toc_hit is not None else any_hit


def locate_anchor_offset_in_paragraph(paragraph: ET.Element, anchor_text: str) -> int | None:
    """v2.5: 在指定段落内查 anchor_text 的字符偏移；找不到返回 None。"""
    if not anchor_text:
        return None
    text = _para_text(paragraph)
    needle = anchor_text.strip()[:30]
    if not needle:
        return None
    idx = text.find(needle)
    return idx if idx >= 0 else None


def locate_paragraph_in_cell(
    document_root: ET.Element,
    table_index: int,
    row: int,
    col: int,
    p_idx_in_cell: int,
) -> ET.Element | None:
    """按表格四字段联合定位 body 级 <w:tbl> 内的 <w:p>。"""
    body = document_root.find(f"{W_NS}body")
    if body is None:
        return None
    tables = [t for t in body if t.tag == f"{W_NS}tbl"]
    if table_index < 0 or table_index >= len(tables):
        return None
    tbl = tables[table_index]
    rows = [r for r in tbl if r.tag == f"{W_NS}tr"]
    if row < 0 or row >= len(rows):
        return None
    tr = rows[row]
    cells = [c for c in tr if c.tag == f"{W_NS}tc"]
    if col < 0 or col >= len(cells):
        return None
    tc = cells[col]
    ps = [p for p in tc if p.tag == f"{W_NS}p"]
    if p_idx_in_cell < 0 or p_idx_in_cell >= len(ps):
        return None
    return ps[p_idx_in_cell]


def locate_by_issue(document_root: ET.Element, issue: dict) -> ET.Element | None:
    """根据 issue.locator 字段组合 + anchor_text 选择正确的定位函数。

    v2.5 定位优先级（核心修复——v2 完全没用 anchor_text，导致索引偏移时全员错位）：
      1. 表格四字段联合定位（locate_paragraph_in_cell）—— 最精确
      2. anchor_text 全文搜索（locate_by_anchor_text）—— v2.5 主路径
      3. paragraph_index 数字索引（locate_paragraph）—— 仅当 anchor 缺失时使用
      4. 章节首段降级（fallback_to_chapter_head）—— 最后兜底，由调用者处理

    v2.5 设计要点：
    - paragraph_index 在 docx 含 <w:sdt>（目录字段）等容器时易整体偏移
    - 因此 anchor_text 存在时**强制走 anchor 路径**，命中失败直接返回 None
      让调用者走章节降级，而不是用一个已知不可靠的数字索引误导用户
    """
    loc = issue.get("locator", {})
    if all(k in loc for k in CELL_KEYS):
        return locate_paragraph_in_cell(
            document_root,
            loc["table_index"],
            loc["row"],
            loc["col"],
            loc["paragraph_index_in_cell"],
        )

    # v2.5: anchor_text 优先；若 anchor 存在但未命中，**不**退回数字索引
    anchor = issue.get("anchor_text") or ""
    if anchor:
        return locate_by_anchor_text(document_root, anchor)

    # anchor 完全缺失时才用数字索引（向后兼容旧 issues.json）
    pidx = loc.get("paragraph_index")
    if not isinstance(pidx, int) or pidx < 0:
        return None
    return locate_paragraph(document_root, pidx)


# --------------------------------------------------------------------------- #
# T2.4：run 展平与 run 拆分
# --------------------------------------------------------------------------- #

def flatten_runs(paragraph: ET.Element) -> list[tuple]:
    """把段内所有 <w:r> 展平为字符序列。

    返回: [(char, run_element, t_element_or_None, char_index_in_t, rPr_snapshot)]
    - <w:t> 的每个字符展开为一个条目
    - <w:br> 展开为 "\n" 条目，t_element=None
    - <w:tab> 展开为 "\t" 条目，t_element=None
    - 其他子元素（drawing/fldChar/sym 等）跳过
    """
    seq: list[tuple] = []
    for r in paragraph.findall(f"{W_NS}r"):
        rPr = r.find(f"{W_NS}rPr")
        rPr_copy = copy.deepcopy(rPr) if rPr is not None else None
        for child in r:
            if child.tag == f"{W_NS}t":
                for i, ch in enumerate(child.text or ""):
                    seq.append((ch, r, child, i, rPr_copy))
            elif child.tag == f"{W_NS}br":
                seq.append(("\n", r, None, -1, rPr_copy))
            elif child.tag == f"{W_NS}tab":
                seq.append(("\t", r, None, -1, rPr_copy))
            # 其他子元素（drawing/pict/fldChar/instrText/sym）跳过
    return seq


def split_run_at(
    paragraph: ET.Element, char_offset: int
) -> tuple[ET.Element | None, ET.Element | None]:
    """在段内 char_offset 位置拆分命中的 <w:r>。

    返回 (前半 run, 后半 run)。边界行为：
    - offset == 0 → (None, 第一个 <w:r>)
    - offset >= 段长 → (最后一个 <w:r>, None)
    - 命中 run 边界 → (prev_r, cur_r)，不新建
    - 命中 run 中部 → 拆 <w:t>，新建后半 run 并插在原 run 之后
    """
    seq = flatten_runs(paragraph)
    runs = paragraph.findall(f"{W_NS}r")

    if not seq:
        return (None, None)
    if char_offset <= 0:
        return (None, runs[0] if runs else None)
    if char_offset >= len(seq):
        return (runs[-1] if runs else None, None)

    _, run, t_elem, idx_in_t, rPr = seq[char_offset]

    # 情况 1：命中 run 边界
    if seq[char_offset - 1][1] is not run:
        return (seq[char_offset - 1][1], run)

    # 情况 2：命中 <w:br>/<w:tab> 之前
    if t_elem is None:
        return (run, None)

    # 情况 3：命中 run 中部的 <w:t>，需要拆分
    left_text = (t_elem.text or "")[:idx_in_t]
    right_text = (t_elem.text or "")[idx_in_t:]
    t_elem.text = left_text
    t_elem.set(XML_SPACE, "preserve")

    # 构造后半 run，rPr 深拷贝
    new_r = ET.Element(f"{W_NS}r")
    if rPr is not None:
        new_r.append(copy.deepcopy(rPr))
    new_t = ET.SubElement(new_r, f"{W_NS}t")
    new_t.set(XML_SPACE, "preserve")
    new_t.text = right_text

    # 若原 run 在命中 <w:t> 之后还有其他子元素（第二个 <w:t>、<w:br>），
    # 也要挪到 new_r 以保持字符序列连续性
    t_idx_in_run = list(run).index(t_elem)
    tail_children = list(run)[t_idx_in_run + 1:]
    for child in tail_children:
        run.remove(child)
        new_r.append(child)

    # 把 new_r 插入到原 run 之后
    r_idx_in_para = list(paragraph).index(run)
    paragraph.insert(r_idx_in_para + 1, new_r)
    return (run, new_r)


# --------------------------------------------------------------------------- #
# T2.5：commentRange 三元标记插入
# --------------------------------------------------------------------------- #

def insert_comment_markers(
    paragraph: ET.Element,
    start_offset: int,
    end_offset: int,
    comment_id: int,
) -> None:
    """在段内 [start_offset, end_offset) 区间挂载批注 w:id=comment_id。

    - 先拆末端，再拆起始（避免偏移污染）
    - <w:commentRangeStart> 在起始之前
    - <w:commentRangeEnd> 在终止之后
    - <w:commentReference> 包在 <w:r> 里紧随 <w:commentRangeEnd>
    """
    if start_offset >= end_offset:
        # 空区间或反向区间：改为单点挂载（把 start_offset 当作 end_offset）
        end_offset = start_offset + 1 if start_offset + 1 <= _paragraph_length(paragraph) else start_offset

    # 先拆末端
    _, after_end = split_run_at(paragraph, end_offset)
    # 再拆起始
    before_start, _ = split_run_at(paragraph, start_offset)

    # 插入 commentRangeStart
    crs = ET.Element(f"{W_NS}commentRangeStart")
    crs.set(f"{W_NS}id", str(comment_id))
    if before_start is not None:
        idx = list(paragraph).index(before_start) + 1
    else:
        idx = 0
    paragraph.insert(idx, crs)

    # 插入 commentRangeEnd
    cre = ET.Element(f"{W_NS}commentRangeEnd")
    cre.set(f"{W_NS}id", str(comment_id))
    if after_end is not None:
        idx_end = list(paragraph).index(after_end)
    else:
        idx_end = len(list(paragraph))
    paragraph.insert(idx_end, cre)

    # 插入 <w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>
    #   <w:commentReference w:id="N"/></w:r>
    ref_r = ET.Element(f"{W_NS}r")
    ref_rPr = ET.SubElement(ref_r, f"{W_NS}rPr")
    rStyle = ET.SubElement(ref_rPr, f"{W_NS}rStyle")
    rStyle.set(f"{W_NS}val", "CommentReference")
    ref = ET.SubElement(ref_r, f"{W_NS}commentReference")
    ref.set(f"{W_NS}id", str(comment_id))
    paragraph.insert(idx_end + 1, ref_r)


def _paragraph_length(paragraph: ET.Element) -> int:
    """段落的字符长度（供 insert_comment_markers 的空区间降级使用）。"""
    return len(flatten_runs(paragraph))


# --------------------------------------------------------------------------- #
# T2.8：三键稳定排序与 500 条上限
# --------------------------------------------------------------------------- #

def select_top_n(issues: list[dict], cap: int = MAX_COMMENTS) -> tuple[list[dict], list[dict]]:
    """三键稳定排序后取前 cap 条。

    排序键：severity 降序（fatal>major>minor）、locator.paragraph_index 升序、id 字典序升序。
    返回 (selected, overflowed)。selected 保留排序后的顺序，overflowed 供 MD 报告标注。
    """
    def sort_key(it: dict) -> tuple:
        sev = SEVERITY_RANK.get(it.get("severity"), 99)
        loc = it.get("locator", {})
        pidx = loc.get("paragraph_index")
        if not isinstance(pidx, int):
            pidx = 10 ** 9
        return (sev, pidx, it.get("id", ""))

    sorted_issues = sorted(issues, key=sort_key)
    return sorted_issues[:cap], sorted_issues[cap:]


# --------------------------------------------------------------------------- #
# 批注正文模板（与 templates/annotation-body-template.md 共用逻辑）
# --------------------------------------------------------------------------- #

def render_annotation_body(issue: dict, fallback_prefix: str | None = None) -> list[str]:
    """把 issue 渲染为批注正文字符串列表，每个元素对应批注内的一"行"（用 <w:br/> 分隔）。

    模板：
        【{category_cn}】【{severity_cn}】{problem} → {suggestion[0]}
        （若 suggestion 多条）其他建议：
          · {suggestion[1]}
          · {suggestion[2]}
        [id: {issue_id}]

    fallback_prefix：定位失败降级时加在第一行开头的警告前缀。
    """
    cat_cn = CATEGORY_CN.get(issue.get("category", ""), issue.get("category", ""))
    sev_cn = SEVERITY_CN.get(issue.get("severity", ""), issue.get("severity", ""))
    problem = issue.get("problem", "")
    sugg = issue.get("suggestion", []) or [""]
    issue_id = issue.get("id", "")

    first_line = f"【{cat_cn}】【{sev_cn}】{problem} → {sugg[0]}"
    if fallback_prefix:
        first_line = f"{fallback_prefix}: {first_line}"

    lines: list[str] = [first_line]
    if len(sugg) > 1:
        lines.append("其他建议：")
        for s in sugg[1:]:
            lines.append(f"  · {s}")
    lines.append(f"[id: {issue_id}]")
    return lines


# --------------------------------------------------------------------------- #
# T2.6：三个 OOXML 部件构造（comments.xml / commentsExtended.xml / commentsIds.xml）
# --------------------------------------------------------------------------- #

def _stable_para_id(issue_id: str) -> str:
    """paraId：8 位大写十六进制，由 issue_id 稳定哈希生成。"""
    h = hashlib.md5(("para:" + issue_id).encode("utf-8")).hexdigest()
    return h[:8].upper()


def _stable_durable_id(issue_id: str) -> str:
    """durableId：8 位大写十六进制，由 issue_id 稳定哈希生成，与 paraId 独立。"""
    h = hashlib.md5(("durable:" + issue_id).encode("utf-8")).hexdigest()
    return h[:8].upper()


def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601（秒精度，带 Z 后缀）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_comments_xml(mounted: list[tuple[int, dict, str | None]]) -> bytes:
    """构造 word/comments.xml。

    mounted: [(comment_id, issue, fallback_prefix), ...] 列表，comment_id 自 0 起递增
    """
    ET.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")

    root = ET.Element(f"{W_NS}comments")
    # 注册 w14 / mc 命名空间（作为根元素的属性）
    root.set(f"{MC_NS}Ignorable", "w14")

    now = _now_iso()
    for cid, issue, fb_prefix in mounted:
        cmt = ET.SubElement(root, f"{W_NS}comment")
        cmt.set(f"{W_NS}id", str(cid))
        cmt.set(f"{W_NS}author", AUTHOR)
        cmt.set(f"{W_NS}initials", INITIALS)
        cmt.set(f"{W_NS}date", now)
        cmt.set(f"{W14_NS}paraId", _stable_para_id(issue["id"]))

        p = ET.SubElement(cmt, f"{W_NS}p")
        # paraId 也要在 <w:p> 上以 w14:paraId 出现，用于 commentsExtended 对齐
        p.set(f"{W14_NS}paraId", _stable_para_id(issue["id"]))
        lines = render_annotation_body(issue, fallback_prefix=fb_prefix)
        for i, line in enumerate(lines):
            if i > 0:
                # 换行 <w:r><w:br/></w:r>
                br_r = ET.SubElement(p, f"{W_NS}r")
                ET.SubElement(br_r, f"{W_NS}br")
            t_r = ET.SubElement(p, f"{W_NS}r")
            t = ET.SubElement(t_r, f"{W_NS}t")
            t.set(XML_SPACE, "preserve")
            t.text = line

    return _serialize_xml(root, default_ns="w", ns_map={
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
        "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    })


def build_comments_extended_xml(mounted: list[tuple[int, dict, str | None]]) -> bytes:
    """构造 word/commentsExtended.xml。"""
    root = ET.Element(f"{W15_NS}commentsEx")
    for _, issue, _ in mounted:
        cx = ET.SubElement(root, f"{W15_NS}commentEx")
        cx.set(f"{W15_NS}paraId", _stable_para_id(issue["id"]))
        cx.set(f"{W15_NS}done", "0")
    return _serialize_xml(root, default_ns="w15", ns_map={
        "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    })


def build_comments_ids_xml(mounted: list[tuple[int, dict, str | None]]) -> bytes:
    """构造 word/commentsIds.xml。"""
    root = ET.Element(f"{W16CID_NS}commentsIds")
    for _, issue, _ in mounted:
        cid_el = ET.SubElement(root, f"{W16CID_NS}commentId")
        cid_el.set(f"{W16CID_NS}paraId", _stable_para_id(issue["id"]))
        cid_el.set(f"{W16CID_NS}durableId", _stable_durable_id(issue["id"]))
    return _serialize_xml(root, default_ns="w16cid", ns_map={
        "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    })


def _serialize_xml(root: ET.Element, default_ns: str, ns_map: dict[str, str]) -> bytes:
    """用 ET 的 tostring 序列化并加 XML 声明头。

    ns_map: 期望的 namespace 前缀映射；通过 ET.register_namespace 注册以便输出前缀干净。
    """
    for prefix, uri in ns_map.items():
        ET.register_namespace(prefix, uri)
    xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # 确保以 standalone="yes" 结尾（OOXML 期望）
    decl = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    if xml_bytes.startswith(b'<?xml'):
        # 替换 ET 生成的声明
        end = xml_bytes.index(b'?>') + 2
        xml_bytes = decl + xml_bytes[end:]
    else:
        xml_bytes = decl + b"\n" + xml_bytes
    return xml_bytes


# --------------------------------------------------------------------------- #
# T2.7：Content_Types 与 rels 注入
# --------------------------------------------------------------------------- #

def patch_content_types(ct_bytes: bytes) -> bytes:
    """向 [Content_Types].xml 追加三个 Override。"""
    root = ET.fromstring(ct_bytes)
    existing = {o.get("PartName") for o in root if o.tag == f"{CT_NS}Override"}

    def _add(part: str, content_type: str) -> None:
        if part in existing:
            return
        o = ET.SubElement(root, f"{CT_NS}Override")
        o.set("PartName", part)
        o.set("ContentType", content_type)

    _add("/word/comments.xml",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml")
    _add("/word/commentsExtended.xml",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml")
    _add("/word/commentsIds.xml",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml")

    return _serialize_xml(root, default_ns="", ns_map={
        "": "http://schemas.openxmlformats.org/package/2006/content-types",
    })


def patch_document_rels(rels_bytes: bytes) -> bytes:
    """向 word/_rels/document.xml.rels 追加三个 Relationship，rId 从 max+1 分配。"""
    root = ET.fromstring(rels_bytes)
    used: list[int] = []
    existing_targets = set()
    for rel in root:
        rid = rel.get("Id", "")
        if rid.startswith("rId"):
            try:
                used.append(int(rid[3:]))
            except ValueError:
                pass
        existing_targets.add(rel.get("Target", ""))

    next_n = (max(used) if used else 0) + 1

    def _add(rel_type: str, target: str) -> None:
        nonlocal next_n
        if target in existing_targets:
            return
        r = ET.SubElement(root, f"{REL_NS}Relationship")
        r.set("Id", f"rId{next_n}")
        r.set("Type", rel_type)
        r.set("Target", target)
        next_n += 1

    _add("http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
         "comments.xml")
    _add("http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
         "commentsExtended.xml")
    _add("http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
         "commentsIds.xml")

    return _serialize_xml(root, default_ns="", ns_map={
        "": "http://schemas.openxmlformats.org/package/2006/relationships",
    })


# --------------------------------------------------------------------------- #
# T2.9：同段多批注、跨段批注与定位失败降级
# --------------------------------------------------------------------------- #

def determine_offsets(issue: dict, paragraph: ET.Element) -> tuple[int, int]:
    """根据 issue 的 scope 与 locator 字段决定 [start_offset, end_offset) 区间。

    v2.5 优先级：
    - char_offset_in_paragraph 给定 → 字级定位 [offset, offset+10) 或到段末
    - 否则用 anchor_text 在段内搜索 → 命中则高亮 anchor_text 那一段
    - 否则按 scope/sentence_index 走旧逻辑
    """
    loc = issue.get("locator", {})
    para_len = _paragraph_length(paragraph)
    if para_len == 0:
        return (0, 0)

    char_offset = loc.get("char_offset_in_paragraph")
    if isinstance(char_offset, int) and char_offset >= 0:
        start = min(char_offset, para_len)
        end = min(start + 10, para_len)
        return (start, end)

    # v2.5: anchor_text 字级精确定位
    anchor = issue.get("anchor_text") or ""
    if anchor:
        a_start = locate_anchor_offset_in_paragraph(paragraph, anchor)
        if a_start is not None:
            a_len = min(len(anchor.strip()), 30)
            return (a_start, min(a_start + a_len, para_len))

    sent_idx = loc.get("sentence_index")
    if isinstance(sent_idx, int) and sent_idx >= 0:
        start, end = _sentence_range(paragraph, sent_idx)
        if start is not None:
            return (start, end)

    # scope=paragraph / chapter / document / span 或其他：整段
    return (0, para_len)


_SENT_ENDERS = set("。！？；.!?;")


def _sentence_range(paragraph: ET.Element, sentence_index: int) -> tuple[int | None, int]:
    """按段内字符序列切分句子，返回第 N 句的 [start, end) 区间。

    切分规则与 rules/online-verification-unified.md / rules/issues-schema.md § 3 一致。
    缩写保护：'.' 前后为字母或数字时不触发切分。
    """
    seq = flatten_runs(paragraph)
    chars = [s[0] for s in seq]
    sentences: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(chars):
        if ch in _SENT_ENDERS:
            # 缩写保护：仅对英文句号 '.'
            if ch == "." and i > 0 and i < len(chars) - 1:
                left_is_alnum = chars[i - 1].isalnum() and chars[i - 1].isascii()
                right_is_alnum = chars[i + 1].isalnum() and chars[i + 1].isascii()
                if left_is_alnum and right_is_alnum:
                    continue
            sentences.append((start, i + 1))
            start = i + 1
    # 段末未终结的部分作为最后一句
    if start < len(chars):
        sentences.append((start, len(chars)))
    if not sentences:
        return (None, 0)
    if sentence_index < 0 or sentence_index >= len(sentences):
        return (None, 0)
    return sentences[sentence_index]


def fallback_to_chapter_head(
    document_root: ET.Element, chapter: str
) -> ET.Element | None:
    """定位失败降级：尝试找章节标题段落；失败则返回文档首段。

    v2.5: 使用 _collect_body_paragraphs 递归收集，与主定位算法一致。
    """
    body = document_root.find(f"{W_NS}body")
    if body is None:
        return None
    paragraphs = _collect_body_paragraphs(body)
    if not paragraphs:
        return None

    # 尝试按章节号匹配（如 "3.2.1" 或 "第3章" / "第三章"）
    ch_str = str(chapter).strip()
    for p in paragraphs:
        text = _para_text(p)
        if ch_str and ch_str in text:
            return p
        if ch_str and len(ch_str) > 0 and text.lstrip().startswith(ch_str):
            return p

    # 降级到文档首段
    return paragraphs[0]


def mount_issue(
    document_root: ET.Element,
    issue: dict,
    comment_id: int,
) -> tuple[bool, str | None]:
    """把一条 issue 挂载到 document.xml 的正确段落。

    返回 (挂载成功, fallback_prefix_or_None)。
    - 成功 + 无降级：(True, None)
    - 成功 + 章节首段降级：(True, "⚠️ 精确定位失败, 挂到本章开头")
    - 成功 + 文档首段降级：(True, "⚠️ 精确定位失败, 挂到文档开头")
    - 失败（段落无可挂点）：(False, None)
    """
    loc = issue.get("locator", {})
    p = locate_by_issue(document_root, issue)
    fallback_prefix: str | None = None

    if p is None:
        # 降级：章节首段
        chapter = loc.get("chapter", "")
        p = fallback_to_chapter_head(document_root, chapter)
        if p is None:
            return (False, None)
        # 判断是章节首段还是文档首段
        body = document_root.find(f"{W_NS}body")
        first_para = (_collect_body_paragraphs(body)[0] if (body is not None and _collect_body_paragraphs(body)) else None)
        if p is first_para:
            fallback_prefix = "⚠️ 精确定位失败, 挂到文档开头"
        else:
            fallback_prefix = "⚠️ 精确定位失败, 挂到本章开头"

    # 决定区间
    start_offset, end_offset = determine_offsets(issue, p)
    # 零长度段的兜底：至少在段首挂一个空区间的标记
    if start_offset == end_offset == 0 and _paragraph_length(p) == 0:
        # 零长度段：挂一个仅 commentRangeStart + commentRangeEnd + Ref 的标记序列
        crs = ET.Element(f"{W_NS}commentRangeStart")
        crs.set(f"{W_NS}id", str(comment_id))
        p.insert(0, crs)
        cre = ET.Element(f"{W_NS}commentRangeEnd")
        cre.set(f"{W_NS}id", str(comment_id))
        p.insert(1, cre)
        ref_r = ET.Element(f"{W_NS}r")
        ref_rPr = ET.SubElement(ref_r, f"{W_NS}rPr")
        rStyle = ET.SubElement(ref_rPr, f"{W_NS}rStyle")
        rStyle.set(f"{W_NS}val", "CommentReference")
        ref = ET.SubElement(ref_r, f"{W_NS}commentReference")
        ref.set(f"{W_NS}id", str(comment_id))
        p.insert(2, ref_r)
        return (True, fallback_prefix)

    insert_comment_markers(p, start_offset, end_offset, comment_id)
    return (True, fallback_prefix)


def mount_all_issues(
    document_root: ET.Element, selected_issues: list[dict]
) -> list[tuple[int, dict, str | None]]:
    """挂载全部 issues，返回成功挂载的 (comment_id, issue, fallback_prefix) 列表。

    同段多批注按 char_offset 降序插入（从后往前避免偏移污染）。
    按段落分组，段内按 offset 降序。
    """
    # 第 1 轮：先算出每条 issue 会挂在哪个段落（用于按段分组）
    # 同时给每条 issue 预分配 comment_id
    plan: list[tuple[int, dict, ET.Element | None, tuple[int, int], str | None]] = []
    for cid, issue in enumerate(selected_issues):
        loc = issue.get("locator", {})
        p = locate_by_issue(document_root, issue)
        fb_prefix: str | None = None
        if p is None:
            p = fallback_to_chapter_head(document_root, loc.get("chapter", ""))
            if p is None:
                plan.append((cid, issue, None, (0, 0), None))
                continue
            body = document_root.find(f"{W_NS}body")
            first_para = (_collect_body_paragraphs(body)[0] if (body is not None and _collect_body_paragraphs(body)) else None)
            if p is first_para:
                fb_prefix = "⚠️ 精确定位失败, 挂到文档开头"
            else:
                fb_prefix = "⚠️ 精确定位失败, 挂到本章开头"
        start, end = determine_offsets(issue, p)
        plan.append((cid, issue, p, (start, end), fb_prefix))

    # 第 2 轮：按段分组，段内按 offset 降序挂载
    from collections import defaultdict
    by_para: dict[int, list] = defaultdict(list)
    for cid, issue, p, (start, end), fb in plan:
        if p is None:
            continue
        by_para[id(p)].append((cid, issue, p, start, end, fb))

    mounted: list[tuple[int, dict, str | None]] = []
    for pid_key, items in by_para.items():
        # 降序：offset 大的先挂（从后往前插）
        items.sort(key=lambda x: x[3], reverse=True)
        for cid, issue, p, start, end, fb in items:
            para_len = _paragraph_length(p)
            if start == end == 0 and para_len == 0:
                # 零长度段兜底
                crs = ET.Element(f"{W_NS}commentRangeStart")
                crs.set(f"{W_NS}id", str(cid))
                p.insert(0, crs)
                cre = ET.Element(f"{W_NS}commentRangeEnd")
                cre.set(f"{W_NS}id", str(cid))
                p.insert(1, cre)
                ref_r = ET.Element(f"{W_NS}r")
                ref_rPr = ET.SubElement(ref_r, f"{W_NS}rPr")
                rStyle = ET.SubElement(ref_rPr, f"{W_NS}rStyle")
                rStyle.set(f"{W_NS}val", "CommentReference")
                ref = ET.SubElement(ref_r, f"{W_NS}commentReference")
                ref.set(f"{W_NS}id", str(cid))
                p.insert(2, ref_r)
            else:
                insert_comment_markers(p, start, end, cid)
            mounted.append((cid, issue, fb))

    # 按 comment_id 重新排序（便于 comments.xml 按顺序写）
    mounted.sort(key=lambda x: x[0])
    return mounted


# --------------------------------------------------------------------------- #
# T2.10：回读三不变式校验
# --------------------------------------------------------------------------- #

class ReadbackError(RuntimeError):
    """回读校验失败，副本已被删除。"""


def readback_verify(annotated_docx_path: Path) -> tuple[bool, list[str]]:
    """回读校验，返回 (ok, errors)。

    三不变式（R6.22）：
    1. 所有关键 XML part 可被 ET.parse 解析
    2. <w:commentRangeStart> 数量 == <w:commentRangeEnd> 数量
    3. <w:comment> 数量 == <w:commentReference> 数量
    """
    errors: list[str] = []
    required_parts = [
        "[Content_Types].xml",
        "word/document.xml",
        "word/comments.xml",
        "word/commentsExtended.xml",
        "word/commentsIds.xml",
        "word/_rels/document.xml.rels",
    ]

    try:
        with zipfile.ZipFile(annotated_docx_path, "r") as z:
            names = set(z.namelist())
            for p in required_parts:
                if p not in names:
                    errors.append(f"missing part: {p}")
                    continue
                try:
                    ET.fromstring(z.read(p))
                except ET.ParseError as e:
                    errors.append(f"parse failed for {p}: {e}")

            if "word/document.xml" in names and "word/comments.xml" in names:
                try:
                    doc_root = ET.fromstring(z.read("word/document.xml"))
                    cm_root = ET.fromstring(z.read("word/comments.xml"))
                except ET.ParseError:
                    pass
                else:
                    n_start = len(doc_root.findall(f".//{W_NS}commentRangeStart"))
                    n_end = len(doc_root.findall(f".//{W_NS}commentRangeEnd"))
                    if n_start != n_end:
                        errors.append(
                            f"commentRangeStart count ({n_start}) != commentRangeEnd count ({n_end})"
                        )
                    n_comment = len(cm_root.findall(f".//{W_NS}comment"))
                    n_ref = len(doc_root.findall(f".//{W_NS}commentReference"))
                    if n_comment != n_ref:
                        errors.append(
                            f"w:comment count ({n_comment}) != w:commentReference count ({n_ref})"
                        )
    except (zipfile.BadZipFile, OSError) as e:
        errors.append(f"zip read failed: {e}")

    return (len(errors) == 0, errors)


# --------------------------------------------------------------------------- #
# T2.11：主流程与白名单 part 复制
# --------------------------------------------------------------------------- #

def load_docx_document_xml(src_path: Path) -> tuple[ET.Element, bytes, bytes]:
    """从源 docx 读入 document.xml / [Content_Types].xml / document.xml.rels。"""
    with zipfile.ZipFile(src_path, "r") as z:
        doc_bytes = z.read("word/document.xml")
        ct_bytes = z.read("[Content_Types].xml")
        rels_bytes = z.read("word/_rels/document.xml.rels")
    root = ET.fromstring(doc_bytes)
    return root, ct_bytes, rels_bytes


def write_annotated_docx(
    src_path: Path,
    dst_path: Path,
    new_parts: dict[str, bytes],
) -> None:
    """把原 docx 复制到 dst，覆盖/新建 new_parts 中指定的 parts。

    v2.5 改进：未修改的部件用原 ZipInfo 透传（保留压缩级别等元数据），
    避免重写后文件大小异常缩水。
    """
    with zipfile.ZipFile(src_path, "r") as src, \
            zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as dst:
        existing = set(src.namelist())
        for info in src.infolist():
            name = info.filename
            if name in PARTS_TO_REWRITE and name in new_parts:
                # 修改过的部件：用新内容，按默认压缩
                dst.writestr(name, new_parts[name])
            else:
                # 未修改的部件：连同 ZipInfo 一起透传（保留压缩/时间戳/外部属性）
                data = src.read(name)
                dst.writestr(info, data)
        # 新建 part（原 docx 不存在但需新写入的）
        for name in PARTS_TO_REWRITE:
            if name not in existing and name in new_parts:
                dst.writestr(name, new_parts[name])


def inject(
    src_docx: Path,
    issues_json: Path,
    dst_docx: Path,
) -> tuple[int, list[str]]:
    """主入口：注入批注，返回 (成功挂载条数, overflowed_ids)。

    失败路径：
    - issues.json 自校验失败 → raise ValueError
    - 源 docx 损坏 → raise zipfile.BadZipFile
    - 回读三不变式失败 → 删除 dst_docx，raise ReadbackError
    """
    # 1) 读 issues.json + 校验
    data = json.loads(issues_json.read_text(encoding="utf-8"))
    errs = validate_issues_json(data, input_is_pdf=False)
    if errs:
        raise ValueError("issues.json validation failed:\n" + "\n".join(errs[:10]))

    # 2) 三键排序 + 500 条上限
    selected, overflowed = select_top_n(data["issues"])
    overflowed_ids = [it["id"] for it in overflowed]

    # 3) 读 document.xml + 同源的 Content_Types / rels
    doc_root, ct_bytes, rels_bytes = load_docx_document_xml(src_docx)

    # 4) 挂载全部 issues
    mounted = mount_all_issues(doc_root, selected)

    # 5) 序列化 document.xml
    new_doc_bytes = _serialize_xml(doc_root, default_ns="w", ns_map={
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
        "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    })

    # 6) 构造三个批注 part
    new_comments = build_comments_xml(mounted)
    new_comments_ex = build_comments_extended_xml(mounted)
    new_comments_ids = build_comments_ids_xml(mounted)

    # 7) 补丁 Content_Types / rels
    new_ct = patch_content_types(ct_bytes)
    new_rels = patch_document_rels(rels_bytes)

    new_parts = {
        "word/document.xml": new_doc_bytes,
        "word/comments.xml": new_comments,
        "word/commentsExtended.xml": new_comments_ex,
        "word/commentsIds.xml": new_comments_ids,
        "[Content_Types].xml": new_ct,
        "word/_rels/document.xml.rels": new_rels,
    }

    # 8) 写副本
    write_annotated_docx(src_docx, dst_docx, new_parts)

    # 9) 回读三不变式
    ok, verify_errors = readback_verify(dst_docx)
    if not ok:
        try:
            os.remove(dst_docx)
        except OSError:
            pass
        raise ReadbackError(
            "readback invariants failed:\n" + "\n".join(verify_errors)
        )

    return (len(mounted), overflowed_ids)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="inject-docx-comments",
        description="Inject issues.json into a docx as Word comments, "
                    "producing an annotated copy.",
    )
    ap.add_argument("src_docx", type=Path, help="source .docx file (unmodified)")
    ap.add_argument("issues_json", type=Path, help="issues.json path")
    ap.add_argument("dst_docx", type=Path, help="output .annotated.docx path")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not args.src_docx.is_file():
        print(f"error: source docx not found: {args.src_docx}", file=sys.stderr)
        return 2
    if not args.issues_json.is_file():
        print(f"error: issues.json not found: {args.issues_json}", file=sys.stderr)
        return 2

    try:
        count, overflowed = inject(args.src_docx, args.issues_json, args.dst_docx)
    except ValueError as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 3
    except ReadbackError as e:
        print(f"readback error: {e}", file=sys.stderr)
        return 4
    except (zipfile.BadZipFile, OSError) as e:
        print(f"I/O error: {e}", file=sys.stderr)
        return 5

    print(f"[inject-docx-comments] mounted: {count} comments -> {args.dst_docx}")
    if overflowed:
        print(f"[inject-docx-comments] overflowed (not exported as comments): {len(overflowed)} issues")
        for oid in overflowed[:5]:
            print(f"  - {oid}")
        if len(overflowed) > 5:
            print(f"  ... ({len(overflowed) - 5} more)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
