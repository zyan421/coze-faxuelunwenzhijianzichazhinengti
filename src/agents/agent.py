#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
法学论文质检自查 Agent

unified-thesis-reviewer 总编排器
整合 legal-thesis-reviewer 和 legal-citation-checker
"""

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain_core.messages import AnyMessage
from langchain_core.tools import tool
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

# SDK导入（延迟加载，避免LSP报错）
# from coze_coding_dev_sdk import WebSearchClient, FetchUrlClient, DocumentGenerationClient

# 导入工具脚本
from tools.thesis_review_tools import (
    PaperAnalysis,
    ThesisReviewer,
    CitationChecker,
    WebVerifier,
    AnnotationGenerator,
    MarkdownReportGenerator,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

LLM_CONFIG = "config/agent_llm_config.json"
MAX_MESSAGES = 40

# 不定义自定义AgentState，使用langgraph默认的AgentState
# 如果需要扩展状态，可以在运行时通过context管理


# ============================================================================
# 工具函数定义
# ============================================================================

@tool
def welcome_and_guide() -> str:
    """
    欢迎引导工具。

    返回Agent的开场白和引导信息。
    用户首次对话或需要说明时调用。
    """
    return """请上传论文初稿的 Word 或文本型 PDF。

我会进行一次严格的法学论文质检，包括：
- 问题意识与选题价值
- 结构逻辑与章节比例
- 论证严谨性与深度
- 文献综述质量
- 实证与案例分析规范性
- 法律规范适用准确性
- 对策建议可行性
- 学术不端风险预警
- 引注格式专项校对（GB/T 7714 / 法学引注手册）
- 引用真实性联网核实

完成后我会返回：
- 带逐处批注的 Word/PDF 文件（可直接对照修改）
- 完整 Markdown 审查报告
- 问题清单和 Top 10 优先修改清单

**上传时请说明**（如未说明我会自动推断）：
- 论文类型：本科 / 硕士 / 博士 / 期刊投稿 / 课程论文
- 引注格式：GB/T 7714 / 法学引注手册 / 学校指定模板
"""


@tool
def read_paper_file(file_path: str) -> Dict[str, Any]:
    """
    读取论文文件。

    支持格式：.docx, .pdf（文本型）, .txt, .md
    扫描件PDF无法处理，会提示用户换用Word或OCR。

    Args:
        file_path: 论文文件路径

    Returns:
        包含论文文本、结构分析、元数据的字典
    """
    logger.info(f"读取论文文件: {file_path}")

    path = Path(file_path)
    if not path.exists():
        return {
            "success": False,
            "error": f"文件不存在: {file_path}"
        }

    suffix = path.suffix.lower()

    if suffix == '.docx':
        return _read_docx(file_path)
    elif suffix == '.pdf':
        return _read_pdf(file_path)
    elif suffix in ['.txt', '.md']:
        return _read_text(file_path)
    else:
        return {
            "success": False,
            "error": f"不支持的文件格式: {suffix}。请上传 .docx、.pdf、.txt 或 .md 文件。"
        }


def _read_docx(file_path: str) -> Dict[str, Any]:
    """读取Word文档"""
    try:
        from tools.scripts.extract_docx import DOCXTextExtractor

        with DOCXTextExtractor(file_path) as extractor:
            result = extractor.extract_structured()

        return {
            "success": True,
            "format": "docx",
            "text": result["text"],
            "structure": result["structure"],
            "metadata": result["metadata"],
            "blocks": result.get("blocks", []),
            "footnotes": result.get("footnotes", []),
            "references": result.get("references", []),
        }
    except Exception as e:
        logger.error(f"读取Word文档失败: {e}")
        return {
            "success": False,
            "error": f"读取Word文档失败: {str(e)}"
        }


def _read_pdf(file_path: str) -> Dict[str, Any]:
    """读取PDF文档"""
    try:
        from tools.scripts.extract_pdf_text import PDFTextExtractor

        with PDFTextExtractor(file_path) as extractor:
            result = extractor.extract_structured()

        # 检查是否为扫描件
        if result.get("report", {}).get("is_likely_scanned"):
            return {
                "success": False,
                "error": "该PDF可能是扫描件，无法提取文本。请使用OCR转换后的文本型PDF，或提供Word版本。",
                "is_scanned": True,
            }

        return {
            "success": True,
            "format": "pdf",
            "text": result["text"],
            "structure": result["structure"],
            "metadata": result["metadata"],
            "footnotes": result.get("footnotes", []),
            "references": result.get("references", []),
            "extraction_report": result.get("report", {}),
        }
    except Exception as e:
        logger.error(f"读取PDF失败: {e}")
        return {
            "success": False,
            "error": f"读取PDF失败: {str(e)}"
        }


def _read_text(file_path: str) -> Dict[str, Any]:
    """读取纯文本文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()

        # 简单分析结构
        lines = text.split('\n')
        structure = {
            "title": lines[0][:100] if lines else "",
            "word_count": len(text),
            "char_count": len(text.replace(" ", "")),
        }

        return {
            "success": True,
            "format": "text",
            "text": text,
            "structure": structure,
            "metadata": {},
        }
    except Exception as e:
        logger.error(f"读取文本文件失败: {e}")
        return {
            "success": False,
            "error": f"读取文本文件失败: {str(e)}"
        }


