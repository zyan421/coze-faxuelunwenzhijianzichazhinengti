#!/usr/bin/env python3
"""
citation-crossref.py —— v2.0.0 新增

抓取论文中的作者名，交叉对比**正文/脚注**与**参考文献章节**，生成三份对照表：

1. in_text_not_in_refs：正文引用但参考文献表无条目的作者
2. in_refs_not_in_text：参考文献列表有但正文未引用的条目
3. matched：两侧都有的作者

目的：替代 Agent 主观判断"脱钩"，给出机械可验证的对比表。

用法：
    python3 citation-crossref.py <input.pdf> <output.crossref.json>

输出 JSON 结构：
{
  "schema_version": "1.0",
  "pdf_path": "...",
  "refs_section_detected": true/false,
  "stats": {
    "authors_in_text": N1,
    "authors_in_refs": N2,
    "in_text_not_in_refs": N3,
    "in_refs_not_in_text": N4,
    "matched": N5
  },
  "in_text_not_in_refs": [{"author": "...", "context": "原文上下文"}],
  "in_refs_not_in_text": [{"author": "...", "entry": "参考文献条目"}],
  "matched": [{"author": "...", "text_count": N, "refs_count": N}]
}

依赖：PyMuPDF
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import fitz
except ImportError:
    print("error: PyMuPDF is required. pip install --user PyMuPDF", file=sys.stderr)
    sys.exit(10)


# 参考文献章节的标题模式
REFS_HEADERS = (
    "参考文献", "参 考 文 献",
    "References", "REFERENCES", "Bibliography",
)


def extract_sections(doc) -> tuple[str, str, int]:
    """把论文分为"正文前段"和"参考文献章节"，返回 (before_refs, refs_section, start_page_of_refs)。"""
    full_text_pages: list[str] = []
    for page_idx in range(doc.page_count):
        full_text_pages.append(doc[page_idx].get_text())

    # 找参考文献章节起始页
    refs_start_page = -1
    for idx, text in enumerate(full_text_pages):
        for header in REFS_HEADERS:
            # 标题在行首或文本前 50 字内
            if header in text[:200]:
                # 进一步确认是"章节标题"而非"参考文献"一词在正文中的提及
                header_idx = text.index(header)
                line = text[max(0, header_idx - 5):header_idx + len(header) + 20]
                # 简单启发：紧跟换行或冒号是标题
                if header in line and (idx > doc.page_count // 2):  # 通常在后半本
                    refs_start_page = idx
                    break
        if refs_start_page != -1:
            break

    if refs_start_page == -1:
        return ("\n".join(full_text_pages), "", -1)

    before = "\n".join(full_text_pages[:refs_start_page])
    # 参考文献章节 = refs_start_page 到末尾
    refs_section_full = "\n".join(full_text_pages[refs_start_page:])
    # 进一步精确：找章节开始的位置
    for header in REFS_HEADERS:
        if header in refs_section_full:
            refs_idx = refs_section_full.index(header)
            refs_section = refs_section_full[refs_idx:]
            break
    else:
        refs_section = refs_section_full

    return (before, refs_section, refs_start_page)


# 匹配中文作者名（2-4 字中文字符，常见双名/单名/三名）+ 后跟标点或特征文字
# 典型模式："张三：《……》" 或 "张三.文章题目" 或 "张三、李四"
CN_AUTHOR_PATTERN = re.compile(
    r"([\u4e00-\u9fa5]{2,4})(?:[：:]《|\.\s*[\u4e00-\u9fa5《]|[,，、]\s*[\u4e00-\u9fa5]{2,4}[\.:]|\s*[:：]\s*《|\s*等\s*[:：])"
)

# 英文作者：First Last 或 Last, First
EN_AUTHOR_PATTERN = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)"
)


def extract_authors(text: str) -> set[str]:
    """从文本中粗抓作者名。"""
    authors: set[str] = set()

    # 中文作者
    for m in CN_AUTHOR_PATTERN.finditer(text):
        name = m.group(1)
        # 过滤常见假阳性（如"我国"、"当前"、"研究"等非人名）
        if name not in _CN_FILTER:
            authors.add(name)

    # 英文作者
    for m in EN_AUTHOR_PATTERN.finditer(text):
        name = m.group(1)
        authors.add(name)

    return authors


# 常见不是人名的中文两字词（用于过滤 CN_AUTHOR_PATTERN 假阳性）
_CN_FILTER = {
    "我国", "中国", "当前", "研究", "信托", "股权", "登记", "公示", "制度",
    "法律", "财产", "公司", "法院", "法学", "学者", "本文", "笔者", "以上",
    "综上", "另外", "此外", "然而", "但是", "因此", "所以", "其中", "同时",
    "由于", "以及", "并且", "或者", "虽然", "尽管", "要求", "规定", "适用",
    "进行", "完成", "具有", "需要", "成为", "作为", "经过", "对于", "关于",
    "根据", "按照", "包括", "涉及", "通过", "体现", "实现", "解决", "保护",
    "系统", "问题", "分析", "讨论", "论述", "阐述", "认为", "指出", "提出",
    "必须", "应当", "可以", "应该", "不能", "不得", "已经", "尚未", "即将",
    "首先", "其次", "再次", "最后", "此前", "此后", "当前", "目前", "现在",
    "美国", "英国", "日本", "德国", "法国", "韩国", "欧盟", "台湾", "香港",
    "上海", "北京", "广州", "深圳", "杭州", "南京", "武汉", "成都", "西安",
    "规则", "原则", "效力", "基础", "理论", "实践", "路径", "策略", "方法",
    "机制", "模式", "体系", "结构", "关系", "影响", "发展", "改革", "完善",
    "监管", "银行", "商业", "金融", "市场", "经济", "社会", "国家", "政府",
    "主义", "主体", "客体", "对象", "内容", "形式", "范围", "领域", "方面",
    "具体", "抽象", "一般", "特殊", "独立", "一定", "相关", "主要", "核心",
    "关键", "重要", "根本", "基本", "普通", "典型", "特定", "专门", "一切",
    "所有", "全部", "部分", "若干", "多个", "少数", "大多", "全体", "整个",
    "存在", "处于", "反映", "体现", "表现", "呈现", "展现", "显示", "证明",
    "说明", "表明", "意味", "反思", "改进", "优化", "提升", "加强", "强化",
    "二〇", "二零", "年修订", "修订", "出版", "定稿", "草案", "意见", "通知",
    "批准", "发布", "发文", "征求",
}


def extract_refs_entries(refs_section: str) -> list[tuple[str, str]]:
    """从参考文献章节抽取每一条"author: entry" 对。

    粗启发：按"[N]"编号切分，每段的前 10 字符内通常有作者名。
    """
    entries: list[tuple[str, str]] = []

    # 按 [N] 切分
    parts = re.split(r"\[\d+\]", refs_section)
    for part in parts[1:]:  # 跳过标题前
        part = part.strip()
        if not part:
            continue
        # 提取前 60 字符作为 entry 预览
        entry = part[:100].replace("\n", " ")
        # 提取开头的作者名（中英文）
        # 中文：开头的 2-4 字中文
        cn_m = re.match(r"^([\u4e00-\u9fa5]{2,4})(?:[,，、、.:：《]|\s)", entry)
        en_m = re.match(r"^([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)", entry)

        author = ""
        if cn_m:
            author = cn_m.group(1)
        elif en_m:
            author = en_m.group(1)

        if author and author not in _CN_FILTER:
            entries.append((author, entry))

    return entries


def context_around(text: str, keyword: str, chars: int = 80) -> str:
    """返回 keyword 首次出现的前后 chars 字符上下文。"""
    idx = text.find(keyword)
    if idx < 0:
        return ""
    start = max(0, idx - chars // 2)
    end = min(len(text), idx + len(keyword) + chars // 2)
    return text[start:end].replace("\n", " ").strip()


def crossref(pdf_path: Path) -> dict:
    doc = fitz.open(pdf_path)
    before, refs_section, refs_start_page = extract_sections(doc)
    doc.close()

    if not refs_section:
        return {
            "schema_version": "1.0",
            "pdf_path": str(pdf_path),
            "refs_section_detected": False,
            "note": "未识别出参考文献章节，无法交叉对比",
            "stats": {"authors_in_text": 0, "authors_in_refs": 0,
                      "in_text_not_in_refs": 0, "in_refs_not_in_text": 0, "matched": 0},
            "in_text_not_in_refs": [],
            "in_refs_not_in_text": [],
            "matched": [],
        }

    authors_in_text = extract_authors(before)
    refs_entries = extract_refs_entries(refs_section)
    authors_in_refs = set(author for author, _ in refs_entries)

    in_text_only = authors_in_text - authors_in_refs
    in_refs_only = authors_in_refs - authors_in_text
    matched = authors_in_text & authors_in_refs

    # 排序以便可重现
    in_text_only_sorted = sorted(in_text_only)
    in_refs_only_sorted = sorted(in_refs_only)
    matched_sorted = sorted(matched)

    # 组装结果
    in_text_not_in_refs = []
    for author in in_text_only_sorted:
        in_text_not_in_refs.append({
            "author": author,
            "context": context_around(before, author),
        })

    in_refs_not_in_text = []
    refs_map: dict[str, list[str]] = {}
    for author, entry in refs_entries:
        refs_map.setdefault(author, []).append(entry)
    for author in in_refs_only_sorted:
        in_refs_not_in_text.append({
            "author": author,
            "entry": refs_map.get(author, [""])[0],
        })

    matched_detail = []
    for author in matched_sorted:
        text_count = before.count(author)
        refs_count = sum(1 for a, _ in refs_entries if a == author)
        matched_detail.append({
            "author": author,
            "text_count": text_count,
            "refs_count": refs_count,
        })

    return {
        "schema_version": "1.0",
        "pdf_path": str(pdf_path),
        "refs_section_detected": True,
        "refs_section_start_page": refs_start_page + 1,
        "stats": {
            "authors_in_text": len(authors_in_text),
            "authors_in_refs": len(authors_in_refs),
            "in_text_not_in_refs": len(in_text_only),
            "in_refs_not_in_text": len(in_refs_only),
            "matched": len(matched),
        },
        "in_text_not_in_refs": in_text_not_in_refs,
        "in_refs_not_in_text": in_refs_not_in_text,
        "matched": matched_detail,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="citation-crossref",
        description="Cross-reference authors in text vs references section (v2.0.0).",
    )
    ap.add_argument("src_pdf", type=Path)
    ap.add_argument("out_json", type=Path)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.src_pdf.is_file():
        print(f"error: pdf not found: {args.src_pdf}", file=sys.stderr)
        return 2

    result = crossref(args.src_pdf)
    args.out_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[citation-crossref] wrote {args.out_json}")
    print(f"  refs detected: {result['refs_section_detected']}")
    stats = result["stats"]
    print(f"  authors in text:     {stats['authors_in_text']}")
    print(f"  authors in refs:     {stats['authors_in_refs']}")
    print(f"  in_text_not_in_refs: {stats['in_text_not_in_refs']} (脚注引用但参考文献漏列——规范问题)")
    print(f"  in_refs_not_in_text: {stats['in_refs_not_in_text']} (参考文献列出但正文未引用——v2.6 起不再视为问题)")
    print(f"  matched:             {stats['matched']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
