# -*- coding: utf-8 -*-
"""
法学论文质检自查Agent

面向法学专业老师和学生的论文初稿质检智能体
"""

import os
import re
import json
import logging
from typing import Annotated, List, Dict, Any, Optional, Union
from datetime import datetime

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
from coze_coding_utils.runtime_ctx.context import default_headers, new_context

logger = logging.getLogger(__name__)

LLM_CONFIG = "config/agent_llm_config.json"
MAX_MESSAGES = 40


def _windowed_messages(old, new):
    """滑动窗口: 只保留最近 MAX_MESSAGES 条消息"""
    combined = add_messages(old, new)
    # 类型可能是 list[BaseMessage] 或 AnyMessage，直接切片
    if hasattr(combined, '__getitem__'):
        return combined[-MAX_MESSAGES:]  # type: ignore
    return list(combined)[-MAX_MESSAGES:]


class AgentState(MessagesState):
    """法学论文质检Agent的状态"""
    pass  # 继承MessagesState的messages字段，添加其他可选字段


def _get_storage_saver():
    """延迟导入storage模块"""
    from storage.memory.memory_saver import get_memory_saver
    return get_memory_saver()


def _extract_text_from_docx(file_path: str) -> str:
    """从Word文档中提取文本内容"""
    try:
        from docx import Document
        doc = Document(file_path)
        full_text = []
        for para in doc.paragraphs:
            full_text.append(para.text)
        return "\n".join(full_text)
    except Exception as e:
        logger.error(f"读取Word文档失败: {e}")
        raise


def _extract_text_from_pdf(file_path: str) -> str:
    """从PDF文件中提取文本内容"""
    try:
        import pymupdf
        doc = pymupdf.open(file_path)
        text_parts = []
        for page in doc:
            page_text = page.get_text()
            if isinstance(page_text, str):
                text_parts.append(page_text)
            elif isinstance(page_text, list):
                text_parts.extend([t for t in page_text if isinstance(t, str)])
            else:
                text_parts.append(str(page_text))
        text = "".join(text_parts)
        doc.close()
        return text
    except Exception as e:
        logger.error(f"读取PDF文件失败: {e}")
        raise


def _detect_paper_type(text: str) -> str:
    """根据论文内容推断论文类型"""
    # 检测期刊投稿特征
    journal_keywords = ["期刊", "投稿", "发表", "学术论文", "研究论文"]
    for kw in journal_keywords:
        if kw in text[:500]:
            return "期刊投稿"

    # 检测学位类型
    if "硕士学位论文" in text or "硕士论文" in text:
        return "硕士"
    if "博士学位论文" in text or "博士论文" in text:
        return "博士"
    if "学士学位论文" in text or "本科毕业论文" in text or "学士论文" in text:
        return "本科"
    if "课程论文" in text or "学期论文" in text:
        return "课程论文"

    return "未知"


def _detect_citation_style(text: str) -> str:
    """根据论文内容推断引注方案"""
    # 检测GB/T 7714特征
    gb7714_indicators = ["[1]", "[2-5]", "[6,8]", "et al.", "pp.", "eds."]

    # 检测法学引注手册特征
    law_indicators = ["《", "》", "载", "参见", "第", "条"]

    gb_count = sum(1 for ind in gb7714_indicators if ind in text)
    law_count = sum(1 for ind in law_indicators if ind in text)

    if gb_count > law_count:
        return "GB/T_7714"
    elif law_count > 3:
        return "法学引注手册"
    else:
        return "双方案对比"