@tool
def detect_paper_type(text: str, user_specified: Optional[str] = None) -> str:
    """
    识别论文类型。

    自动推断或使用用户指定的论文类型。

    Args:
        text: 论文文本
        user_specified: 用户指定的论文类型

    Returns:
        论文类型: bachelor, master, phd, journal, course, unknown
    """
    if user_specified:
        return user_specified

    # 自动推断
    text_lower = text.lower()

    # 关键词检测
    indicators = {
        "phd": ["博士学位论文", "博士论文", "phd thesis", "doctoral dissertation"],
        "master": ["硕士学位论文", "硕士论文", "master thesis", "master's dissertation"],
        "journal": ["发表于", "载《", "期刊", "核心期刊", "cssci", "期刊投稿"],
        "course": ["课程论文", "期末论文", "学年论文"],
        "bachelor": ["本科毕业论文", "学士学位论文", "本科论文"],
    }

    for paper_type, keywords in indicators.items():
        for keyword in keywords:
            if keyword in text_lower:
                return paper_type

    return "unknown"


@tool
def detect_citation_style(
    text: str,
    user_specified: Optional[str] = None
) -> str:
    """
    识别引注格式。

    Args:
        text: 论文文本
        user_specified: 用户指定的引注格式

    Returns:
        引注格式: gbt7714, legal_citation, school_template, unknown
    """
    if user_specified:
        return user_specified

    text_lower = text.lower()

    # 检测GB/T 7714特征
    if re.search(r'\[\d+\]', text) and re.search(r'\d{4}\s*出版社', text):
        return "gbt7714"

    # 检测法学引注手册特征
    if re.search(r'脚注|尾注', text_lower) and not re.search(r'\[\d+\]', text):
        return "legal_citation"

    # 检测混合
    if re.search(r'\[\d+\]', text) and not re.search(r'\d{4}\s*出版社', text):
        return "legal_citation"  # 可能是法学引注但用了序号

    return "unknown"


