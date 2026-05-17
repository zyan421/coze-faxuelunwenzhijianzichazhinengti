"""
法学论文质检自查 Agent —— unified-thesis-reviewer 总编排器

核心职责：
1. 接收论文文件（docx / pdf / txt / md）或 URL
2. 调用 UTR 工具链执行一站式审查
3. 生成 .annotated.docx 或 .annotated.pdf 批注文档
4. 返回 Markdown 总报告 + issues.json + 修改建议
"""

import os
import sys
import json
import tempfile
import traceback
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

def _utr_tool_path(name: str) -> str:
    return os.path.join(UTR_BUNDLE, "unified-thesis-reviewer", "tools", name)


# ---------------------------------------------------------------------------
# 底层读取函数（供 @tool 和非 @tool 共用）
# ---------------------------------------------------------------------------
def _read_text(file_path: str) -> dict:
    """从本地路径或公网 URL 读取文本。"""
    try:
        path = file_path.strip()
        # URL 下载
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
            return {"success": True, "text": content, "format": fmt, "file_name": path.split("/")[-1]}

        # Coze 平台 file_id（形如 file_xxx）
        if path.startswith("file_") and not os.path.exists(path):
            try:
                import coze_coding_utils.file as cf_mod  # type: ignore[import]  # LSP误报：运行时模块存在
                coze_file = getattr(cf_mod, "file", cf_mod)  # type: ignore[assignment]  # LSP误报：动态获取属性
                fpath = coze_file.get_file(path).file_path  # type: ignore[attr-defined]  # LSP误报：运行时方法存在
                path = fpath
            except Exception:
                return {"success": False, "error": f"无法通过平台 file_id 获取文件: {path}"}

        if not os.path.exists(path):
            return {"success": False, "error": f"文件不存在: {path}"}

        ext = os.path.splitext(path)[1].lower()

        # docx
        if ext == ".docx":
            import subprocess
            extract_script = _utr_tool_path("extract-docx.py")
            if os.path.exists(extract_script):
                proc = subprocess.run(
                    [sys.executable, extract_script, path, "-"],
                    capture_output=True, text=True, timeout=120
                )
                if proc.returncode == 0:
                    return {"success": True, "text": proc.stdout, "format": "docx", "file_name": os.path.basename(path)}
                else:
                    # fallback 到 python-docx
                    from docx import Document
                    doc = Document(path)
                    paragraphs = [p.text for p in doc.paragraphs]
                    return {"success": True, "text": "\n".join(paragraphs), "format": "docx", "file_name": os.path.basename(path)}
            else:
                from docx import Document
                doc = Document(path)
                paragraphs = [p.text for p in doc.paragraphs]
                return {"success": True, "text": "\n".join(paragraphs), "format": "docx", "file_name": os.path.basename(path)}

        # pdf
        if ext == ".pdf":
            try:
                import fitz
                doc = fitz.open(path)
                if doc.is_encrypted:
                    doc.close()
                    return {"success": False, "error": "PDF 已加密", "is_encrypted": True}
                total_chars = sum(len(page.get_text()) for page in doc)  # type: ignore
                if total_chars == 0:
                    doc.close()
                    return {"success": False, "error": "PDF 为扫描件或图片型 PDF，无法提取文本。请换用 Word 版本或 OCR 后再试。", "is_scanned": True}
                texts = []
                for i, page in enumerate(doc):
                    texts.append(f"--- Page {i + 1} ---\n{page.get_text()}")  # type: ignore
                doc.close()
                return {"success": True, "text": "\n".join(texts), "format": "pdf", "page_count": len(texts), "total_chars": total_chars, "file_name": os.path.basename(path)}
            except ImportError:
                return {"success": False, "error": "PyMuPDF (fitz) 未安装，无法读取 PDF"}

        # txt / md / 其他纯文本
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return {"success": True, "text": content, "format": ext.lstrip("."), "file_name": os.path.basename(path)}

    except Exception as e:
        return {"success": False, "error": f"文件读取失败: {str(e)}"}


# ---------------------------------------------------------------------------
# @tool 函数（Agent 可调用的工具）
# ---------------------------------------------------------------------------
@tool
def read_paper_file(file_path: str) -> str:
    """读取论文文件内容。file_path 可以是本地路径、公网 URL 或 Coze 平台 file_id。"""
    result = _read_text(file_path)
    if not result["success"]:
        return json.dumps(result, ensure_ascii=False)
    preview = result["text"][:3000]
    return json.dumps({
        "success": True,
        "format": result.get("format"),
        "file_name": result.get("file_name"),
        "total_chars": len(result["text"]),
        "text_preview": preview,
        "full_text_available": True
    }, ensure_ascii=False, indent=2)