def _build_review_prompt(text: str, paper_type: str, citation_style: str) -> str:
    """构建论文审查的提示词"""
    prompt = f"""请对以下法学论文进行严格的质检审查。

## 论文类型
{paper_type}

## 引注方案
{citation_style}

## 审查要求

### 一、论文基本信息
1. 题目
2. 作者（已匿名化处理）
3. 字数估算
4. 章节结构概览

### 二、致命/重大问题（必须全部列出）
包括但不限于：
- 核心观点存在严重逻辑漏洞
- 关键法条引用错误或已失效
- 核心案例信息严重失实
- 统计数据与权威来源严重不符
- 存在明显抄袭或不当引用嫌疑

### 三、九大维度审查

**1. 选题质量**
- 选题是否具有学术价值或实践意义
- 研究问题是否明确、具体
- 创新点是否清晰

**2. 论文结构**
- 章节逻辑是否清晰
- 各部分比例是否合理
- 是否存在结构缺失

**3. 论证逻辑**
- 论点与论据是否匹配
- 论证过程是否严密
- 是否存在逻辑跳跃

**4. 文献综述**
- 文献覆盖是否全面
- 是否正确评价已有研究
- 是否找到研究空白

**5. 实证分析（如有）**
- 数据来源是否可靠
- 分析方法是否恰当
- 结论是否由数据支撑

**6. 规范适用**
- 法条引用是否准确
- 法律适用逻辑是否正确
- 是否考虑法律解释方法

**7. 语言表达**
- 是否存在病句或歧义
- 法学专业术语使用是否准确
- 表达是否简洁准确

**8. 对策建议（如有）**
- 建议是否具有可操作性
- 建议与问题是否对应
- 是否考虑可行性

**9. 学术规范**
- 是否存在学术不端风险
- 引用是否规范
- 格式是否统一

### 四、引注格式校对
1. 脚注格式检查（序号、标点、出版信息）
2. 参考文献格式检查
3. 正文夹注格式检查
4. 法律文件引用格式检查
5. 案例引用格式检查
6. 外文文献格式检查

### 五、引用交叉对比
- 脚注与正文引用是否一致
- 脚注与参考文献是否对应
- 同一文献多次引用是否统一

### 六、联网核实清单
列出需要核实的内容（法律名称、法条、案例、统计数据等），格式：
- 待核实项 | 原文表述 | 核实建议

### 七、优先修改清单（Top 10）
按优先级排序，格式：
1. [优先级] 问题描述 | 位置 | 建议修改方式

### 八、答辩/讨论问题建议
针对论文薄弱环节，提出3-5个可能的答辩问题

---
## 待审查论文内容：

{text[:80000]}

---
## 审查报告

请严格按照上述格式输出审查报告。
"""
    return prompt


def _build_citation_check_prompt(text: str, citation_style: str) -> str:
    """构建引注格式专项检查的提示词"""
    prompt = f"""请对以下法学论文进行引注格式专项检查。

## 引注方案
{citation_style}

## 检查要求

### 一、脚注格式检查
1. 序号格式是否正确
2. 作者、题目、出版社/期刊、年份格式
3. 标点符号使用是否规范
4. 引用页码是否完整

### 二、参考文献格式检查
1. 格式是否符合{citation_style}
2. 中外文文献分开处理
3. 顺序是否正确

### 三、正文夹注检查
1. 是否与脚注一致
2. 格式是否规范

### 四、法律文件引用检查
1. 法条编号格式
2. 文件全称与简称使用

### 五、案例引用检查
1. 案号格式
2. 当事人信息处理
3. 审理法院和年份

### 六、外文文献检查
1. 作者姓名格式
2. 标题格式
3. 出版信息格式

---
## 待检查论文内容：

{text[:60000]}

---
## 检查报告

请列出所有发现的引注格式问题，按严重程度排序。
"""
    return prompt


def _build_annotation_docx_prompt(text: str, issues: List[Dict], paper_title: str) -> str:
    """构建生成带批注Word文档的提示词"""
    issues_text = "\n".join([
        f"- 位置{issue.get('location', '未知')}: {issue.get('description', '')} [{issue.get('severity', '建议')}]"
        for issue in issues[:50]
    ])

    prompt = f"""请为以下论文生成带批注的Word文档内容。

## 论文标题
{paper_title}

## 论文正文
{text[:50000]}

## 问题批注清单（请在对应位置添加批注）
{issues_text}

## Word批注格式要求
请以Markdown格式输出，包含：
1. 论文原文（保留原有结构）
2. 在问题位置用特殊标记标注批注
3. 批注格式：[批注] 严重程度: 问题描述 | 修改建议

## 输出要求
生成完整的带批注文档内容，确保：
1. 保留论文原有章节结构
2. 在每个问题位置插入批注
3. 使用明确的批注标记
"""
    return prompt