@tool
def review_thesis(
    text: str,
    paper_type: str = "unknown",
    focus_areas: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    法学论文九大维度深度审查。

    严格审查论文内容，发现问题并给出证据。

    Args:
        text: 论文文本
        paper_type: 论文类型
        focus_areas: 重点审查领域

    Returns:
        九维度审查结果
    """
    logger.info(f"执行法学论文审查，类型: {paper_type}")

    reviewer = ThesisReviewer(paper_type=paper_type)
    result = reviewer.review(text, focus_areas=focus_areas)

    return result


@tool
def check_citations(
    text: str,
    citation_style: str = "unknown",
    reference_section: Optional[str] = None
) -> Dict[str, Any]:
    """
    引注格式专项检查。

    支持 GB/T 7714 和《法学引注手册》两种标准。

    Args:
        text: 论文文本
        citation_style: 引注格式
        reference_section: 参考文献章节（可选）

    Returns:
        引注检查结果
    """
    logger.info(f"执行引注格式检查，格式: {citation_style}")

    checker = CitationChecker(citation_style=citation_style)
    result = checker.check(text, reference_section=reference_section)

    return result


@tool
def crossref_citations(text: str) -> Dict[str, Any]:
    """
    脚注、正文、参考文献交叉对比。

    检查引用的一致性和完整性。

    Args:
        text: 论文文本

    Returns:
        交叉对比结果
    """
    logger.info("执行引用交叉对比")

    # 使用引用交叉对比脚本
    try:
        from tools.scripts.citation_crossref import CitationCrossReferencer

        analyzer = CitationCrossReferencer(text)
        result = analyzer.run_analysis()

        return {
            "success": True,
            "summary": result["summary"],
            "issues": result["issues"],
            "citations": result["citations"],
        }
    except Exception as e:
        logger.error(f"引用交叉对比失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "issues": [],
        }


@tool
def verify_facts_online(
    items: List[Dict[str, str]]
) -> Dict[str, Any]:
    """
    联网核实事实性内容。

    核实法律条文、案例信息、统计数据等。

    Args:
        items: 待核实项列表，每项包含 type, text, claim

    Returns:
        核实结果
    """
    logger.info(f"联网核实 {len(items)} 个项目")

    verifier = WebVerifier()
    results = []

    for item in items:
        result = verifier.verify(
            item.get("type", "general"),
            item.get("text", ""),
            item.get("claim", "")
        )
        results.append(result)

    # 分类汇总
    verified = [r for r in results if r.get("status") == "verified"]
    contradicted = [r for r in results if r.get("status") == "contradicted"]
    uncertain = [r for r in results if r.get("status") == "uncertain"]

    return {
        "success": True,
        "total": len(items),
        "verified": verified,
        "contradicted": contradicted,
        "uncertain": uncertain,
        "summary": {
            "verified_count": len(verified),
            "contradicted_count": len(contradicted),
            "uncertain_count": len(uncertain),
        }
    }


@tool
def generate_annotated_docx(
    input_path: str,
    issues: List[Dict[str, Any]],
    output_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    生成带批注的 Word 文档。

    Args:
        input_path: 原始Word文件路径
        issues: 问题列表
        output_path: 输出路径（可选）

    Returns:
        结果信息
    """
    logger.info(f"生成带批注的Word文档: {input_path}")

    if not output_path:
        input_p = Path(input_path)
        output_path = str(input_p.parent / f"{input_p.stem}.annotated.docx")

    try:
        from tools.scripts.inject_docx_comments import DOCXCommentInjector

        injector = DOCXCommentInjector(input_path, output_path)

        if not injector.load():
            return {
                "success": False,
                "error": "无法加载文档"
            }

        success, failed, failed_list = injector.add_comments_from_issues(issues)
        injector.save()

        return {
            "success": True,
            "output_path": output_path,
            "annotations_added": success,
            "annotations_failed": failed,
            "failed_list": failed_list,
        }
    except Exception as e:
        logger.error(f"生成批注文档失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@tool
def generate_annotated_pdf(
    input_path: str,
    issues: List[Dict[str, Any]],
    output_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    生成带批注的 PDF 文档（嵌入式高亮）。

    Args:
        input_path: 原始PDF文件路径
        issues: 问题列表
        output_path: 输出路径（可选）

    Returns:
        结果信息
    """
    logger.info(f"生成带批注的PDF文档: {input_path}")

    if not output_path:
        input_p = Path(input_path)
        output_path = str(input_p.parent / f"{input_p.stem}.annotated.pdf")

    try:
        from tools.scripts.annotate_pdf import PDFAnnotator

        with PDFAnnotator(input_path, output_path) as annotator:
            if not annotator.load():
                return {
                    "success": False,
                    "error": "无法加载PDF"
                }

            success, failed, failed_list = annotator.add_annotations_from_issues(issues)

        result = annotator.get_result()
        return result.to_dict()

    except Exception as e:
        logger.error(f"生成PDF批注失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@tool
def generate_markdown_report(
    paper_analysis: Dict,
    review_result: Dict,
    citation_result: Dict,
    crossref_result: Dict,
    verification_result: Dict,
    issues: List[Dict],
    paper_type: str = "unknown",
    citation_style: str = "unknown"
) -> str:
    """
    生成完整 Markdown 审查报告。

    Args:
        paper_analysis: 论文分析结果
        review_result: 九维度审查结果
        citation_result: 引注检查结果
        crossref_result: 交叉对比结果
        verification_result: 联网核实结果
        issues: 问题清单
        paper_type: 论文类型
        citation_style: 引注格式

    Returns:
        Markdown格式报告
    """
    logger.info("生成Markdown审查报告")

    generator = MarkdownReportGenerator(
        paper_type=paper_type,
        citation_style=citation_style
    )

    report = generator.generate(
        paper_analysis=paper_analysis,
        review_result=review_result,
        citation_result=citation_result,
        crossref_result=crossref_result,
        verification_result=verification_result,
        issues=issues,
    )

    return report


@tool
def upload_to_storage(local_path: str) -> str:
    """
    上传文件到对象存储。

    Args:
        local_path: 本地文件路径

    Returns:
        可下载的URL
    """
    logger.info(f"上传文件到存储: {local_path}")

    try:
        # 延迟导入避免LSP报错
        import importlib
        sdk = importlib.import_module("coze_coding_dev_sdk")
        StorageClient = getattr(sdk, "StorageClient", None)
        if StorageClient is None:
            raise ImportError("StorageClient not found")
        client = StorageClient()
        url = client.upload_file(local_path)
        return url
    except Exception as e:
        logger.warning(f"上传服务不可用: {e}，返回本地路径")
        return f"file://{local_path}"


# ============================================================================
# Agent 构建
# ============================================================================

def build_agent(ctx=None):
    """构建法学论文质检Agent"""

    # 加载配置
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)

    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    # 初始化LLM
    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")

    llm = ChatOpenAI(
        model=cfg['config'].get("model"),
        api_key=api_key,
        base_url=base_url,
        temperature=cfg['config'].get('temperature', 0.3),
        streaming=True,
        timeout=cfg['config'].get('timeout', 600),
        extra_body={
            "thinking": {
                "type": cfg['config'].get('thinking', 'enabled')
            }
        },
        default_headers={}  # 如果需要传递headers，在此配置
    )

    # 工具列表
    tools = [
        welcome_and_guide,
        read_paper_file,
        detect_paper_type,
        detect_citation_style,
        review_thesis,
        check_citations,
        crossref_citations,
        verify_facts_online,
        generate_annotated_docx,
        generate_annotated_pdf,
        generate_markdown_report,
        upload_to_storage,
    ]

    # 创建Agent（不指定state_schema，使用默认AgentState）
    agent = create_agent(
        model=llm,
        system_prompt=cfg.get("sp"),
        tools=tools,
        checkpointer=None,  # 可选：启用记忆
    )

    return agent


# ============================================================================
# 主入口
# ============================================================================

if __name__ == "__main__":
    agent = build_agent()
    print("法学论文质检Agent已构建")
    print("使用 agent.invoke() 调用")