@tool
def generate_issues_json(analysis_result: str) -> str:
    """根据审查分析结果，生成符合统一 issues-schema 的 JSON。
    analysis_result: 模型审查分析的 Markdown 文本。
    """
    issues = []
    lines = analysis_result.strip().split("\n")
    current_issue = None
    for line in lines:
        line = line.strip()
        if not line:
            if current_issue:
                issues.append(current_issue)
                current_issue = None
            continue
        # 匹配 "1. [严重][选题] 选题过大" 格式
        if line[0].isdigit() and "." in line[:5]:
            if current_issue:
                issues.append(current_issue)
            parts = line.split("]", 2)
            severity = "建议"
            dimension = "其他"
            description = line
            if len(parts) >= 2:
                sev_part = parts[0]
                if "致命" in sev_part:
                    severity = "致命"
                elif "严重" in sev_part or "重大" in sev_part:
                    severity = "严重"
                elif "中等" in sev_part or "一般" in sev_part:
                    severity = "中等"
                dim_part = parts[1] if len(parts) > 1 else ""
                if "[" in dim_part:
                    dimension = dim_part.split("[")[-1].split("]")[0] if "[" in dim_part else "其他"
                description = parts[-1].strip() if len(parts) > 2 else line
            current_issue = {
                "id": f"ISS-{len(issues) + 1:03d}",
                "severity": severity,
                "dimension": dimension,
                "description": description,
                "evidence": "",
                "suggestion": "",
                "anchor_text": "",
            }
        elif current_issue:
            if line.startswith("证据：") or line.startswith("依据："):
                current_issue["evidence"] = line[3:].strip()
            elif line.startswith("建议：") or line.startswith("修改："):
                current_issue["suggestion"] = line[3:].strip()
            else:
                current_issue["description"] += " " + line
    if current_issue:
        issues.append(current_issue)
    return json.dumps({"issues": issues}, ensure_ascii=False, indent=2)


@tool
def verify_fact(query: str) -> str:
    """联网核实事实性内容。query 可以是法律名称、法条、案例、统计数据等。"""
    try:
        import coze_coding_dev_sdk as ccds  # type: ignore[import]
        client = ccds.SearchClient()  # type: ignore[attr-defined]
        results = client.web_search(query, top_n=5)
        snippets = []
        for r in results.get("results", []):
            snippets.append(f"- [{r.get('title', '')}]({r.get('url', '')}): {r.get('snippet', '')[:200]}")
        return json.dumps({
            "query": query,
            "verified": len(snippets) > 0,
            "sources": snippets[:3],
            "note": "联网核实仅供参考，请以权威来源为准。"
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"query": query, "verified": False, "error": str(e), "sources": []}, ensure_ascii=False)


@tool
def generate_annotated_docx(docx_path: str, issues_json: str) -> str:
    """生成带批注的 .annotated.docx。docx_path 为原始 docx 路径，issues_json 为问题清单 JSON 字符串。"""
    try:
        import subprocess
        inject_script = _utr_tool_path("inject-docx-comments.py")
        if not os.path.exists(inject_script):
            return json.dumps({"success": False, "error": f"批注注入脚本不存在: {inject_script}"})
        if not os.path.exists(docx_path):
            return json.dumps({"success": False, "error": f"原始 docx 文件不存在: {docx_path}"})
        issues_raw = json.loads(issues_json) if isinstance(issues_json, str) else issues_json
        # 确保 issues_data 是 {"schema_version": "1.0", "issues": [...]} 格式
        if isinstance(issues_raw, list):
            issues_data = {"schema_version": "1.0", "issues": issues_raw}
        elif isinstance(issues_raw, dict) and "issues" in issues_raw:
            issues_data = issues_raw
        else:
            issues_data = {"schema_version": "1.0", "issues": []}
        temp_issues = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(issues_data, temp_issues, ensure_ascii=False, indent=2)
        temp_issues.close()
        out_path = docx_path.replace(".docx", ".annotated.docx")
        proc = subprocess.run(
            [sys.executable, inject_script, docx_path, temp_issues.name, out_path],
            capture_output=True, text=True, timeout=180
        )
        os.unlink(temp_issues.name)
        if proc.returncode == 0:
            return json.dumps({"success": True, "annotated_docx_path": out_path, "output": proc.stdout}, ensure_ascii=False)
        else:
            return json.dumps({"success": False, "error": proc.stderr or "批注注入失败", "stdout": proc.stdout}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False)


@tool
def generate_markdown_report(analysis_result: str, issues_json: str) -> str:
    """生成完整 Markdown 审查报告。"""
    try:
        from coze_coding_dev_sdk import DocumentGenerationClient
        report_md = f"""# 法学论文质检审查报告

## 一、审查概况

{analysis_result}

## 二、问题清单

```json
{issues_json}
```

## 三、说明

- 本报告由法学论文质检自查智能体自动生成
- 审查结果仅供参考，请以导师/评阅人意见为准
- 致命/严重问题建议优先处理
- 如有疑问，请与导师沟通确认
"""
        client = DocumentGenerationClient()
        pdf_url = client.create_pdf_from_markdown(report_md, "review_report")
        return json.dumps({"success": True, "pdf_url": pdf_url, "markdown": report_md}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "markdown": analysis_result}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 错误处理中间件
# ---------------------------------------------------------------------------
@wrap_tool_call
def handle_tool_errors(request, handler):
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"工具执行错误: {str(e)}",
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
        generate_issues_json,
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