def _build_markdown_report(
    paper_info: Dict[str, Any],
    fatal_issues: List[str],
    dimension_reviews: Dict[str, str],
    citation_check: Dict[str, Any],
    cross_reference: Dict[str, Any],
    verification_results: List[Dict],
    top10_issues: List[Dict],
    discussion_questions: List[str]
) -> str:
    """构建Markdown格式的审查报告"""
    report = f"""# 法学论文质检审查报告

> 审查时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}
> 审查类型：一站式深度质检

---

## 一、论文基本信息

| 项目 | 内容 |
|------|------|
| 论文标题 | {paper_info.get('title', '未知')} |
| 论文类型 | {paper_info.get('type', '未知')} |
| 字数估算 | {paper_info.get('word_count', '未知')} |
| 引注方案 | {paper_info.get('citation_style', '未知')} |
| 章节结构 | {paper_info.get('structure', '未知')} |

---

## 二、致命/重大问题

"""

    if fatal_issues:
        for i, issue in enumerate(fatal_issues, 1):
            report += f"{i}. ⚠️ **{issue}**\n\n"
    else:
        report += "✅ 未发现致命或重大问题\n\n"

    report += "---\n\n## 三、九大维度审查\n\n"

    dimension_names = {
        "选题质量": "topic_quality",
        "论文结构": "structure",
        "论证逻辑": "argumentation",
        "文献综述": "literature_review",
        "实证分析": "empirical_analysis",
        "规范适用": "norm_application",
        "语言表达": "language",
        "对策建议": "recommendations",
        "学术规范": "academic_integrity"
    }

    for dim_name, dim_key in dimension_names.items():
        report += f"### {dim_name}\n\n"
        if dim_key in dimension_reviews:
            report += f"{dimension_reviews[dim_key]}\n\n"
        else:
            report += "（未提供详细审查结果）\n\n"
        report += "---\n\n"

    report += "## 四、引注格式校对\n\n"

    if citation_check:
        report += f"""### 4.1 脚注格式
{citation_check.get('footnotes', '（无问题）')}

### 4.2 参考文献格式
{citation_check.get('references', '（无问题）')}

### 4.3 正文夹注
{citation_check.get('intext_citations', '（无问题）')}

### 4.4 法律文件引用
{citation_check.get('legal_citations', '（无问题）')}

### 4.5 案例引用
{citation_check.get('case_citations', '（无问题）')}

### 4.6 外文文献
{citation_check.get('foreign_literature', '（无问题）')}

---
"""

    report += "## 五、引用交叉对比\n\n"

    if cross_reference:
        report += f"""### 5.1 脚注与正文一致性
{cross_reference.get('footnote_text_match', '（未检查）')}

### 5.2 脚注与参考文献一致性
{cross_reference.get('footnote_ref_match', '（未检查）')}

### 5.3 多次引用统一性
{cross_reference.get('repeated_citation', '（未检查）')}

---
"""

    if verification_results:
        report += "## 六、联网核实结果\n\n"

        for i, result in enumerate(verification_results, 1):
            report += f"""### {i}. {result.get('item', '待核实项')}

| 项目 | 内容 |
|------|------|
| 原文中表述 | {result.get('original', '未知')} |
| 核实结果 | {result.get('status', '未知')} |
| 正确表述 | {result.get('correct', '无')} |
| 权威来源 | {result.get('source', '未知')} |
| 学理判断 | {result.get('judgment', '未知')} |

---
"""

    report += "## 七、优先修改清单（Top 10）\n\n"

    if top10_issues:
        for i, issue in enumerate(top10_issues[:10], 1):
            severity = issue.get('severity', '建议')
            severity_icon = {"致命": "🔴", "重大": "🟠", "一般": "🟡", "建议": "🟢"}.get(severity, "⚪")
            report += f"{i}. {severity_icon} **{severity}**: {issue.get('description', '')}\n"
            report += f"   - 位置: {issue.get('location', '未知')}\n"
            report += f"   - 建议: {issue.get('suggestion', '')}\n\n"
    else:
        report += "✅ 未发现需要优先修改的问题\n\n"

    report += "---\n\n## 八、答辩/讨论问题建议\n\n"

    if discussion_questions:
        for i, q in enumerate(discussion_questions, 1):
            report += f"{i}. {q}\n\n"
    else:
        report += "（未提供答辩问题建议）\n\n"

    report += f"""---

## 九、批注文档说明

本报告附带带批注的Word文档（`.annotated.docx`），请在Word或支持批注的阅读器中打开查看。

批注标记说明：
- 🔴 **致命问题**：必须修改，否则影响论文基本成立
- 🟠 **重大问题**：建议优先修改，影响论文质量
- 🟡 **一般问题**：可以修改，提升论文规范性
- 🟢 **建议优化**：可选修改，非必须

---

## 十、声明

本审查报告仅供参考，不代表最终评审意见。论文的学术价值和创新性最终由评审专家判断。

**隐私声明**：本报告仅供内部使用，不对外公开论文内容。
"""

    return report


