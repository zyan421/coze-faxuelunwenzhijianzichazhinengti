"""
法学论文质检自查 Agent —— unified-thesis-reviewer 总编排器 (v2.0)

核心职责：
1. 接收论文文件（docx / pdf / txt / md）或 URL
2. 调用 UTR 工具链执行一站式审查，生成严格 UTR schema issues.json
3. 生成 .annotated.docx 或 .annotated.pdf 批注文档
4. 返回 Markdown 总报告 + issues.json + 修改建议
5. 质量门禁：issue >= 8，实质性 >= 5，无 anchor_text 空洞
6. 失败时暴露完整堆栈，禁止泛化话术
"""

import os
import sys
import json
import tempfile
import traceback
import re
from typing import Any, Dict, List, Optional

from langchain.tools import tool
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import AnyMessage, ToolMessage
from langchain_openai import ChatOpenAI
from coze_coding_utils.runtime_ctx.context import default_headers
from storage.memory.memory_saver import get_memory_saver

# ---------------------------------------------------------------------------
# 常量 / 路径
# ---------------------------------------------------------------------------
LLM_CONFIG = "config/agent_llm_config.json"
UTR_BUNDLE = os.path.join(os.path.dirname(__file__), "..", "tools", "scripts", "utr-bundle")

ENUM_SOURCE = {"thesis", "citation"}
ENUM_CATEGORY = {
    "structure", "argumentation", "literature-review", "empirical",
    "legal-norms", "language", "policy", "academic-integrity",
    "citation-format", "citation-missing-info"
}
ENUM_SEVERITY = {"fatal", "major", "minor"}
ENUM_SCOPE = {"document", "chapter", "paragraph", "sentence", "span"}
ID_PATTERN = re.compile(r"^(thesis|citation)-([a-z-]+)-\d{3}$")
GROUP_ID_PATTERN = re.compile(r"^g-\d{3,}$")

SEVERITY_ORDER = {"fatal": 0, "major": 1, "minor": 2}


def _utr_tool_path(name: str) -> str:
    return os.path.join(UTR_BUNDLE, "unified-thesis-reviewer", "tools", name)


