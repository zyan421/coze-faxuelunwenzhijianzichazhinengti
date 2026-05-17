# -*- coding: utf-8 -*-
"""
法学论文质检工具模块

提供论文审查、引注校对、联网核实、文档生成等功能
"""

import os
import re
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_text_from_docx(file_path: str) -> str:
    """
    从Word文档中提取文本内容
    
    Args:
        file_path: Word文件路径
        
    Returns:
        提取的文本内容
    """
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


def extract_text_from_pdf(file_path: str) -> str:
    """
    从PDF文件中提取文本内容
    
    Args:
        file_path: PDF文件路径
        
    Returns:
        提取的文本内容
    """
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


def detect_paper_type(text: str) -> str:
    """
    根据论文内容推断论文类型
    
    Args:
        text: 论文文本
        
    Returns:
        论文类型: 本科/硕士/博士/期刊投稿/课程论文/未知
    """
    text_lower = text.lower()
    
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


def detect_citation_style(text: str) -> str:
    """
    根据论文内容推断引注方案
    
    Args:
        text: 论文文本
        
    Returns:
        引注方案: GB/T_7714/法学引注手册/学校模板/双方案对比/未知
    """
    # 检测GB/T 7714特征
    gb7714_indicators = [
        "[1]", "[2-5]", "[6,8]",  # 顺序编码制
        "et al.", "pp.", "eds.",  # 英文文献特征
    ]
    
    # 检测法学引注手册特征
    law_indicators = [
        "《", "》",  # 中文书名号
        "载", "参见",  # 法学引注常用词
        "第", "条",  # 法条引用
    ]
    
    gb_count = sum(1 for ind in gb7714_indicators if ind in text)
    law_count = sum(1 for ind in law_indicators if ind in text)
    
    if gb_count > law_count:
        return "GB/T_7714"
    elif law_count > 3:
        return "法学引注手册"
    else:
        return "双方案对比"


def build_review_prompt(text: str, paper_type: str, citation_style: str) -> str:
    """
    构建论文审查的提示词
    
    Args:
        text: 论文文本
        paper_type: 论文类型
        citation_style: 引注方案
        
    Returns:
        审查提示词
    """
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


def build_citation_check_prompt(text: str, citation_style: str) -> str:
    """
    构建引注格式专项检查的提示词
    
    Args:
        text: 论文文本
        citation_style: 引注方案
        
    Returns:
        引注检查提示词
    """
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


def build_verification_prompt(items: List[Dict[str, str]]) -> str:
    """
    构建联网核实的提示词
    
    Args:
        items: 待核实项列表 [{"原文": "...", "类型": "...", "核实建议": "..."}]
        
    Returns:
        核实提示词
    """
    items_text = "\n".join([
        f"- {i+1}. [{item.get('类型', '未知')}] {item.get('原文', '')} | 核实建议: {item.get('核实建议', '')}"
        for i, item in enumerate(items)
    ])
    
    prompt = f"""请对以下待核实项进行联网搜索核实，返回核实结果。

## 待核实项
{items_text}

## 核实要求
1. 对每个待核实项，通过联网搜索查找权威来源
2. 返回格式：
   - 核实项编号
   - 原文中表述
   - 核实结果（一致/不一致/无法核实）
   - 如不一致，给出正确表述
   - 权威来源（URL或官方来源）
   - 学理判断

## 注意
- 以当前日期（{datetime.now().strftime('%Y年%m月%d日')}）为基准判断时效性
- 优先查找官方来源或权威数据库
- 区分法律名称、法条内容、案例信息等不同类型
"""
    return prompt


def build_annotation_docx_prompt(text: str, issues: List[Dict], paper_title: str) -> str:
    """
    构建生成带批注Word文档的提示词
    
    Args:
        text: 论文文本
        issues: 问题清单
        paper_title: 论文标题
        
    Returns:
        生成批注文档的提示词
    """
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


def build_markdown_report(
    paper_info: Dict[str, Any],
    fatal_issues: List[str],
    dimension_reviews: Dict[str, str],
    citation_check: Dict[str, Any],
    cross_reference: Dict[str, Any],
    verification_results: List[Dict],
    top10_issues: List[Dict],
    discussion_questions: List[str]
) -> str:
    """
    构建Markdown格式的审查报告
    
    Args:
        paper_info: 论文基本信息
        fatal_issues: 致命问题列表
        dimension_reviews: 各维度审查结果
        citation_check: 引注检查结果
        cross_reference: 交叉引用检查结果
        verification_results: 联网核实结果
        top10_issues: Top10修改清单
        discussion_questions: 答辩问题建议
        
    Returns:
        Markdown格式报告
    """
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


def parse_review_response(response: str) -> Dict[str, Any]:
    """
    解析LLM返回的审查结果
    
    Args:
        response: LLM返回的原始响应
        
    Returns:
        结构化的审查结果
    """
    result = {
        "paper_info": {},
        "fatal_issues": [],
        "dimension_reviews": {},
        "citation_check": {},
        "cross_reference": {},
        "verification_items": [],
        "top10_issues": [],
        "discussion_questions": []
    }
    
    # 简单的结构化解析
    # 实际使用时由LLM生成结构化JSON更好
    
    return result
