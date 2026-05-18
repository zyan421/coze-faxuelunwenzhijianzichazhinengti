"""
法学论文质检自查 Agent —— unified-thesis-reviewer 总编排器 (v3.0)

核心职责：
1. 接收论文文件（docx / pdf / txt / md）或 URL
2. 调用 read_paper_file 读取论文全文
3. LLM 直接在分析中输出 UTR schema issues JSON（无需额外 normalize 步骤）
4. 调用 generate_deliverables 一次性生成带批注 docx + PDF 报告

优化（v3.0 vs v2.0）：
- 删除 verify_fact：该工具每次联网搜索耗时5分钟×10+事实=50+分钟，是最大瓶颈
  LLM 自身知识足以识别法条引用是否规范，标注"需人工核实"即可
- 删除 normalize_issues_for_annotation：LLM 直接输出 UTR JSON，省去一次 LLM 往返
- 合并 generate_annotated_docx + generate_markdown_report → generate_deliverables：
  一次工具调用完成两个交付物，省去一次 LLM 往返
- 总 LLM 工具往返从 6+2N 降至 3（read → analysis → deliverables）

失败时暴露完整堆栈，禁止泛化话术。
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
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages  # noqa: F401 - kept for compatibility
from typing import Annotated
from coze_coding_utils.runtime_ctx.context import default_headers
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
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

SEVERITY_ORDER = {"fatal": 0, "major": 1, "minor": 2}


class AgentState(MessagesState):
    messages: Annotated[list[AnyMessage], add_messages]


def _utr_tool_path(name: str) -> str:
    return os.path.join(UTR_BUNDLE, "unified-thesis-reviewer", "tools", name)


def _resolve_local_docx_path(file_path: str) -> str:
    """将 URL / file_id / 本地路径统一解析为本地 .docx 二进制文件路径。"""
    path = file_path.strip()

    # URL → 下载二进制文件到 /tmp
    if path.startswith("http://") or path.startswith("https://"):
        import requests
        resp = requests.get(path, timeout=60)
        if resp.status_code != 200:
            raise FileNotFoundError(f"URL 下载失败，状态码 {resp.status_code}: {path}")
        fname = path.split("/")[-1].split("?")[0] or "downloaded.docx"
        if not fname.endswith(".docx"):
            fname += ".docx"
        tmp_path = os.path.join("/tmp", f"utr_{int(__import__('time').time())}_{fname}")
        with open(tmp_path, "wb") as f:
            f.write(resp.content)
        return tmp_path

    # Coze file_id → 解析为本地路径
    if path.startswith("file_") and not os.path.exists(path):
        try:
            import coze_coding_utils.file as cf_mod
            coze_file = getattr(cf_mod, "file", cf_mod)
            resolved = coze_file.get_file(path).file_path  # type: ignore[union-attr]
            return resolved
        except Exception:
            pass

    # 本地路径直接返回
    return path


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
                import coze_coding_utils.file as cf_mod
                coze_file = getattr(cf_mod, "file", cf_mod)
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
                finally:
                    if tmp_out and os.path.exists(tmp_out):
                        try:
                            os.unlink(tmp_out)
                        except Exception:
                            pass
            # Fall through to python-docx fallback
            from docx import Document
            doc = Document(path)
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
    # 先尝试解析本地路径（供后续 generate_deliverables 使用）
    local_docx_path = ""
    try:
        resolved = _resolve_local_docx_path(file_path)
        if resolved and os.path.exists(resolved):
            local_docx_path = resolved
    except Exception:
        pass

    result = _read_text(file_path)
    if not result["success"]:
        return json.dumps(result, ensure_ascii=False)
    paper_text = result["text"]
    preview = paper_text[:5000]
    return json.dumps({
        "success": True,
        "format": result.get("format"),
        "file_name": result.get("file_name"),
        "total_chars": len(paper_text),
        "local_docx_path": local_docx_path,
        "full_paper_text": paper_text,
        "text_preview": preview,
        "full_text_available": True
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# UTR Schema 验证与修补（内联，不需要单独工具）
# ---------------------------------------------------------------------------
ID_PATTERN = re.compile(r"^(thesis|citation)-([a-z-]+)-\d{3}$")
GROUP_ID_PATTERN = re.compile(r"^g-\d{3,}$")

# 封面页/模板文本黑名单
_COVER_BLACKLIST = {
    "湘潭大学", "毕业论文", "学士学位论文", "硕士论文", "博士论文",
    "学号", "指导教师", "学院", "法学学部", "Faculty of Law",
    "Xiangtan University", "本科", "课程论文", "原创性声明",
    "版权使用授权书", "答辩委员会", "摘要", "Abstract", "关键词",
    "Keywords", "目录", "参考文献", "致谢", "附录",
}


def _validate_and_fix_issues(issues_data: dict, paper_text: str) -> dict:
    """验证并修补 UTR issues JSON，确保 schema 合规。"""
    issues = issues_data.get("issues", [])
    if not issues:
        return issues_data

    fixed_issues = []
    group_counter = 1
    gid_map = {}

    def _next_gid(src, cat, chapter, pidx):
        nonlocal group_counter
        key = (src, cat, chapter, pidx)
        if key not in gid_map:
            gid_map[key] = f"g-{group_counter:03d}"
            group_counter += 1
        return gid_map[key]

    for i, issue in enumerate(issues):
        # Fix id
        src = issue.get("source", "thesis")
        cat = issue.get("category", "argumentation")
        if src not in ENUM_SOURCE:
            src = "thesis"
        if cat not in ENUM_CATEGORY:
            cat = "argumentation"
        issue["id"] = f"{src}-{cat}-{i+1:03d}"
        issue["source"] = src
        issue["category"] = cat

        # Fix severity
        sev = issue.get("severity", "minor")
        if sev not in ENUM_SEVERITY:
            sev = "minor"
        issue["severity"] = sev

        # Fix scope
        scope = issue.get("scope", "paragraph")
        if scope not in ENUM_SCOPE:
            scope = "paragraph"
        issue["scope"] = scope

        # Fix locator
        locator = issue.get("locator", {})
        if not isinstance(locator, dict):
            locator = {}
        locator.setdefault("chapter", "正文")
        locator.setdefault("paragraph_index", 0)
        issue["locator"] = locator

        # Fix excerpt (scope=document/chapter时必须为空)
        excerpt = issue.get("excerpt", "")
        if scope in ("document", "chapter"):
            excerpt = ""
        elif not excerpt and len(paper_text) > 0:
            # 尝试从问题中提取可能的原文片段
            anchor = issue.get("anchor_text", "")
            excerpt = anchor[:60] if anchor else ""
        if len(excerpt) > 60:
            excerpt = excerpt[:57] + "..."
        issue["excerpt"] = excerpt

        # Fix anchor_text - 确保是论文原文而非概括
        anchor_text = issue.get("anchor_text", "")
        if anchor_text and paper_text:
            # 验证 anchor_text 是否在论文中出现
            if anchor_text not in paper_text:
                # 尝试找最相似的片段
                anchor_text = _find_anchor_in_text(issue.get("problem", ""), paper_text)
        elif not anchor_text and paper_text:
            anchor_text = _find_anchor_in_text(issue.get("problem", ""), paper_text)
        if len(anchor_text) > 60:
            anchor_text = anchor_text[:60]
        issue["anchor_text"] = anchor_text

        # Fix problem
        problem = issue.get("problem", "")
        if not problem or not problem.strip():
            problem = "问题描述待补充"
        if len(problem) > 200:
            problem = problem[:200]
        issue["problem"] = problem

        # Fix suggestion
        suggestion = issue.get("suggestion", [])
        if not isinstance(suggestion, list):
            suggestion = [str(suggestion)]
        if not suggestion:
            suggestion = ["请根据具体上下文补充修改建议"]
        suggestion = [str(s)[:500] for s in suggestion[:5]]
        issue["suggestion"] = suggestion

        # Fix group_id
        gid = issue.get("group_id", "")
        if not GROUP_ID_PATTERN.match(gid):
            gid = _next_gid(src, cat, locator.get("chapter", "正文"), locator.get("paragraph_index", 0))
        issue["group_id"] = gid

        fixed_issues.append(issue)

    issues_data["issues"] = fixed_issues
    # 质量门禁
    total = len(fixed_issues)
    substantive = sum(1 for i in fixed_issues if i["severity"] in ("fatal", "major"))
    issues_data.setdefault("quality_gate", {
        "total_issues": total,
        "substantive_issues": substantive,
        "quality_gate_passed": total >= 8 and substantive >= 5
    })
    issues_data["quality_gate"]["total_issues"] = total
    issues_data["quality_gate"]["substantive_issues"] = substantive
    issues_data["quality_gate"]["quality_gate_passed"] = total >= 8 and substantive >= 5

    return issues_data


def _find_anchor_in_text(text: str, paper: str) -> str:
    """在论文正文中寻找可定位的 anchor_text，返回前60码点。"""
    if not paper or not text:
        return text[:60] if text else ""

    # 找到正文起始位置（跳过封面/目录等前置部分）
    body_start = 0
    for marker in [r'第[一1]章', r'绪\s*论', r'引\s*言', r'一、']:
        m = re.search(marker, paper)
        if m:
            body_start = max(body_start, m.start())
            break
    if body_start == 0 and len(paper) > 2000:
        body_start = int(len(paper) * 0.15)

    body_paper = paper[body_start:]

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
    unique.sort(key=lambda x: (-x[2], -x[0]))

    stop_words = {"全文", "本文", "论文", "该文", "该论文", "建议", "问题", "内容", "相关",
                   "主要", "重要", "严重", "轻微", "具体", "有关", "研究", "分析",
                   "湘潭大学", "毕业论文", "学士学位", "学号", "指导教师"}
    for _, cand, _ in unique:
        if cand in stop_words:
            continue
        if cand in body_paper and not any(bw in cand for bw in _COVER_BLACKLIST if len(bw) <= len(cand)):
            return cand[:60]

    # 降级：在正文区域搜索关键词
    words = re.findall(r'[\u4e00-\u9fff]{4,8}', text)
    for w in sorted(set(words), key=len, reverse=True):
        if w not in stop_words and w in body_paper and not any(bw in w for bw in _COVER_BLACKLIST if len(bw) <= len(w)):
            return w[:60]

    return text.strip()[:40][:60]


@tool
def generate_deliverables(docx_path: str, issues_json: str, analysis_summary: str = "") -> str:
    """一次性生成全部交付物：带批注的 .annotated.docx + PDF 审查报告。

    参数：
    - docx_path: read_paper_file 返回的 local_docx_path 字段
    - issues_json: 完整 UTR schema JSON 字符串，格式 {"schema_version":"1.0","issues":[...]}
    - analysis_summary: 审查分析摘要文本（用于生成报告）

    返回 JSON 包含 annotated_docx_url 和 pdf_url 两个下载链接。
    """
    results = {
        "annotated_docx": {"success": False, "url": None, "error": None},
        "pdf_report": {"success": False, "url": None, "error": None},
    }

    # ── Step 1: 解析并验证 issues_json ──
    raw_str = issues_json if isinstance(issues_json, str) else json.dumps(issues_json, ensure_ascii=False)
    try:
        issues_raw = json.loads(raw_str)
    except json.JSONDecodeError:
        # 尝试修复常见问题
        fixed = raw_str
        for truncate_marker in ['],', '},', '",', 'null,', 'true,', 'false,']:
            idx = fixed.rfind(truncate_marker)
            if idx > 0:
                fixed = fixed[:idx + len(truncate_marker)]
                break
        fixed = re.sub(r',?\s*"[^"]*"\s*:\s*"[^"]*$', '', fixed)
        fixed = re.sub(r',?\s*"[^"]*"\s*:\s*\S+$', '', fixed)
        open_b = fixed.count('{') + fixed.count('[')
        close_b = fixed.count('}') + fixed.count(']')
        for _ in range(open_b - close_b):
            fixed += '}' if fixed.rstrip().endswith(']') or fixed.rstrip().endswith('}') else ']'
        try:
            issues_raw = json.loads(fixed)
        except json.JSONDecodeError as je2:
            return json.dumps({
                "success": False,
                "error": f"issues_json 解析失败: {str(je2)}",
                "hint": "请确保传入的 issues_json 是合法 JSON",
                "raw_preview": raw_str[:200],
            }, ensure_ascii=False)

    if isinstance(issues_raw, list):
        issues_data = {"schema_version": "1.0", "issues": issues_raw}
    elif isinstance(issues_raw, dict) and "issues" in issues_raw:
        issues_data = issues_raw
    else:
        issues_data = {"schema_version": "1.0", "issues": []}

    # 读取论文全文（用于验证和修补 anchor_text）
    paper_text = ""
    try:
        if docx_path and os.path.exists(docx_path):
            read_result = _read_text(docx_path)
            if read_result.get("success"):
                paper_text = read_result.get("text", "")
    except Exception:
        pass

    # 验证并修补 issues
    issues_data = _validate_and_fix_issues(issues_data, paper_text)

    # 保存 issues.json 供后续使用
    try:
        with open("/tmp/last_issues.json", "w", encoding="utf-8") as f:
            json.dump(issues_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # ── Step 2: 生成带批注的 docx ──
    if docx_path:
        try:
            import subprocess
            inject_script = _utr_tool_path("inject-docx-comments.py")

            if os.path.exists(inject_script):
                # 解析路径
                try:
                    resolved_path = _resolve_local_docx_path(docx_path)
                except FileNotFoundError as fe:
                    results["annotated_docx"]["error"] = f"原始 docx 文件无法获取: {str(fe)}"
                    resolved_path = None

                if resolved_path and os.path.exists(resolved_path):
                    # 写入临时 issues.json
                    debug_path = resolved_path.replace(".docx", ".debug.issues.json")
                    with open(debug_path, "w", encoding="utf-8") as f:
                        json.dump(issues_data, f, ensure_ascii=False, indent=2)

                    # 调用 inject-docx-comments.py 的 validate
                    validation_ok = True
                    try:
                        import importlib.util
                        spec = importlib.util.spec_from_file_location("inject_docx_comments", inject_script)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        validation_errors = mod.validate_issues_json(issues_data)
                        if validation_errors:
                            validation_ok = False
                            results["annotated_docx"]["error"] = f"issues.json 校验失败: {'; '.join(validation_errors[:5])}"
                    except Exception:
                        pass  # 跳过校验，直接生成

                    if validation_ok:
                        temp_issues = tempfile.NamedTemporaryFile(
                            mode="w", suffix=".json", delete=False, encoding="utf-8"
                        )
                        json.dump(issues_data, temp_issues, ensure_ascii=False, indent=2)
                        temp_issues.close()

                        out_path = resolved_path.replace(".docx", ".annotated.docx")
                        proc = subprocess.run(
                            [sys.executable, inject_script, resolved_path, temp_issues.name, out_path],
                            capture_output=True, text=True, timeout=180
                        )
                        os.unlink(temp_issues.name)

                        if proc.returncode == 0:
                            # 上传到对象存储
                            download_url = _upload_to_s3(out_path, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                            if download_url:
                                results["annotated_docx"] = {"success": True, "url": download_url}
                            else:
                                results["annotated_docx"] = {"success": True, "url": out_path, "note": "本地路径，请从项目文件列表下载"}
                        else:
                            results["annotated_docx"]["error"] = proc.stderr or "批注注入脚本返回非零退出码"
                else:
                    if not results["annotated_docx"]["error"]:
                        results["annotated_docx"]["error"] = f"docx 文件不存在: {docx_path}"
            else:
                results["annotated_docx"]["error"] = f"批注注入脚本不存在: {inject_script}"
        except Exception as e:
            results["annotated_docx"]["error"] = f"生成批注文档异常: {str(e)}"
    else:
        results["annotated_docx"]["error"] = "未提供 docx_path，跳过批注文档生成"

    # ── Step 3: 生成 PDF 审查报告 ──
    try:
        from coze_coding_dev_sdk import DocumentGenerationClient

        issues_list = issues_data.get("issues", [])
        sorted_issues = sorted(issues_list, key=lambda x: SEVERITY_ORDER.get(x.get("severity", "minor"), 2))
        fatal_count = sum(1 for i in issues_list if i.get("severity") == "fatal")
        major_count = sum(1 for i in issues_list if i.get("severity") == "major")
        minor_count = sum(1 for i in issues_list if i.get("severity") == "minor")

        issues_detail = ""
        if issues_list:
            issues_detail = f"共 **{len(issues_list)}** 项问题：致命 {fatal_count} 项 / 严重 {major_count} 项 / 轻微 {minor_count} 项\n\n"
            for idx, issue in enumerate(sorted_issues, 1):
                sev = issue.get("severity", "minor")
                sev_label = {"fatal": "❌ 致命", "major": "⚠️ 严重", "minor": "💡 轻微"}.get(sev, "💡 轻微")
                cat = issue.get("category", "unknown")
                cat_label = {
                    "structure": "结构逻辑", "argumentation": "论证严谨性",
                    "literature-review": "文献综述", "empirical": "实证分析",
                    "legal-norms": "规范适用", "language": "语言表达",
                    "policy": "政策建议", "academic-integrity": "学术诚信",
                    "citation-format": "引注格式", "citation-missing-info": "引注缺失"
                }.get(cat, cat)
                problem = issue.get("problem", "（未描述）").replace("|", "｜").replace("\n", " ")
                suggestions = issue.get("suggestion", [])
                sug_text = "；".join(suggestions[:3]).replace("\n", " ") if suggestions else "请根据上下文修改"
                excerpt = issue.get("excerpt", "")[:80].replace("\n", " ")

                issues_detail += f"### {idx}. {sev_label} 【{cat_label}】\n\n"
                issues_detail += f"**问题**：{problem}\n\n"
                if excerpt:
                    issues_detail += f"**原文**：…{excerpt}…\n\n"
                issues_detail += f"**建议**：{sug_text}\n\n---\n\n"
        else:
            issues_detail = "> 问题清单数据暂不可用，请参考审查分析内容。\n"

        summary_text = analysis_summary or "（详细审查分析请见对话记录）"

        report_md = f"""# 法学论文质检审查报告

