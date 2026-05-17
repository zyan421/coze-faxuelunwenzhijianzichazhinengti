#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF文本抽取脚本 (extract-pdf-text.py)

从文本型PDF中提取文本内容，检测扫描件并给出明确提示。
支持输出结构化JSON元数据和纯文本。
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import pymupdf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PDFTextExtractor:
    """PDF文本提取器"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.doc = None
        self.metadata: Dict[str, Any] = {}
        
    def open(self) -> bool:
        """打开PDF文件"""
        try:
            self.doc = pymupdf.open(self.file_path)
            self._extract_metadata()
            return True
        except Exception as e:
            logger.error(f"无法打开PDF文件: {e}")
            return False
    
    def _extract_metadata(self):
        """提取PDF元数据"""
        if self.doc:
            self.metadata = {
                "page_count": len(self.doc),
                "title": self.doc.metadata.get("title", ""),
                "author": self.doc.metadata.get("author", ""),
                "subject": self.doc.metadata.get("subject", ""),
                "creator": self.doc.metadata.get("creator", ""),
                "producer": self.doc.metadata.get("producer", ""),
            }
    
    def extract_text(self) -> Tuple[str, Dict[str, Any]]:
        """
        提取PDF全文文本
        
        Returns:
            (text, extraction_report): 文本内容和提取报告
        """
        if not self.doc:
            return "", {"error": "PDF未打开"}
        
        all_text = []
        page_reports = []
        total_chars = 0
        empty_pages = []
        scanned_indicators = []
        
        for page_num, page in enumerate(self.doc, 1):
            page_text = page.get_text("text")
            
            # 清理文本
            page_text = self._clean_text(page_text)
            
            char_count = len(page_text.strip())
            total_chars += char_count
            
            page_report = {
                "page": page_num,
                "char_count": char_count,
                "has_text": char_count > 50,
                "preview": page_text[:200] if page_text else ""
            }
            
            if char_count <= 50:
                empty_pages.append(page_num)
            
            # 检测扫描件指标
            if self._is_likely_scanned(page_text, page_num):
                scanned_indicators.append(page_num)
            
            all_text.append(f"[第{page_num}页]\n{page_text}")
            page_reports.append(page_report)
        
        # 构建提取报告
        extraction_report = {
            "page_count": len(self.doc),
            "total_chars": total_chars,
            "empty_pages": empty_pages,
            "scanned_suspicion_pages": scanned_indicators,
            "is_likely_scanned": len(scanned_indicators) > len(self.doc) * 0.5,
            "page_reports": page_reports,
        }
        
        return "\n\n".join(all_text), extraction_report
    
    def _clean_text(self, text: str) -> str:
        """清理提取的文本"""
        if not text:
            return ""
        
        # 移除多余的空白字符
        text = re.sub(r'\s+', ' ', text)
        
        # 移除孤立字符行（通常是OCR错误）
        lines = text.split(' ')
        cleaned_lines = []
        for line in lines:
            if len(line) > 1 or line.isalnum():
                cleaned_lines.append(line)
        
        return ' '.join(cleaned_lines).strip()
    
    def _is_likely_scanned(self, page_text: str, page_num: int) -> bool:
        """
        检测页面是否可能是扫描件
        
        扫描件特征：
        - 几乎没有可提取的文本
        - 文本高度碎片化
        - 包含大量乱码或特殊字符
        """
        if not page_text.strip():
            return True
        
        # 检查文本密度（实际字符 vs 总字符）
        alpha_ratio = sum(c.isalnum() for c in page_text) / max(len(page_text), 1)
        if alpha_ratio < 0.1:
            return True
        
        # 检查是否包含常见OCR错误模式
        ocr_error_patterns = [
            r'[█▓▒░]',  # 填充块字符
            r'[□■☆★○●]',  # 特殊符号过多
            r'\.{5,}',  # 连续点过多
        ]
        
        for pattern in ocr_error_patterns:
            if re.search(pattern, page_text):
                return True
        
        return False
    
    def close(self):
        """关闭PDF文件"""
        if self.doc:
            self.doc.close()
            self.doc = None
    
    def extract_structured(self) -> Dict[str, Any]:
        """
        提取结构化信息（标题、章节、脚注、参考文献等）
        """
        text, _ = self.extract_text()
        
        result = {
            "metadata": self.metadata,
            "text": text,
            "structure": self._analyze_structure(text),
            "footnotes": self._extract_footnotes(text),
            "references": self._extract_references(text),
            "legal_citations": self._extract_legal_citations(text),
            "case_citations": self._extract_case_citations(text),
        }
        
        return result
    
    def _analyze_structure(self, text: str) -> Dict[str, Any]:
        """分析论文结构"""
        lines = text.split('\n')
        
        structure = {
            "title": "",
            "chapters": [],
            "sections": [],
            "total_headings": 0
        }
        
        # 匹配章节标题模式
        heading_patterns = [
            r'^第[一二三四五六七八九十百\d]+[章节部篇]',  # 第X章、第X节
            r'^[一二三四五六七八九十百\d]+[、\.](?!\d)',  # X. 标题
            r'^\d+\.\d+(?!\d)',  # 1.1 格式
            r'^(?:摘要|Abstract|引言|Introduction|结论|Conclusion|参考文献|致谢)',  # 特殊章节
        ]
        
        in_chapter = False
        current_chapter = None
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            
            # 检测是否为标题
            is_heading = False
            for pattern in heading_patterns:
                if re.match(pattern, line):
                    is_heading = True
                    structure["total_headings"] += 1
                    break
            
            if is_heading:
                if not structure["title"]:
                    structure["title"] = line[:100]
                elif not in_chapter:
                    in_chapter = True
                    current_chapter = {
                        "title": line,
                        "line_num": i,
                        "sections": []
                    }
                    structure["chapters"].append(current_chapter)
                else:
                    if current_chapter:
                        current_chapter["sections"].append({
                            "title": line,
                            "line_num": i
                        })
                    structure["sections"].append({
                        "title": line,
                        "line_num": i
                    })
        
        return structure
    
    def _extract_footnotes(self, text: str) -> List[Dict[str, str]]:
        """提取脚注"""
        footnotes = []
        
        # 脚注通常在页面底部或以数字序号开始
        footnote_patterns = [
            r'^\[\d+\]\s*(.+)',  # [1] 格式
            r'^\d+、(.+)',  # 1、格式
            r'^[a-zA-Z]\d*\s+(.+)',  # a1 格式
        ]
        
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()
            for pattern in footnote_patterns:
                match = re.match(pattern, line)
                if match:
                    footnotes.append({
                        "text": match.group(1)[:200],
                        "line_num": i,
                        "raw": line
                    })
                    break
        
        return footnotes[:100]  # 限制数量
    
    def _extract_references(self, text: str) -> List[Dict[str, str]]:
        """提取参考文献"""
        references = []
        
        # 查找参考文献章节
        ref_section_match = re.search(
            r'(?:参考文献|References|引用文献)[：:]\s*\n(.*?)(?=\n(?:脚注|注释|正文|$))',
            text,
            re.DOTALL | re.IGNORECASE
        )
        
        if ref_section_match:
            ref_text = ref_section_match.group(1)
            lines = ref_text.split('\n')
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # 参考文献通常以序号开头
                ref_match = re.match(r'^\[\d+\]|\d+\.|^[\[（\(]\d+[\]）\)]', line)
                if ref_match:
                    references.append({
                        "text": line[:300],
                        "type": self._detect_ref_type(line)
                    })
        
        return references[:100]  # 限制数量
    
    def _detect_ref_type(self, text: str) -> str:
        """检测参考文献类型"""
        if re.search(r'[《》\[\(（]法|\[法\]|F\d+', text):
            return "law"
        if re.search(r'\d{4}[年]?\s*[第卷期]?\s*\d+|\(\d{4}\)', text):
            return "journal"
        if re.search(r'(?:北京|上海|广州|人民出版社|法律出版社)', text):
            return "book"
        if re.search(r'(?:博士|硕士)学位论文', text):
            return "thesis"
        if re.search(r'http[s]?://|www\.', text):
            return "web"
        if re.search(r'[A-Z][a-z]+,\s*[A-Z]\.', text):
            return "foreign"
        return "other"
    
    def _extract_legal_citations(self, text: str) -> List[Dict[str, str]]:
        """提取法律条文引用"""
        citations = []
        
        # 匹配法条引用模式
        legal_patterns = [
            r'《([^《》]+)》第(\d+)[条款节章部]',
            r'(?:第)(\d+)(?:条|款|节)(?:之(\d+))?',
            r'《([^《》]+)》第(\d+)条(?:\s*第(\d+)款)?',
        ]
        
        for pattern in legal_patterns:
            for match in re.finditer(pattern, text):
                citations.append({
                    "text": match.group(0),
                    "law": match.group(1) if match.lastindex >= 1 else "",
                    "article": match.group(2) if match.lastindex >= 2 else "",
                    "paragraph": match.group(3) if match.lastindex >= 3 else "",
                })
        
        return citations[:50]
    
    def _extract_case_citations(self, text: str) -> List[Dict[str, str]]:
        """提取案例引用"""
        citations = []
        
        # 匹配案例引用模式
        case_patterns = [
            r'([^\s]+)\s*案\s*(?:第?\s*(\d+)\s*号)?',
            r'(?:最高法|最高人民法院|最高检|最高人民检察院)(?:民|刑|行|知)(?:终|再|二)?(?:?:字|)\s*第?\s*(\d+)\s*号',
            r'\[(\d{4})\]\s*(?:最高法|最高人民法院)(?:民|刑|行|知).*?第?\s*(\d+)\s*号',
        ]
        
        for pattern in case_patterns:
            for match in re.finditer(pattern, text):
                citations.append({
                    "text": match.group(0),
                    "year": match.group(1) if match.lastindex >= 1 else "",
                    "case_num": match.group(2) if match.lastindex >= 2 else "",
                })
        
        return citations[:30]
    
    def __enter__(self):
        self.open()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="从PDF中提取文本内容，支持结构化分析"
    )
    parser.add_argument(
        "input_file",
        help="输入PDF文件路径"
    )
    parser.add_argument(
        "-o", "--output",
        help="输出文本文件路径（可选）"
    )
    parser.add_argument(
        "-j", "--json",
        action="store_true",
        help="输出JSON格式的完整报告"
    )
    parser.add_argument(
        "-s", "--structured",
        action="store_true",
        help="输出结构化分析（标题、脚注、参考文献等）"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="静默模式，只输出结果"
    )
    
    args = parser.parse_args()
    
    if not Path(args.input_file).exists():
        print(f"错误: 文件不存在: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    
    with PDFTextExtractor(args.input_file) as extractor:
        if args.structured or args.json:
            result = extractor.extract_structured()
        else:
            text, report = extractor.extract_text()
            result = {
                "text": text,
                "report": report
            }
    
    # 输出结果
    if args.json or args.structured:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
        
        if not args.quiet:
            print("\n" + "="*60)
            print("提取报告")
            print("="*60)
            report = result.get("report", {})
            print(f"页数: {report.get('page_count', 0)}")
            print(f"总字符数: {report.get('total_chars', 0):,}")
            
            if report.get("empty_pages"):
                print(f"⚠️ 空/少文本页面: {report['empty_pages']}")
            
            if report.get("is_likely_scanned"):
                print("⚠️ 警告: 该PDF可能是扫描件，无法提取文本")
                print("   建议: 请使用OCR转换后的文本型PDF，或提供Word版本")
    
    # 保存到文件
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            if args.json or args.structured:
                json.dump(result, f, ensure_ascii=False, indent=2)
            else:
                f.write(result["text"])
        
        if not args.quiet:
            print(f"\n✓ 文本已保存到: {args.output}")


if __name__ == "__main__":
    main()