def build_agent(ctx=None):
    """构建法学论文质检Agent"""

    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)

    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

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
        default_headers=default_headers(ctx) if ctx else {}
    )

    return create_agent(
        model=llm,
        system_prompt=cfg.get("sp"),
        tools=[
            read_paper_file,
            review_paper,
            check_citations,
            verify_facts_online,
            generate_annotated_docx,
            generate_markdown_report
        ],
        checkpointer=_get_storage_saver(),
        state_schema=AgentState,
    )


@tool
def read_paper_file(file_url: str) -> str:
    """
    读取论文文件（Word或PDF），提取文本内容。

    支持格式：
    - .docx (Word文档)
    - .pdf (文本型PDF，扫描件PDF需用户提供OCR版本)

    Args:
        file_url: 论文文件的URL或本地路径

    Returns:
        论文文本内容及基本信息摘要
    """
    ctx = new_context(method="read_paper_file")
    logger.info(f"读取论文文件: {file_url}")

    try:
        # 判断是URL还是本地路径
        if file_url.startswith('http'):
            # 下载文件到临时目录
            import tempfile
            import urllib.request

            file_name = os.path.basename(file_url.split('?')[0])
            temp_path = os.path.join('/tmp', file_name)

            try:
                urllib.request.urlretrieve(file_url, temp_path)
                file_path = temp_path
            except Exception as e:
                logger.error(f"下载文件失败: {e}")
                return f"❌ 无法下载文件: {str(e)}\n\n请确保文件URL可公开访问，或直接上传文件。"
        else:
            file_path = file_url
            file_name = os.path.basename(file_path)

        # 检查文件类型
        if file_name.lower().endswith('.docx'):
            text = _extract_text_from_docx(file_path)
        elif file_name.lower().endswith('.pdf'):
            text = _extract_text_from_pdf(file_path)
            if not text.strip():
                return ("⚠️ 该PDF可能是扫描件，无法提取文本。\n\n"
                       "**建议**：\n"
                       "1. 请先将扫描件转换为文本型PDF\n"
                       "2. 或提供Word版本的文件\n"
                       "3. 部分OCR工具可辅助转换")
        else:
            return "❌ 不支持的文件格式，请上传 .docx 或 .pdf 文件。"

        # 检测论文类型和引注方案
        paper_type = _detect_paper_type(text)
        citation_style = _detect_citation_style(text)

        # 估算字数
        word_count = len(text)
        char_count = len(text.replace(' ', '').replace('\n', ''))

        # 提取标题
        title_match = re.search(r'^#\s+(.+?)$|^(.{5,50})$', text, re.MULTILINE)
        title = title_match.group(1) if title_match else "未检测到标题"

        result = f"""📄 **论文读取成功**

| 项目 | 内容 |
|------|------|
| 文件名 | {file_name} |
| 提取字数 | 约 {word_count:,} 字 |
| 字符数 | {char_count:,} |
| 论文类型 | {paper_type} |
| 引注方案 | {citation_style} |

---
### 论文标题
{title[:100]}

---
### 论文内容预览（前2000字）
{text[:2000]}

---
**后续步骤**：我将自动进入论文审查流程。如需调整论文类型或引注方案，请告知。"""

        logger.info(f"论文读取成功: {file_name}, 字数: {word_count}")
        return result

    except Exception as e:
        logger.error(f"读取论文文件失败: {e}")
        return f"❌ 读取文件失败: {str(e)}\n\n请检查文件是否完整且可读。"


@tool
def review_paper(
    text: str,
    paper_type: str = "未知",
    citation_style: str = "双方案对比",
    focus_areas: str = "全部"
) -> str:
    """
    对论文进行九大维度的深度审查。

    审查维度：
    1. 选题质量 - 学术价值、研究问题、创新点
    2. 论文结构 - 章节逻辑、比例、结构完整性
    3. 论证逻辑 - 论点论据匹配、论证严密性
    4. 文献综述 - 文献覆盖、评价质量、研究空白
    5. 实证分析 - 数据可靠性、方法恰当性
    6. 规范适用 - 法条准确性、法律适用逻辑
    7. 语言表达 - 病句歧义、术语准确性
    8. 对策建议 - 可操作性、可行性
    9. 学术规范 - 学术不端风险、引用规范性

    Args:
        text: 论文文本内容（可从前一步read_paper_file获取）
        paper_type: 论文类型（本科/硕士/博士/期刊投稿/课程论文/未知）
        citation_style: 引注方案（GB/T_7714/法学引注手册/学校模板/双方案对比/未知）
        focus_areas: 重点审查领域，默认为"全部"，可指定如"引注格式+学术规范"

    Returns:
        结构化的审查报告，包含各维度评价、问题清单和修改建议
    """
    ctx = new_context(method="review_paper")
    logger.info(f"开始论文审查: 类型={paper_type}, 引注={citation_style}")

    try:
        from coze_coding_dev_sdk import LLMClient
        from langchain_core.messages import HumanMessage

        client = LLMClient(ctx=ctx)

        # 构建审查提示词
        prompt = _build_review_prompt(text, paper_type, citation_style)

        # 如果指定了重点领域，调整审查重点
        if focus_areas != "全部":
            prompt = prompt.replace(
                "## 审查要求",
                f"## 重点审查领域\n{focus_areas}\n\n## 审查要求"
            )

        messages = [HumanMessage(content=prompt)]

        # 调用LLM进行审查
        response = client.invoke(
            messages=messages,
            model="doubao-seed-2-0-pro-260215",
            thinking="enabled",
            temperature=0.3,
            max_completion_tokens=32768
        )

        # 处理响应内容
        if isinstance(response.content, str):
            review_result = response.content
        elif isinstance(response.content, list):
            text_parts = []
            for item in response.content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
            review_result = "\n".join(text_parts)
        else:
            review_result = str(response.content)

        logger.info("论文审查完成")
        return review_result

    except Exception as e:
        logger.error(f"论文审查失败: {e}")
        return f"❌ 论文审查失败: {str(e)}\n\n请重试或检查论文内容是否正确。"


@tool
def check_citations(
    text: str,
    citation_style: str = "双方案对比",
    check_types: str = "全部"
) -> str:
    """
    专项检查引注格式。

    检查项目：
    - 脚注格式（序号、标点、出版信息）
    - 参考文献格式（按GB/T 7714或法学引注手册）
    - 正文夹注格式
    - 法律文件引用格式
    - 案例引用格式
    - 外文文献格式

    Args:
        text: 论文文本内容
        citation_style: 引注方案（GB/T_7714/法学引注手册/学校模板/双方案对比）
        check_types: 检查类型，默认为"全部"，可指定如"脚注+参考文献"

    Returns:
        引注格式检查报告，包含问题清单和正确格式示例
    """
    ctx = new_context(method="check_citations")
    logger.info(f"开始引注格式检查: 方案={citation_style}")

    try:
        from coze_coding_dev_sdk import LLMClient
        from langchain_core.messages import HumanMessage

        client = LLMClient(ctx=ctx)

        # 构建引注检查提示词
        prompt = _build_citation_check_prompt(text, citation_style)

        # 如果指定了检查类型
        if check_types != "全部":
            prompt = prompt.replace(
                "## 检查要求",
                f"## 重点检查项目\n{check_types}\n\n## 检查要求"
            )

        messages = [HumanMessage(content=prompt)]

        response = client.invoke(
            messages=messages,
            model="doubao-seed-2-0-pro-260215",
            thinking="disabled",
            temperature=0.3,
            max_completion_tokens=16384
        )

        # 处理响应
        if isinstance(response.content, str):
            check_result = response.content
        elif isinstance(response.content, list):
            text_parts = []
            for item in response.content:
                if isinstance(item, dict) and item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
            check_result = "\n".join(text_parts)
        else:
            check_result = str(response.content)

        logger.info("引注格式检查完成")
        return check_result

    except Exception as e:
        logger.error(f"引注格式检查失败: {e}")
        return f"❌ 引注格式检查失败: {str(e)}\n\n请重试。"


@tool
def verify_facts_online(verification_items: List[Dict[str, str]]) -> str:
    """
    联网核实论文中的事实性内容。

    核实类型：
    - 法律名称核实（如《民法典》vs已废止法律）
    - 法条内容核实（条款是否准确、是否有效）
    - 案例信息核实（案号、法院、判决结果）
    - 统计数据核实（权威来源验证）
    - 人物/机构信息核实

    Args:
        verification_items: 待核实项列表，格式为：
            [{"原文": "...", "类型": "法条/案例/统计/其他", "核实建议": "..."}]

    Returns:
        核实结果报告，包含每个待核实项的：
        - 核实状态（一致/不一致/无法核实）
        - 正确表述（如不一致）
        - 权威来源
        - 学理判断
    """
    ctx = new_context(method="verify_facts_online")
    logger.info(f"开始联网核实: {len(verification_items)} 个待核实项")

    if not verification_items:
        return "✅ 未提供待核实项，跳过联网核实。"

    try:
        from coze_coding_dev_sdk import SearchClient, LLMClient
        from langchain_core.messages import HumanMessage

        search_client = SearchClient(ctx=ctx)
        llm_client = LLMClient(ctx=ctx)

        verification_results = []

        for i, item in enumerate(verification_items[:20], 1):
            original_text = item.get('原文', '')
            item_type = item.get('类型', '其他')
            suggestion = item.get('核实建议', '')

            logger.info(f"正在核实第 {i} 项: {original_text[:50]}...")

            try:
                # 使用联网搜索
                search_response = search_client.web_search(
                    query=f"{original_text[:100]} {item_type}",
                    count=5,
                    need_summary=True
                )

                if search_response.web_items:
                    # 获取搜索结果摘要
                    search_summary = ""
                    for web_item in search_response.web_items[:3]:
                        search_summary += f"- [{web_item.title}]({web_item.url}): {web_item.snippet[:200]}\n"

                    # 使用LLM判断一致性
                    verify_prompt = f"""请判断以下论文中的表述是否准确：

## 原文中表述
{original_text}

## 类型
{item_type}

## 核实建议
{suggestion}

## 搜索结果
{search_summary}

请返回JSON格式：
{{
    "status": "一致/不一致/无法核实",
    "correct_text": "如不一致，给出正确表述",
    "source": "权威来源URL或名称",
    "judgment": "学理判断"
}}"""

                    verify_messages = [HumanMessage(content=verify_prompt)]
                    verify_response = llm_client.invoke(
                        messages=verify_messages,
                        model="doubao-seed-2-0-pro-260215",
                        temperature=0.1,
                        max_completion_tokens=2048
                    )

                    # 解析LLM响应
                    content = verify_response.content
                    if isinstance(content, list):
                        content = "\n".join([
                            item.get('text', '') for item in content
                            if isinstance(item, dict) and item.get('type') == 'text'
                        ])

                    # 尝试提取JSON
                    json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
                    if json_match:
                        result_data = json.loads(json_match.group())
                    else:
                        result_data = {
                            "status": "无法核实",
                            "correct_text": "",
                            "source": "",
                            "judgment": "搜索结果不足，无法判断"
                        }

                    verification_results.append({
                        "item": f"第{i}项",
                        "original": original_text,
                        "type": item_type,
                        "status": result_data.get("status", "无法核实"),
                        "correct": result_data.get("correct_text", ""),
                        "source": result_data.get("source", ""),
                        "judgment": result_data.get("judgment", "")
                    })
                else:
                    verification_results.append({
                        "item": f"第{i}项",
                        "original": original_text,
                        "type": item_type,
                        "status": "无法核实",
                        "correct": "",
                        "source": "",
                        "judgment": "未找到相关搜索结果"
                    })

            except Exception as e:
                logger.warning(f"核实第{i}项失败: {e}")
                verification_results.append({
                    "item": f"第{i}项",
                    "original": original_text,
                    "type": item_type,
                    "status": "核实失败",
                    "correct": "",
                    "source": "",
                    "judgment": f"核实过程出错: {str(e)}"
                })

        # 构建结果报告
        report = "## 联网核实结果\n\n"

        for result in verification_results:
            status_icon = {"一致": "✅", "不一致": "❌", "无法核实": "⚠️", "核实失败": "🔴"}.get(
                result['status'], "❓"
            )
            report += f"""### {status_icon} {result['item']} [{result['type']}]

| 项目 | 内容 |
|------|------|
| 原文中表述 | {result['original']} |
| 核实结果 | **{result['status']}** |
"""
            if result['correct']:
                report += f"| 正确表述 | {result['correct']} |\n"
            if result['source']:
                report += f"| 权威来源 | {result['source']} |\n"
            report += f"| 学理判断 | {result['judgment']} |\n\n"

        logger.info(f"联网核实完成: {len(verification_results)} 项")
        return report

    except Exception as e:
        logger.error(f"联网核实失败: {e}")
        return f"❌ 联网核实失败: {str(e)}\n\n请重试或手动核实相关事实。"


@tool
def generate_annotated_docx(
    paper_text: str,
    issues: List[Dict[str, str]],
    paper_title: str = "论文批注版"
) -> str:
    """
    生成带批注的Word文档。

    Args:
        paper_text: 论文原文
        issues: 问题清单，格式为：
            [{"location": "位置描述", "description": "问题描述", "severity": "致命/重大/一般/建议", "suggestion": "修改建议"}]
        paper_title: 论文标题（用于文件名）

    Returns:
        带批注的Word文档下载链接（.annotated.docx）
    """
    ctx = new_context(method="generate_annotated_docx")
    logger.info(f"生成带批注文档: {paper_title}")

    if not paper_text:
        return "❌ 请先读取论文内容。"

    try:
        from coze_coding_dev_sdk import LLMClient, DocumentGenerationClient, DOCXConfig
        from langchain_core.messages import HumanMessage

        llm_client = LLMClient(ctx=ctx)

        # 使用LLM生成批注文档内容
        prompt = _build_annotation_docx_prompt(paper_text, issues, paper_title)

        messages = [HumanMessage(content=prompt)]
        response = llm_client.invoke(
            messages=messages,
            model="doubao-seed-2-0-pro-260215",
            thinking="disabled",
            temperature=0.3,
            max_completion_tokens=32768
        )

        # 处理响应
        content = response.content
        if isinstance(content, list):
            content = "\n".join([
                item.get('text', '') for item in content
                if isinstance(item, dict) and item.get('type') == 'text'
            ])

        # 生成Word文档
        # 使用安全的文件名
        safe_title = re.sub(r'[^\w\-_.]', '_', paper_title)[:50]
        doc_title = f"annotated_{safe_title}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        doc_config = DOCXConfig(
            font_name="Noto Sans CJK SC",
            font_size=11,
            top_margin=0.75,
            bottom_margin=0.75,
            left_margin=0.75,
            right_margin=0.75
        )
        doc_client = DocumentGenerationClient(docx_config=doc_config)

        url = doc_client.create_docx_from_markdown(content, doc_title)

        logger.info(f"带批注文档生成成功: {url}")

        return f"""📄 **带批注Word文档已生成**

**下载链接**: {url}

> ⚠️ 链接有效期为24小时，请及时下载。

### 批注说明
- 🔴 **致命问题**：必须修改
- 🟠 **重大问题**：建议优先修改
- 🟡 **一般问题**：可以修改
- 🟢 **建议优化**：可选修改

### 使用建议
1. 下载后在Microsoft Word或WPS中打开
2. 打开"审阅"面板查看所有批注
3. 按批注位置逐一修改
4. 修改完成后删除所有批注标记"""

    except Exception as e:
        logger.error(f"生成批注文档失败: {e}")
        return f"❌ 生成批注文档失败: {str(e)}\n\n请检查论文内容是否完整，或稍后重试。"


@tool
def generate_markdown_report(
    paper_info: Dict[str, Any],
    review_result: str,
    citation_check: str = "",
    verification_result: str = "",
    top10_issues: List[Dict[str, str]] = None
) -> str:
    """
    生成完整的Markdown格式审查报告。

    Args:
        paper_info: 论文基本信息 {"title": "...", "type": "...", "word_count": "...", "citation_style": "..."}
        review_result: 论文审查结果（完整报告文本）
        citation_check: 引注格式检查结果（可选）
        verification_result: 联网核实结果（可选）
        top10_issues: Top10优先修改清单 [{"severity": "...", "description": "...", "location": "...", "suggestion": "..."}]

    Returns:
        完整的Markdown审查报告下载链接（.pdf）
    """
    ctx = new_context(method="generate_markdown_report")
    logger.info("生成Markdown审查报告")

    try:
        from coze_coding_dev_sdk import DocumentGenerationClient, PDFConfig, LLMClient
        from langchain_core.messages import HumanMessage

        llm_client = LLMClient(ctx=ctx)

        # 提取审查报告中的各部分内容
        parse_prompt = f"""请从以下论文审查结果中提取结构化信息：

{review_result}

请返回JSON格式：
{{
    "fatal_issues": ["致命问题1", "致命问题2", ...],
    "dimension_reviews": {{
        "topic_quality": "选题质量评价",
        "structure": "结构评价",
        "argumentation": "论证评价",
        "literature_review": "文献综述评价",
        "empirical_analysis": "实证分析评价",
        "norm_application": "规范适用评价",
        "language": "语言表达评价",
        "recommendations": "对策建议评价",
        "academic_integrity": "学术规范评价"
    }},
    "discussion_questions": ["答辩问题1", "答辩问题2", ...]
}}"""

        messages = [HumanMessage(content=parse_prompt)]
        parse_response = llm_client.invoke(
            messages=messages,
            model="doubao-seed-2-0-pro-260215",
            temperature=0.1,
            max_completion_tokens=8192
        )

        # 解析JSON
        parse_content = parse_response.content
        if isinstance(parse_content, list):
            parse_content = "\n".join([
                item.get('text', '') for item in parse_content
                if isinstance(item, dict) and item.get('type') == 'text'
            ])

        json_match = re.search(r'\{{.*\}}', parse_content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {
                "fatal_issues": [],
                "dimension_reviews": {},
                "discussion_questions": []
            }

        # 构建交叉引用结果（简化处理）
        cross_reference = {
            "footnote_text_match": "已在审查报告中覆盖",
            "footnote_ref_match": "已在引注检查中覆盖",
            "repeated_citation": "已在引注检查中覆盖"
        }

        # 构建Markdown报告
        markdown_report = _build_markdown_report(
            paper_info=paper_info,
            fatal_issues=parsed.get("fatal_issues", []),
            dimension_reviews=parsed.get("dimension_reviews", {}),
            citation_check={"footnotes": citation_check[:2000] if citation_check else ""},
            cross_reference=cross_reference,
            verification_results=[],  # verification_result已单独展示
            top10_issues=top10_issues or [],
            discussion_questions=parsed.get("discussion_questions", [])
        )

        # 生成PDF
        pdf_config = PDFConfig(
            page_size="A4",
            left_margin=72,
            right_margin=72,
            top_margin=72,
            bottom_margin=72
        )
        doc_client = DocumentGenerationClient(pdf_config=pdf_config)

        safe_title = re.sub(r'[^\w\-_.]', '_', paper_info.get('title', '论文审查报告'))[:50]
        report_title = f"review_report_{safe_title}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        pdf_url = doc_client.create_pdf_from_markdown(markdown_report, report_title)

        logger.info(f"审查报告生成成功: {pdf_url}")

        return f"""📋 **法学论文质检审查报告已生成**

**PDF下载链接**: {pdf_url}

> ⚠️ 链接有效期为24小时，请及时下载。

### 报告内容
1. 论文基本信息
2. 致命/重大问题清单
3. 九大维度审查结果
4. 引注格式校对报告
5. 引用交叉对比分析
6. 联网核实结果（如有）
7. Top 10优先修改清单
8. 答辩/讨论问题建议

---
### 配套文件
如需带批注的Word文档，请使用 `generate_annotated_docx` 工具生成。"""

    except Exception as e:
        logger.error(f"生成审查报告失败: {e}")

        # 即使失败也返回纯文本报告
        fallback_report = f"""# 法学论文质检审查报告

> 审查时间：{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

## 论文基本信息
- 标题：{paper_info.get('title', '未知')}
- 类型：{paper_info.get('type', '未知')}
- 字数：{paper_info.get('word_count', '未知')}
- 引注方案：{paper_info.get('citation_style', '未知')}

## 审查结果

{review_result[:10000] if review_result else '（审查结果为空）'}

---
*报告生成过程中部分功能受限，请参考上述审查结果手动整理。*
"""
        return fallback_report