# ---------------------------------------------------------------------------
# 底层读取函数
# ---------------------------------------------------------------------------
def _read_text(file_path: str) -> dict:
    """从本地路径或公网 URL 读取文本。"""
    try:
        path = file_path.strip()
        if path.startswith("http://") or path.startswith("https://"):
            import requests
            resp = requests.get(path, timeout=30)
            if resp.status_code == 200:
                content = resp.text
            else:
                return {"success": False, "error": f"URL 返回状态码 {resp.status_code}"}
            fmt = "docx" if path.endswith(".docx") else \
                  "pdf" if path.endswith(".pdf") else \
                  "txt" if path.endswith(".txt") else \
                  "md" if path.endswith(".md") else "txt"
            return {"success": True, "text": content, "format": fmt,
                    "file_name": path.split("/")[-1]}

        if path.startswith("file_") and not os.path.exists(path):
            try:
                import coze_coding_utils.file as cf_mod  # type: ignore[import]
                coze_file = getattr(cf_mod, "file", cf_mod)
                # type: ignore[union-attr] — LSP 无法推断 get_file 动态属性，运行时存在
                fpath = coze_file.get_file(path).file_path  # type: ignore[union-attr]
                path = fpath
            except Exception:
                return {"success": False, "error": f"无法通过平台 file_id 获取文件: {path}"}

        if not os.path.exists(path):
            return {"success": False, "error": f"文件不存在: {path}"}

        ext = os.path.splitext(path)[1].lower()

        if ext == ".docx":
            import subprocess
            extract_script = _utr_tool_path("extract-docx.py")
            tmp_out = None
            if os.path.exists(extract_script):
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt", delete=False) as tf:
                    tmp_out = tf.name
                try:
                    proc = subprocess.run(
                        [sys.executable, extract_script, path, tmp_out],
                        capture_output=True, text=True, timeout=120
                    )
                    if proc.returncode == 0 and os.path.exists(tmp_out):
                        with open(tmp_out, encoding="utf-8") as f:
                            text = f.read()
                        os.unlink(tmp_out)
                        return {"success": True, "text": text, "format": "docx",
                                "file_name": os.path.basename(path),
                                "total_chars": len(text),
                                "text_preview": text[:5000] if text else "",
                                "full_text_available": True}
                    # else fall through to python-docx
                finally:
                    if tmp_out and os.path.exists(tmp_out):
                        try:
                            os.unlink(tmp_out)
                        except Exception:
                            pass
                # Fall through to python-docx fallback
            from docx import Document
            doc = Document(path)
            # 遍历所有 Run，捕获完整段落文本（含样式中的文本）
            W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            def full_para_text(p_el):
                parts = []
                for child in p_el.iter():
                    tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
                    if tag == "t" and child.text:
                        parts.append(child.text)
                    elif tag == "tab":
                        parts.append("\t")
                return "".join(parts)
            body_el = doc.element.body  # type: ignore[attr-defined]
            paragraphs = [full_para_text(p_el) for p_el in body_el.iter(f"{{{W_NS}}}p")]
            paragraphs = [p for p in paragraphs if p.strip()]
            text = "\n".join(paragraphs)
            return {"success": True, "text": text, "format": "docx",
                    "file_name": os.path.basename(path),
                    "total_chars": len(text),
                    "text_preview": text[:5000] if text else "",
                    "full_text_available": True}

        if ext == ".pdf":
            try:
                import fitz
                doc = fitz.open(path)
                if doc.is_encrypted:
                    doc.close()
                    return {"success": False, "error": "PDF 已加密", "is_encrypted": True}
                total_chars = sum(len(page.get_text()) for page in doc)
                if total_chars == 0:
                    doc.close()
                    return {"success": False, "error": "PDF 为扫描件或图片型 PDF，无法提取文本。请换用 Word 版本或 OCR 后再试。", "is_scanned": True}
                texts = []
                for i, page in enumerate(doc):
                    texts.append(f"--- Page {i + 1} ---\n{page.get_text()}")
                doc.close()
                return {"success": True, "text": "\n".join(texts), "format": "pdf",
                        "page_count": len(texts), "total_chars": total_chars,
                        "file_name": os.path.basename(path)}
            except ImportError:
                return {"success": False, "error": "PyMuPDF (fitz) 未安装，无法读取 PDF"}

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return {"success": True, "text": content, "format": ext.lstrip("."),
                "file_name": os.path.basename(path)}
    except Exception as e:
        return {"success": False, "error": f"文件读取失败: {str(e)}"}


# ---------------------------------------------------------------------------
# @tool 函数
# ---------------------------------------------------------------------------
@tool
def read_paper_file(file_path: str) -> str:
    """读取论文文件内容。file_path 可以是本地路径、公网 URL 或 Coze 平台 file_id。"""
    result = _read_text(file_path)
    if not result["success"]:
        return json.dumps(result, ensure_ascii=False)
    paper_text = result["text"]
    # 预览取前 5000 字符（足够预览又不会过大）
    preview = paper_text[:5000]
    # 注意：full_paper_text 放在 text_preview 之前（避免 Agent 输出截断导致丢失）
    return json.dumps({
        "success": True,
        "format": result.get("format"),
        "file_name": result.get("file_name"),
        "total_chars": len(paper_text),
        "full_paper_text": paper_text,
        "text_preview": preview,
        "full_text_available": True
    }, ensure_ascii=False)


@tool
def normalize_issues_for_annotation(analysis_result: str, paper_text: str) -> str:
    """
    将模型审查分析文本转换为严格 UTR schema issues.json。
    
    输入：analysis_result（Markdown 审查文本）、paper_text（论文全文文本）
    输出：{"schema_version": "1.0", "issues": [...]} 格式的 JSON 字符串。
    每条 issue 必须符合：
    - id: thesis-{category}-001 或 citation-{category}-001
    - source: thesis/citation
    - category: structure/argumentation/literature-review/empirical/legal-norms/language/policy/academic-integrity/citation-format/citation-missing-info
    - severity: fatal/major/minor（中文"致命"->fatal, "严重/重要"->major, "建议/轻微"->minor）
    - scope: document/chapter/paragraph/sentence/span
    - locator: {chapter: "非空字符串", paragraph_index: 整数}
    - excerpt: ≤60码点，scope=document/chapter时必须为空字符串
    - anchor_text: ≤60码点，用于docx批注定位
    - problem: 1-200码点非空
    - suggestion: 字符串数组，长度1-5，每条1-500码点
    - group_id: g-001格式
    
    质量门禁：
    - issue总数必须 >= 8
    - fatal+major 实质性 issue 必须 >= 5
    - 本科论文实质性(fatal+major)占比 >= 50%
    - 若不符合，在返回的JSON中包含 "quality_gate_passed": false
    """
    issues = []
    group_counter = 1
    gid_map = {}

    def _next_gid(src: str, cat: str, chapter: str, pidx: int) -> str:
        nonlocal group_counter
        key = (src, cat, chapter, pidx)
        if key not in gid_map:
            gid_map[key] = f"g-{group_counter:03d}"
            group_counter += 1
        return gid_map[key]

    lines = analysis_result.strip().split("\n")
    current = None
    severity_map = {
        "致命": "fatal", "严重": "major", "重大": "major", "重要": "major",
        "中等": "minor", "一般": "minor", "建议": "minor", "轻微": "minor"
    }
    category_map = {
        "选题": "structure", "结构": "structure", "章节": "structure",
        "论证": "argumentation", "论点": "argumentation", "论据": "argumentation",
        "文献": "literature-review", "综述": "literature-review",
        "实证": "empirical", "数据": "empirical", "案例": "empirical",
        "规范": "legal-norms", "法条": "legal-norms", "法律": "legal-norms",
        "语言": "language", "表达": "language", "术语": "language",
        "对策": "policy", "建议": "policy",
        "学术不端": "academic-integrity", "引用": "citation-format",
        "引注": "citation-format", "脚注": "citation-format", "参考文献": "citation-missing-info"
    }

    def _infer_category(text: str) -> str:
        """从文本推断 category。优先从方括号标签提取，其次从内容关键词推断。"""
        text_lower = text.lower()
        # 1. 先从方括号标签提取 category（取第二个方括号，第一个是 severity）
        bracket_tags = re.findall(r'\[([^\]]+)\]', text)
        category_tag_map = {
            "结构": "structure", "选题": "structure", "章节": "structure",
            "论证": "argumentation", "论点": "argumentation", "论据": "argumentation",
            "文献": "literature-review", "综述": "literature-review",
            "实证": "empirical", "数据": "empirical", "案例": "empirical",
            "规范": "legal-norms", "法条": "legal-norms", "法律": "legal-norms",
            "语言": "language", "表达": "language", "术语": "language",
            "对策": "policy",
            "学术不端": "academic-integrity",
            "引注": "citation-format", "引用": "citation-format",
            "脚注": "citation-format", "参考文献": "citation-missing-info",
            "引注格式": "citation-format", "citation": "citation-format",
        }
        for tag in bracket_tags:
            tag_lower = tag.lower().strip()
            if tag_lower in category_tag_map:
                return category_tag_map[tag_lower]
        # 2. 兜底：从内容关键词推断（排除 severity 关键词）
        # 去掉方括号标签后再推断
        content = re.sub(r'\[[^\]]+\]', '', text_lower)
        for key, val in category_map.items():
            # 跳过仅作为 severity 使用的关键词
            if key in ("建议", "轻微"):
                continue
            if key in content:
                return val
        if "引" in content or "注" in content or " footnote" in text_lower or "reference" in text_lower:
            return "citation-format"
        return "argumentation"

    def _infer_severity(text: str) -> str:
        for key, val in severity_map.items():
            if key in text:
                return val
        return "minor"

    def _find_anchor(text: str, paper: str) -> str:
        """在论文文本中寻找可定位的 anchor_text，返回前60码点。
        
        策略（按优先级）：
        1. 引号内原文引用（LLM 在分析中直接引用论文原文）
        2. 6-50字的中文句子片段
        3. 4-8字的关键词搜索
        4. 兜底：描述前40字（inject-docx 仍可定位）
        """
        if not paper or not text:
            return text[:60] if text else ""
        
        candidates = []
        # 1. 引号内原文引用（最高优先级）
        for m in re.finditer(r'["""](.{4,60}?)["""]', text):
            candidates.append((len(m.group(1)), m.group(1).strip(), 3))
        # 2. 中文句子片段
        for m in re.finditer(r'[\u4e00-\u9fff]{6,50}', text):
            candidates.append((len(m.group()), m.group(), 1))
        
        # 去重并按优先级+长度排序
        seen, unique = {}, []
        for length, cand, priority in candidates:
            if cand not in seen:
                seen[cand] = True
                unique.append((length, cand, priority))
        unique.sort(key=lambda x: (-x[2], -x[0]))  # 优先级高 → 长度长优先
        
        stop_words = {"全文", "本文", "论文", "该文", "该论文", "建议", "问题", "内容", "相关",
                       "主要", "重要", "严重", "轻微", "具体", "有关", "研究", "分析"}
        for _, cand, _ in unique:
            if cand in stop_words:
                continue
            if cand in paper:
                return cand[:60]
        
        # 降级：搜索关键词
        words = re.findall(r'[\u4e00-\u9fff]{4,8}', text)
        for w in sorted(set(words), key=len, reverse=True):
            if w not in stop_words and w in paper:
                return w[:60]
        
        return text.strip()[:40][:60]

    def _infer_chapter(text: str, paper: str) -> str:
        """推断章节标题。"""
        # 从文本中找 "第X章" 或 "X、" 开头的章节名
        m = re.search(r'(第[一二三四五六七八九十\d]+章[^\n，。]{0,20}|\d+[、\.][^\n，。]{0,20})', text)
        if m:
            ch = m.group(1).strip()
            if ch in paper:
                return ch[:60]
        # 默认章节
        return "正文"

    def _infer_paragraph_index(text: str, paper: str) -> int:
        """推断段落索引，默认 0。"""
        # 如果能找到 anchor_text 在论文中的位置，估算 paragraph_index
        anchor = _find_anchor(text, paper)
        if anchor and anchor in paper:
            before = paper[:paper.index(anchor)]
            return before.count('\n') // 2  # 粗略估算
        return 0

    for line in lines:
        line = line.strip()
        if not line:
            if current:
                issues.append(current)
                current = None
            continue
        # 匹配 issue 标题行，如 "1. [严重][论证] 论证不严谨"
        if re.match(r'^\d+[\.\)\s]', line[:5]):
            if current:
                issues.append(current)
            sev = _infer_severity(line)
            cat = _infer_category(line)
            src = "citation" if cat in ("citation-format", "citation-missing-info") else "thesis"
            # 从行中提取描述
            desc = re.sub(r'^\d+[\.\)\s]+', '', line)
            desc = re.sub(r'\[[^\]]+\]', '', desc).strip()
            # 推断 scope
            if cat in ("structure",):
                scope = "chapter"
            elif cat in ("argumentation", "literature-review", "empirical", "legal-norms", "language", "policy"):
                scope = "paragraph"
            else:
                scope = "sentence"
            _ch = _infer_chapter(desc, paper_text)
            _pidx = _infer_paragraph_index(desc, paper_text)
            _idx = len(issues)
            _anchor = _find_anchor(desc, paper_text)
            if _anchor and len(_anchor) > 60:
                _anchor = _anchor[:60]
            current = {
                "id": f"{src}-{cat}-{len(issues)+1:03d}",
                "source": src,
                "category": cat,
                "severity": sev,
                "scope": scope,
                "locator": {
                    "chapter": _ch,
                    "paragraph_index": _pidx
                },
                "excerpt": "" if scope in ("document", "chapter") else (desc[:60] if len(desc) <= 60 else desc[:57] + "..."),
                "anchor_text": _anchor,
                "problem": desc[:200] if desc else "问题描述待补充",
                "suggestion": ["请根据具体上下文补充修改建议"],
                "group_id": _next_gid(src, cat, _ch, _pidx)
            }
        elif current:
            if line.startswith("证据：") or line.startswith("依据："):
                current["problem"] += " [原文核对] " + line[3:].strip()
            elif line.startswith("建议：") or line.startswith("修改："):
                sug = line[3:].strip()
                if sug:
                    current["suggestion"] = [sug[:500]]
            else:
                current["problem"] += " " + line
                if len(current["problem"]) > 200:
                    current["problem"] = current["problem"][:200]
    if current:
        issues.append(current)

    # 质量门禁
    total = len(issues)
    substantive = sum(1 for i in issues if i["severity"] in ("fatal", "major"))
    quality_passed = total >= 8 and substantive >= 5

    result = {
        "schema_version": "1.0",
        "issues": issues,
        "quality_gate": {
            "total_issues": total,
            "substantive_issues": substantive,
            "quality_gate_passed": quality_passed
        }
    }
    # 保存到固定路径，供 generate_markdown_report 自动读取
    try:
        with open("/tmp/last_issues.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def verify_fact(query: str) -> str:
    """联网核实事实性内容。query 可以是法律名称、法条、案例、统计数据等。"""
    try:
        import coze_coding_dev_sdk as ccds  # type: ignore[import]
        client = ccds.SearchClient()  # type: ignore[attr-defined]
        results = client.web_search(query, top_n=5)
        snippets = []
        for r in results.get("results", []):
            snippets.append(
                f"- [{r.get('title', '')}]({r.get('url', '')}): {r.get('snippet', '')[:200]}"
            )
        return json.dumps({
            "query": query,
            "verified": len(snippets) > 0,
            "sources": snippets[:3],
            "note": "联网核实仅供参考，请以权威来源为准。"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        tb = traceback.format_exc()
        return json.dumps({
            "query": query, "verified": False,
            "error": str(e), "traceback": tb, "sources": []
        }, ensure_ascii=False)


@tool
def generate_annotated_docx(docx_path: str, issues_json: str) -> str:
    """
    生成带批注的 .annotated.docx。
    docx_path 为原始 docx 路径，issues_json 为严格 UTR schema 的问题清单 JSON 字符串。
    注入前先校验 schema，成功后回读校验。
    """
    try:
        import subprocess
        inject_script = _utr_tool_path("inject-docx-comments.py")
        if not os.path.exists(inject_script):
            return json.dumps({
                "success": False,
                "error": f"批注注入脚本不存在: {inject_script}",
                "failed_function": "generate_annotated_docx",
                "failed_issue_id": None,
                "traceback": None,
                "stderr": None
            }, ensure_ascii=False)

        if not os.path.exists(docx_path):
            return json.dumps({
                "success": False,
                "error": f"原始 docx 文件不存在: {docx_path}",
                "failed_function": "generate_annotated_docx",
                "failed_issue_id": None,
                "traceback": None,
                "stderr": None
            }, ensure_ascii=False)

        # 解析并校验 issues_json
        issues_raw = json.loads(issues_json) if isinstance(issues_json, str) else issues_json
        if isinstance(issues_raw, list):
            issues_data = {"schema_version": "1.0", "issues": issues_raw}
        elif isinstance(issues_raw, dict) and "issues" in issues_raw:
            issues_data = issues_raw
        else:
            issues_data = {"schema_version": "1.0", "issues": []}

        # 先写入 debug.issues.json
        debug_path = docx_path.replace(".docx", ".debug.issues.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(issues_data, f, ensure_ascii=False, indent=2)

        # 调用 inject-docx-comments.py 的 validate
        # 通过导入方式复用
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "inject_docx_comments", inject_script
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            validation_errors = mod.validate_issues_json(issues_data)
            if validation_errors:
                return json.dumps({
                    "success": False,
                    "error": f"issues.json 校验失败: {'; '.join(validation_errors[:5])}",
                    "failed_function": "generate_annotated_docx",
                    "failed_issue_id": None,
                    "validation_errors": validation_errors,
                    "debug_issues_path": debug_path,
                    "traceback": None,
                    "stderr": None
                }, ensure_ascii=False)
        except Exception as ve:
            return json.dumps({
                "success": False,
                "error": f"校验模块加载失败: {str(ve)}",
                "failed_function": "generate_annotated_docx",
                "failed_issue_id": None,
                "traceback": traceback.format_exc(),
                "stderr": None
            }, ensure_ascii=False)

        # 写入临时 issues.json 供脚本读取
        temp_issues = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(issues_data, temp_issues, ensure_ascii=False, indent=2)
        temp_issues.close()

        out_path = docx_path.replace(".docx", ".annotated.docx")
        proc = subprocess.run(
            [sys.executable, inject_script, docx_path, temp_issues.name, out_path],
            capture_output=True, text=True, timeout=180
        )
        os.unlink(temp_issues.name)

        if proc.returncode != 0:
            return json.dumps({
                "success": False,
                "error": proc.stderr or "批注注入脚本返回非零退出码",
                "failed_function": "generate_annotated_docx",
                "failed_issue_id": None,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
                "debug_issues_path": debug_path,
                "traceback": None
            }, ensure_ascii=False)

        # 回读校验
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            with zipfile.ZipFile(out_path, "r") as z:
                ct = z.read("[Content_Types].xml")
                doc = z.read("word/document.xml")
                tree = ET.ElementTree(ET.fromstring(doc))
                root = tree.getroot()
                start_count = sum(1 for _ in root.iter(f"{W_NS}commentRangeStart"))
                end_count = sum(1 for _ in root.iter(f"{W_NS}commentRangeEnd"))
                ref_count = sum(1 for _ in root.iter(f"{W_NS}commentReference"))

                if "word/comments.xml" in z.namelist():
                    comments_xml = z.read("word/comments.xml")
                    ctree = ET.ElementTree(ET.fromstring(comments_xml))
                    croot = ctree.getroot()
                    comment_count = sum(1 for _ in croot.iter(f"{W_NS}comment"))
                else:
                    comment_count = 0

            match = (start_count == end_count == ref_count == comment_count)
            if not match:
                return json.dumps({
                    "success": False,
                    "error": f"回读校验失败: commentRangeStart={start_count}, commentRangeEnd={end_count}, "
                             f"commentReference={ref_count}, comments.xml={comment_count}",
                    "failed_function": "generate_annotated_docx",
                    "failed_issue_id": None,
                    "readback": {
                        "commentRangeStart": start_count,
                        "commentRangeEnd": end_count,
                        "commentReference": ref_count,
                        "comments_xml": comment_count
                    },
                    "debug_issues_path": debug_path,
                    "traceback": None
                }, ensure_ascii=False)

            # 上传到对象存储并返回下载 URL
            download_url = None
            try:
                from coze_coding_dev_sdk.s3 import S3SyncStorage
                storage = S3SyncStorage(
                    endpoint_url=os.getenv("COZE_BUCKET_ENDPOINT_URL"),
                    bucket_name=os.getenv("COZE_BUCKET_NAME"),
                )
                # 生成唯一文件名（去除空格避免签名问题）
                import time
                base_name = os.path.basename(out_path).replace(" ", "_")
                unique_name = f"papers/{int(time.time())}_{base_name}"
                # 流式上传本地文件
                with open(out_path, "rb") as f:
                    file_key = storage.stream_upload_file(
                        fileobj=f,
                        file_name=unique_name,
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                # 生成预签名 URL（24小时有效期）
                download_url = storage.generate_presigned_url(
                    key=file_key,
                    expire_time=86400
                )
            except Exception as ue:
                import logging
                logging.warning(f"[S3Upload] 上传失败: {ue}")
                download_url = None

            if not download_url:
                download_url = f"本地路径（请从项目文件列表下载）: {out_path}"

            return json.dumps({
                "success": True,
                "download_url": download_url,
                "annotated_docx_path": out_path,
                "readback": {
                    "commentRangeStart": start_count,
                    "commentRangeEnd": end_count,
                    "commentReference": ref_count,
                    "comments_xml": comment_count,
                    "match": match
                },
                "debug_issues_path": debug_path,
                "output": proc.stdout
            }, ensure_ascii=False)
        except Exception as re:
            return json.dumps({
                "success": False,
                "error": f"回读校验异常: {str(re)}",
                "failed_function": "generate_annotated_docx",
                "failed_issue_id": None,
                "traceback": traceback.format_exc(),
                "debug_issues_path": debug_path
            }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "failed_function": "generate_annotated_docx",
            "failed_issue_id": None,
            "traceback": traceback.format_exc(),
            "stderr": None
        }, ensure_ascii=False)


@tool
def generate_markdown_report(analysis_result: str = "") -> str:
    """生成完整 Markdown 审查报告。
    
    若 analysis_result 为空，则自动从上次批注生成时保存的 issues.json 读取。
    issues.json 由 normalize_issues_for_annotation 在上一次调用时自动保存到固定路径。
    """
    # 尝试读取上次保存的 issues.json
    issues_json_str = ""
    issues_path = "/tmp/last_issues.json"
    try:
        if os.path.exists(issues_path):
            with open(issues_path, "r", encoding="utf-8") as f:
                issues_data = json.load(f)
            issues_json_str = json.dumps(issues_data, ensure_ascii=False)
    except Exception:
        pass

    # 如果调用者传了 issues_json 路径（从 analysis_result 中提取），则读取该文件
    if not issues_json_str:
        # 查找最近的 .debug.issues.json 文件
        import glob
        candidates = glob.glob("/workspace/projects/**/*.debug.issues.json", recursive=True) + \
                     glob.glob("/tmp/*.debug.issues.json") + \
                     glob.glob("/tmp/papers/*.debug.issues.json", recursive=True)
        if candidates:
            candidates.sort(key=os.path.getmtime, reverse=True)
            try:
                with open(candidates[0], "r", encoding="utf-8") as f:
                    issues_data = json.load(f)
                issues_json_str = json.dumps(issues_data, ensure_ascii=False)
            except Exception:
                pass

    # 如果仍然没有 issues_json，给出清晰提示
    if not issues_json_str and not analysis_result:
        return json.dumps({
            "success": False,
            "error": "缺少分析文本和 issues_json。请在 analysis_result 中传入审查分析文本，或先运行 normalize_issues_for_annotation 生成 issues.json。",
            "suggestion": "先调用 normalize_issues_for_annotation(analysis_result, paper_text)，再调用 generate_markdown_report(analysis_result, issues_json)"
        }, ensure_ascii=False)

    if not analysis_result:
        analysis_result = "# 论文质检审查报告\n\n（请查阅完整审查报告）"

    try:
        from coze_coding_dev_sdk import DocumentGenerationClient
        report_md = f"""# 法学论文质检审查报告

## 一、审查概况

{analysis_result}

## 二、问题清单

```json
{issues_json_str}
```

## 三、说明

- 本报告由法学论文质检自查智能体自动生成
- 审查结果仅供参考，请以导师/评阅人意见为准
- fatal/major 问题建议优先处理
- 如有疑问，请与导师沟通确认
"""
        client = DocumentGenerationClient()
        pdf_url = client.create_pdf_from_markdown(report_md, "review_report")
        return json.dumps({"success": True, "pdf_url": pdf_url, "markdown": report_md}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False, "error": str(e),
            "traceback": traceback.format_exc(),
            "markdown": f"# 法学论文质检审查报告\n\n## 一、审查概况\n\n{analysis_result}\n\n## 二、问题清单\n\nissues.json 读取失败: {str(e)}\n\n## 三、说明\n\n- 本报告由法学论文质检自查智能体自动生成\n- 审查结果仅供参考，请以导师/评阅人意见为准"
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 错误处理中间件
# ---------------------------------------------------------------------------
@wrap_tool_call
def handle_tool_errors(request, handler):
    try:
        return handler(request)
    except Exception as e:
        tb = traceback.format_exc()
        return ToolMessage(
            content=f"工具执行错误 ({request.tool_call.get('name', 'unknown')}): {str(e)}\n\n{tb}",
            tool_call_id=request.tool_call["id"]
        )


# ---------------------------------------------------------------------------
# Agent 构建
# ---------------------------------------------------------------------------
def build_agent(ctx=None):
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")

    llm = ChatOpenAI(
        model=cfg["config"]["model"],
        api_key=api_key,
        base_url=base_url,
        temperature=cfg["config"]["temperature"],
        streaming=True,
        timeout=cfg["config"]["timeout"],
        extra_body={"thinking": {"type": cfg["config"]["thinking"]}},
        default_headers=default_headers(ctx) if ctx else {}
    )

    tools = [
        read_paper_file,
        normalize_issues_for_annotation,
        verify_fact,
        generate_annotated_docx,
        generate_markdown_report,
    ]

    return create_agent(
        model=llm,
        system_prompt=cfg.get("sp", "You are a helpful assistant."),
        tools=tools,
        middleware=[handle_tool_errors],
        checkpointer=get_memory_saver(),
    )