---

## 审查概况

{summary_text}

---

## 问题清单

{issues_detail}

## 使用说明

- 本报告由法学论文质检自查智能体自动生成
- 审查结果仅供参考，请以导师/评阅人意见为准
- 致命/严重问题建议优先处理
- 如有疑问，请与导师沟通确认
"""
        client = DocumentGenerationClient()
        pdf_url = client.create_pdf_from_markdown(report_md, "review_report")
        results["pdf_report"] = {"success": True, "url": pdf_url}
    except Exception as e:
        results["pdf_report"]["error"] = f"PDF报告生成失败: {str(e)}"

    # ── 构建最终返回 ──
    return json.dumps({
        "success": results["annotated_docx"]["success"] or results["pdf_report"]["success"],
        "annotated_docx_url": results["annotated_docx"].get("url"),
        "pdf_url": results["pdf_report"].get("url"),
        "annotated_docx_error": results["annotated_docx"].get("error"),
        "pdf_error": results["pdf_report"].get("error"),
        "total_issues": len(issues_data.get("issues", [])),
        "quality_gate": issues_data.get("quality_gate", {}),
    }, ensure_ascii=False)


def _upload_to_s3(file_path: str, content_type: str) -> Optional[str]:
    """上传文件到对象存储，返回预签名 URL。失败返回 None。"""
    try:
        from coze_coding_dev_sdk.s3 import S3SyncStorage
        import time
        storage = S3SyncStorage(
            endpoint_url=os.getenv("COZE_BUCKET_ENDPOINT_URL"),
            bucket_name=os.getenv("COZE_BUCKET_NAME"),
        )
        base_name = os.path.basename(file_path).replace(" ", "_")
        unique_name = f"papers/{int(time.time())}_{base_name}"
        with open(file_path, "rb") as f:
            file_key = storage.stream_upload_file(
                fileobj=f,
                file_name=unique_name,
                content_type=content_type
            )
        download_url = storage.generate_presigned_url(
            key=file_key,
            expire_time=86400
        )
        return download_url
    except Exception as e:
        import logging
        logging.warning(f"[S3Upload] 上传失败: {e}")
        return None


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

    # 优先使用用户自定义模型配置（BYOK: Bring Your Own Key）
    custom = cfg.get("custom_model", {})
    api_key = custom.get("api_key") or os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = custom.get("base_url") or os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
    model_name = custom.get("model") or cfg["config"]["model"]

    thinking_cfg = cfg["config"].get("thinking", "disabled")

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=cfg["config"]["temperature"],
        max_completion_tokens=cfg["config"].get("max_completion_tokens", 32768),
        streaming=True,
        timeout=cfg["config"]["timeout"],
        extra_body={
            "thinking": {
                "type": "enabled" if thinking_cfg == "enabled" else "disabled"
            }
        },
        default_headers=default_headers(ctx) if ctx else {}
    )

    tools = [
        read_paper_file,
        generate_deliverables,
    ]

    return create_agent(
        model=llm,
        system_prompt=cfg.get("sp", "You are a helpful assistant."),
        tools=tools,
        middleware=[handle_tool_errors],
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )
