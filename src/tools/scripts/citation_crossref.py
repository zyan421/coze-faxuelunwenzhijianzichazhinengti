#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引用交叉对比脚本 (citation-crossref.py)

检测脚注、正文夹注、参考文献之间的不一致和脱钩问题：
1. 脚注引用但在参考文献中没有对应条目
2. 参考文献中有但脚注未引用
3. 正文夹注与脚注/参考文献不一致
4. 引文格式不统一
5. 序号错位、重复、遗漏
"""

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CitationType(Enum):
    """引用类型"""
    FOOTNOTE = "footnote"        # 脚注
    IN_TEXT = "in_text"          # 正文夹注
    REFERENCE = "reference"      # 参考文献条目
    BIBLIOGRAPHY = "bibliography" # 书目文献


@dataclass
class Citation:
    """单条引用"""
    id: str                      # 唯一标识
    type: CitationType           # 类型
    raw: str                     # 原始文本
    author: str = ""             # 作者
    title: str = ""              # 题目
    year: str = ""               # 年份
    source: str = ""             # 来源（期刊名、出版社等）
    pages: str = ""              # 页码
    position: int = 0            # 在文档中的位置
    line_num: int = 0            # 行号
    footnote_num: Optional[int] = None  # 脚注序号
    reference_num: Optional[int] = None  # 参考文献序号
    in_text_format: str = ""     # 夹注格式（如 "(作者, 年份)"）
    
    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, Citation):
            return False
        # 基于作者、年份、标题的相似度判断
        return (
            self._normalize(self.author) == self._normalize(other.author) and
            self.year == other.year and
            self._normalize(self.title) == self._normalize(other.title)
        )
    
    @staticmethod
    def _normalize(text: str) -> str:
        """文本归一化"""
        if not text:
            return ""
        # 移除空格、标点、全角转半角
        text = re.sub(r'[\s\.\,\;\:\'\"\[\]\(\)]', '', text)
        # 繁简体转换（可选）
        # text = zhconv.convert(text, 'zh-hans')
        return text.lower()


@dataclass
class CrossRefIssue:
    """交叉引用问题"""
    severity: str                # fatal, major, minor, info
    issue_type: str              # 问题类型
    description: str             # 描述
    citation_id: str = ""        # 相关引用ID
    footnote_num: Optional[int] = None
    reference_num: Optional[int] = None
    position: int = 0            # 文档位置
    line_num: int = 0
    suggestion: str = ""         # 建议
    evidence: str = ""           # 证据


class CitationCrossReferencer:
    """引用交叉对比器"""
    
    def __init__(self, text: str, paper_type: str = "unknown"):
        self.text = text
        self.paper_type = paper_type
        self.footnotes: List[Citation] = []
        self.in_text_citations: List[Citation] = []
        self.references: List[Citation] = []
        self.issues: List[CrossRefIssue] = []
        self.used_ref_ids: Set[int] = set()  # 记录被引用的参考文献序号
        
    def run_analysis(self) -> Dict[str, Any]:
        """
        执行完整的交叉引用分析
        
        Returns:
            包含分析结果的字典
        """
        # 1. 提取所有引用
        self._extract_footnotes()
        self._extract_in_text_citations()
        self._extract_references()
        
        # 2. 执行各项检查
        self._check_footnote_reference_match()
        self._check_reference_cited()
        self._check_duplicate_citations()
        self._check_citation_sequence()
        self._check_in_text_footnote_consistency()
        self._check_reference_format_consistency()
        
        # 3. 生成报告
        return self._generate_report()
    
    def _extract_footnotes(self):
        """提取脚注"""
        # 脚注模式: [1], [2], etc.
        footnote_patterns = [
            r'\[(\d+)\]\s*([^\[\]]+(?:\[[^\[\]]+\][^\[\]]*)*)',  # [1] 脚注内容
            r'^\[(\d+)\]\s+([^\n]+)',  # 行首 [1] 脚注
        ]
        
        lines = self.text.split('\n')
        footnote_map: Dict[int, str] = {}  # 脚注序号 -> 内容
        
        for line_num, line in enumerate(lines):
            line = line.strip()
            for pattern in footnote_patterns:
                for match in re.finditer(pattern, line, re.MULTILINE):
                    fn_num = int(match.group(1))
                    fn_text = match.group(2).strip()
                    
                    # 解析脚注内容
                    citation = self._parse_citation(
                        fn_text,
                        CitationType.FOOTNOTE,
                        match.start(),
                        line_num
                    )
                    citation.footnote_num = fn_num
                    citation.id = f"fn_{fn_num}"
                    
                    self.footnotes.append(citation)
                    footnote_map[fn_num] = fn_text
                    self.used_ref_ids.add(fn_num)
        
        logger.info(f"提取到 {len(self.footnotes)} 条脚注")
    
    def _extract_in_text_citations(self):
        """提取正文夹注"""
        # 夹注模式: (作者, 年份), (作者, 年份: 页码), etc.
        in_text_patterns = [
            r'\(([^\(\)]+?)\s*,\s*(\d{4})\s*(?::\s*(\d+(?:-\d+)?))?\)',  # (作者, 年份: 页码)
            r'\[([^\[\]]+?)\s*,\s*(\d{4})\s*(?::\s*(\d+(?:-\d+)?))?\]',  # [作者, 年份: 页码]
            r'（([^（）]+?)\s*,\s*(\d{4})\s*(?::\s*(\d+(?:-\d+)?))?）',  # （作者, 年份: 页码）
        ]
        
        for match in re.finditer(in_text_patterns, self.text):
            author = match.group(1).strip()
            year = match.group(2)
            pages = match.group(3) or ""
            
            citation = Citation(
                id=f"it_{match.start()}",
                type=CitationType.IN_TEXT,
                raw=match.group(0),
                author=author,
                year=year,
                pages=pages,
                position=match.start(),
                in_text_format=match.group(0)
            )
            
            self.in_text_citations.append(citation)
        
        logger.info(f"提取到 {len(self.in_text_citations)} 条正文夹注")
    
    def _extract_references(self):
        """提取参考文献"""
        # 查找参考文献章节
        ref_section_match = re.search(
            r'(?:参考文献|References|引用文献|Bibliography)[：:\s]*\n(.*?)(?=\n\n|\Z)',
            self.text,
            re.DOTALL | re.IGNORECASE
        )
        
        if not ref_section_match:
            logger.warning("未找到参考文献章节")
            return
        
        ref_text = ref_section_match.group(1)
        lines = ref_text.split('\n')
        
        ref_num = 0
        for line_num, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            
            # 参考文献通常以序号开头
            ref_match = re.match(r'^\[(\d+)\]|\[\d+\]\s*', line)
            if ref_match:
                ref_num = int(ref_match.group(1)) if ref_match.group(1) else ref_num + 1
                
                citation = self._parse_citation(
                    line,
                    CitationType.REFERENCE,
                    ref_section_match.start() + len(ref_text) - len(line),
                    line_num
                )
                citation.id = f"ref_{ref_num}"
                citation.reference_num = ref_num
                
                self.references.append(citation)
        
        logger.info(f"提取到 {len(self.references)} 条参考文献")
    
    def _parse_citation(self, text: str, citation_type: CitationType, 
                        position: int, line_num: int) -> Citation:
        """解析引用文本"""
        citation = Citation(
            id=f"{citation_type.value}_{position}",
            type=citation_type,
            raw=text[:500],  # 限制长度
            position=position,
            line_num=line_num
        )
        
        # 提取作者
        author_patterns = [
            r'^([^\[][\u4e00-\u9fa5a-zA-Z]+?)(?=\s*[,，]|\s*\(|\s*《)',  # 开头作者
            r'([\u4e00-\u9fa5]{2,4})(?:[,，]\s*(?:[\u4e00-\u9fa5]{1,2}\.)*(?:\s*[,，]))',  # 中文作者
            r'([A-Z][a-z]+(?:,\s*[A-Z]\.?)+)',  # 西文作者
        ]
        for pattern in author_patterns:
            match = re.search(pattern, text)
            if match:
                citation.author = match.group(1).strip()
                break
        
        # 提取年份
        year_match = re.search(r'[(（\[（](\d{4})[)）\]]', text)
        if year_match:
            citation.year = year_match.group(1)
        
        # 提取标题（用书名号或引号包裹的部分）
        title_match = re.search(r'[《""''"]([^《""''""《》]+)[》""''""]', text)
        if title_match:
            citation.title = title_match.group(1).strip()
        
        # 提取来源
        source_patterns = [
            r'《([^《》]+)》',  # 书名
            r'《([^《》]+)》第\d+条',  # 法律
            r'(?:发表于|载于|出自)\s*([^\s,，,。]+)',  # 期刊
        ]
        for pattern in source_patterns:
            match = re.search(pattern, text)
            if match:
                citation.source = match.group(1).strip()
                break
        
        return citation
    
    def _check_footnote_reference_match(self):
        """检查脚注与参考文献的匹配"""
        ref_authors_years: Dict[Tuple[str, str], List[Citation]] = defaultdict(list)
        
        for ref in self.references:
            key = (Citation._normalize(ref.author), ref.year)
            ref_authors_years[key].append(ref)
        
        for fn in self.footnotes:
            fn_key = (Citation._normalize(fn.author), fn.year)
            
            # 查找匹配的参考文献
            matching_refs = ref_authors_years.get(fn_key, [])
            
            if not matching_refs:
                # 脚注引用但无对应参考文献 - 可能是严重问题
                self.issues.append(CrossRefIssue(
                    severity="major",
                    issue_type="footnote_missing_reference",
                    description=f"脚注{fn.footnote_num}引用了({fn.author}, {fn.year})，但参考文献中未找到对应条目",
                    citation_id=fn.id,
                    footnote_num=fn.footnote_num,
                    position=fn.position,
                    line_num=fn.line_num,
                    evidence=f"脚注内容: {fn.raw[:100]}",
                    suggestion="请检查参考文献中是否有该文献，或确认脚注格式是否正确"
                ))
    
    def _check_reference_cited(self):
        """检查参考文献是否被引用"""
        cited_refs: Set[int] = set()
        
        # 从脚注收集被引用
        for fn in self.footnotes:
            if fn.footnote_num:
                cited_refs.add(fn.footnote_num)
        
        # 从正文夹注收集被引用（通过作者年份匹配）
        in_text_keys: Set[Tuple[str, str]] = set()
        for cit in self.in_text_citations:
            in_text_keys.add((Citation._normalize(cit.author), cit.year))
        
        uncited_refs: List[Citation] = []
        inconsistent_refs: List[Citation] = []
        
        for ref in self.references:
            ref_num = ref.reference_num
            
            # 检查序号引用
            if ref_num and ref_num not in cited_refs:
                # 检查是否有正文夹注
                ref_key = (Citation._normalize(ref.author), ref.year)
                if ref_key in in_text_keys:
                    # 引用了但格式不统一
                    inconsistent_refs.append(ref)
                else:
                    # 完全没有引用
                    uncited_refs.append(ref)
        
        # 报告未被引用的参考文献（降低严重性）
        for ref in uncited_refs:
            self.issues.append(CrossRefIssue(
                severity="minor",  # 注意：这是minor而非major
                issue_type="reference_not_cited",
                description=f"参考文献{ref.reference_num}在正文中未被引用",
                citation_id=ref.id,
                reference_num=ref.reference_num,
                position=ref.position,
                line_num=ref.line_num,
                evidence=f"文献内容: {ref.raw[:100]}",
                suggestion="如果该文献确实未被引用，建议删除；如果确实引用了，请检查脚注序号"
            ))
        
        # 报告正文引用但无脚注序号
        for ref in inconsistent_refs:
            self.issues.append(CrossRefIssue(
                severity="major",
                issue_type="citation_format_inconsistent",
                description=f"参考文献{ref.reference_num}在正文中以夹注形式引用，但缺少对应脚注序号",
                citation_id=ref.id,
                reference_num=ref.reference_num,
                position=ref.position,
                line_num=ref.line_num,
                evidence=f"文献内容: {ref.raw[:100]}",
                suggestion="请在引用处添加脚注序号，保持引用格式统一"
            ))
    
    def _check_duplicate_citations(self):
        """检查重复引用"""
        fn_signatures: Dict[str, List[Citation]] = defaultdict(list)
        
        for fn in self.footnotes:
            sig = f"{Citation._normalize(fn.author)}_{fn.year}"
            fn_signatures[sig].append(fn)
        
        for sig, citations in fn_signatures.items():
            if len(citations) > 1:
                nums = [str(c.footnote_num) for c in citations if c.footnote_num]
                self.issues.append(CrossRefIssue(
                    severity="minor",
                    issue_type="duplicate_citation",
                    description=f"同一文献({citations[0].author}, {citations[0].year})在脚注中出现了多次",
                    citation_id=",".join([c.id for c in citations]),
                    footnote_num=citations[0].footnote_num,
                    position=citations[0].position,
                    line_num=citations[0].line_num,
                    evidence=f"出现位置: 脚注{', '.join(nums)}",
                    suggestion="同一文献仅需在首次引用时标注，后续引用可使用同上、见前注等简写"
                ))
    
    def _check_citation_sequence(self):
        """检查引用序号连续性"""
        if not self.footnotes:
            return
        
        fn_nums = sorted([fn.footnote_num for fn in self.footnotes if fn.footnote_num])
        
        if not fn_nums:
            return
        
        # 检查是否有跳跃
        expected = 1
        for num in fn_nums:
            if num != expected and num > expected:
                # 有遗漏的序号
                missing = list(range(expected, num))
                self.issues.append(CrossRefIssue(
                    severity="minor",
                    issue_type="footnote_sequence_gap",
                    description=f"脚注序号存在跳跃，遗漏了序号: {missing}",
                    position=self.footnotes[0].position,
                    suggestion="请检查是否有脚注被删除但序号未重新编排"
                ))
                expected = num + 1
            elif num < expected:
                # 序号重复或乱序
                self.issues.append(CrossRefIssue(
                    severity="minor",
                    issue_type="footnote_sequence_error",
                    description=f"脚注序号 {num} 出现在预期序号 {expected} 之前",
                    footnote_num=num,
                    position=self.footnotes[0].position,
                    suggestion="请重新编排脚注序号，确保顺序连续"
                ))
            else:
                expected = num + 1
    
    def _check_in_text_footnote_consistency(self):
        """检查正文夹注与脚注的一致性"""
        in_text_keys: Dict[Tuple[str, str], Citation] = {}
        
        for cit in self.in_text_citations:
            key = (Citation._normalize(cit.author), cit.year)
            if key not in in_text_keys:
                in_text_keys[key] = cit
        
        for fn in self.footnotes:
            fn_key = (Citation._normalize(fn.author), fn.year)
            if fn_key in in_text_keys:
                # 检查年份是否一致
                it_cit = in_text_keys[fn_key]
                if fn.year != it_cit.year:
                    self.issues.append(CrossRefIssue(
                        severity="major",
                        issue_type="year_mismatch",
                        description=f"同一作者({fn.author})的引用年份不一致：脚注为{fn.year}，夹注为{it_cit.year}",
                        citation_id=f"{fn.id},{it_cit.id}",
                        footnote_num=fn.footnote_num,
                        position=min(fn.position, it_cit.position),
                        evidence=f"脚注: {fn.raw[:80]}\n夹注: {it_cit.raw}",
                        suggestion="请核实正确的发表年份并保持一致"
                    ))
    
    def _check_reference_format_consistency(self):
        """检查参考文献格式一致性"""
        if not self.references:
            return
        
        # 按类型分组检查
        ref_types: Dict[str, List[Citation]] = defaultdict(list)
        for ref in self.references:
            ref_types[ref._normalize(ref.source)].append(ref)
        
        # 检查是否有混合格式
        mixed_type = None
        for ref_type, refs in ref_types.items():
            if len(refs) > 2:  # 同类型超过2条才检查
                # 检查序号格式
                formats = set()
                for ref in refs:
                    if re.match(r'^\[\d+\]', ref.raw):
                        formats.add("bracket")
                    elif re.match(r'^\d+\.', ref.raw):
                        formats.add("dot")
                
                if len(formats) > 1:
                    mixed_type = ref_type
                    break
        
        if mixed_type:
            self.issues.append(CrossRefIssue(
                severity="minor",
                issue_type="reference_format_inconsistent",
                description=f"参考文献中序号格式不统一（混用方括号[]和数字.）",
                position=self.references[0].position,
                suggestion="请统一参考文献的序号格式，建议使用国标格式"
            ))
    
    def _generate_report(self) -> Dict[str, Any]:
        """生成分析报告"""
        # 按严重性分组
        by_severity: Dict[str, List[Dict]] = {
            "fatal": [],
            "major": [],
            "minor": [],
            "info": []
        }
        
        for issue in self.issues:
            by_severity[issue.severity].append({
                "type": issue.issue_type,
                "description": issue.description,
                "suggestion": issue.suggestion,
                "evidence": issue.evidence,
                "position": issue.position,
                "line_num": issue.line_num,
                "citation_id": issue.citation_id,
            })
        
        report = {
            "summary": {
                "total_issues": len(self.issues),
                "by_severity": {
                    k: len(v) for k, v in by_severity.items()
                },
                "total_footnotes": len(self.footnotes),
                "total_in_text_citations": len(self.in_text_citations),
                "total_references": len(self.references),
            },
            "issues_by_severity": by_severity,
            "issues": [
                {
                    "severity": issue.severity,
                    "type": issue.issue_type,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                    "evidence": issue.evidence,
                }
                for issue in self.issues
            ],
            "citations": {
                "footnotes": [
                    {
                        "num": fn.footnote_num,
                        "author": fn.author,
                        "year": fn.year,
                        "raw": fn.raw[:100],
                    }
                    for fn in self.footnotes
                ],
                "in_text": [
                    {
                        "author": cit.author,
                        "year": cit.year,
                        "raw": cit.raw,
                    }
                    for cit in self.in_text_citations
                ],
                "references": [
                    {
                        "num": ref.reference_num,
                        "author": ref.author,
                        "title": ref.title,
                        "year": ref.year,
                        "raw": ref.raw[:100],
                    }
                    for ref in self.references
                ]
            }
        }
        
        return report


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="脚注、正文、参考文献交叉引用对比分析"
    )
    parser.add_argument(
        "input_file",
        help="输入文本文件路径（从PDF/DOCX提取的文本）"
    )
    parser.add_argument(
        "-o", "--output",
        help="输出JSON报告路径"
    )
    parser.add_argument(
        "-t", "--type",
        default="unknown",
        choices=["bachelor", "master", "phd", "journal", "course", "unknown"],
        help="论文类型"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="静默模式"
    )
    
    args = parser.parse_args()
    
    if not Path(args.input_file).exists():
        print(f"错误: 文件不存在: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    
    with open(args.input_file, 'r', encoding='utf-8') as f:
        text = f.read()
    
    analyzer = CitationCrossReferencer(text, args.type)
    report = analyzer.run_analysis()
    
    # 输出
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        if not args.quiet:
            print(f"✓ 报告已保存到: {args.output}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
