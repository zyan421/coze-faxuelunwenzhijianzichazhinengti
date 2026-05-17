#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF批注注入脚本 (annotate-pdf.py)

向PDF文档中注入嵌入式高亮批注，支持：
1. 文本高亮
2. 嵌入式注释（不依赖XFDF）
3. 书签标注
4. 多种输出格式
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import pymupdf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Severity(Enum):
    """问题严重程度"""
    FATAL = "fatal"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


@dataclass
class PDFAnnotation:
    """PDF批注对象"""
    id: str
    severity: Severity
    issue_type: str
    description: str
    location: str
    suggestion: str
    highlight_text: str = ""
    evidence: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result['severity'] = self.severity.value
        return result


@dataclass
class AnnotationResult:
    """批注结果"""
    success: bool
    output_path: str
    annotations_added: int
    annotations_failed: int
    failed_annotations: List[Dict] = None
    error_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        if self.failed_annotations is None:
            result['failed_annotations'] = []
        return result


class PDFAnnotator:
    """PDF批注器"""
    
    # 严重程度对应的颜色
    SEVERITY_COLORS = {
        Severity.FATAL: (1, 0, 0),        # 红色
        Severity.MAJOR: (1, 0.4, 0),      # 橙色
        Severity.MINOR: (1, 0.8, 0),      # 黄色
        Severity.INFO: (0, 0, 1),         # 蓝色
    }
    
    # 严重程度对应的图标
    SEVERITY_ICONS = {
        Severity.FATAL: "✕",
        Severity.MAJOR: "⚠",
        Severity.MINOR: "○",
        Severity.INFO: "ℹ",
    }
    
    def __init__(self, input_path: str, output_path: str):
        self.input_path = input_path
        self.output_path = output_path
        self.doc: Optional[pymupdf.Document] = None
        self.annotations: List[PDFAnnotation] = []
        self.failed: List[Dict] = []
        
    def load(self) -> bool:
        """加载PDF"""
        try:
            self.doc = pymupdf.open(self.input_path)
            return True
        except Exception as e:
            logger.error(f"无法加载PDF: {e}")
            return False
    
    def add_annotation(self, annotation: PDFAnnotation) -> bool:
        """
        添加单条批注
        
        Returns:
            是否成功
        """
        if not self.doc:
            logger.error("PDF未加载")
            return False
        
        try:
            added = False
            
            # 尝试通过文本定位
            if annotation.highlight_text:
                added = self._add_annotation_by_text(annotation)
            
            # 尝试通过位置解析
            if not added and annotation.location:
                added = self._add_annotation_by_location(annotation)
            
            # 失败则记录
            if not added:
                self.failed.append({
                    'annotation': annotation.to_dict(),
                    'reason': '无法定位到PDF中的文本'
                })
            else:
                self.annotations.append(annotation)
            
            return added
            
        except Exception as e:
            logger.error(f"添加批注失败: {e}")
            self.failed.append({
                'annotation': annotation.to_dict(),
                'reason': str(e)
            })
            return False
    
    def _add_annotation_by_text(self, annotation: PDFAnnotation) -> bool:
        """通过文本内容定位并添加批注"""
        highlight_text = annotation.highlight_text.strip()
        if not highlight_text or not self.doc:
            return False
        
        added = False
        
        # 在所有页面搜索
        for page_num, page in enumerate(self.doc):
            # 获取页面文本
            text_dict = page.get_text("dict")
            
            # 搜索匹配文本
            instances = self._find_text_instances(page, highlight_text)
            
            if instances:
                for inst in instances:
                    # 添加高亮
                    self._add_highlight_to_page(
                        page, inst, annotation
                    )
                    added = True
                
                # 只处理第一个匹配
                break
        
        return added
    
    def _find_text_instances(
        self, page, search_text: str
    ) -> List[Dict]:
        """查找文本实例"""
        instances = []
        
        try:
            # 使用PyMuPDF的搜索功能
            rects = page.search_for(search_text, quads=True)
            
            for rect in rects:
                instances.append({
                    'rect': rect,
                    'text': search_text
                })
        except Exception as e:
            logger.debug(f"搜索文本失败: {e}")
        
        return instances
    
    def _add_highlight_to_page(
        self, page, inst: Dict, annotation: PDFAnnotation
    ):
        """在页面添加高亮批注"""
        rect = inst['rect']
        color = self.SEVERITY_COLORS.get(
            annotation.severity, 
            (1, 1, 0)
        )
        
        # 添加高亮注释
        annot = page.add_highlight_annot(rect)
        annot.set_colors(stroke=color)
        
        # 设置注释内容
        prefix = f"{self.SEVERITY_ICONS.get(annotation.severity, '')} "
        content = f"{prefix}{annotation.issue_type}\n\n"
        content += f"问题: {annotation.description}\n\n"
        
        if annotation.suggestion:
            content += f"建议: {annotation.suggestion}\n"
        
        if annotation.evidence:
            content += f"\n证据: {annotation.evidence}"
        
        annot.set_info(content=content)
        annot.update()
        
        # 添加图标标注（如果支持）
        self._add_icon_annot(page, rect, annotation)
    
    def _add_icon_annot(
        self, page, rect, annotation: PDFAnnotation
    ):
        """添加图标标注"""
        try:
            # 在高亮区域旁边添加文本标注
            icon_pos = pymupdf.Rect(
                rect.x1 + 5, rect.y0 - 15,
                rect.x1 + 20, rect.y0
            )
            
            icon = self.SEVERITY_ICONS.get(annotation.severity, "○")
            
            # 添加文本标注
            text_point = pymupdf.Point(icon_pos.x0, icon_pos.y1)
            annot = page.add_freetext_annot(
                text_point,
                icon,
                fontsize=10,
                fontname="helv",
                text_color=self.SEVERITY_COLORS.get(annotation.severity, (0, 0, 0)),
                fill_color=None,
            )
            annot.set_info(content=f"{annotation.issue_type}: {annotation.description[:50]}")
            annot.update()
            
        except Exception as e:
            logger.debug(f"添加图标标注失败: {e}")
    
    def _add_annotation_by_location(
        self, annotation: PDFAnnotation
    ) -> bool:
        """通过位置解析添加批注"""
        if not self.doc:
            return False
        
        # 解析位置信息
        page_num = self._parse_page_number(annotation.location)
        
        if page_num is not None and page_num < len(self.doc):
            page = self.doc[page_num]
            
            # 获取页面中间位置作为示例
            page_rect = page.rect
            sample_rect = pymupdf.Rect(
                page_rect.x0 + 50,
                page_rect.y0 + 50 + (page_num * 20),
                page_rect.x1 - 50,
                page_rect.y0 + 100 + (page_num * 20)
            )
            
            # 添加文本标注
            try:
                text_point = pymupdf.Point(
                    sample_rect.x0,
                    sample_rect.y0 + 10
                )
                
                content = f"{annotation.issue_type}\n"
                content += f"{annotation.description[:100]}\n"
                if annotation.suggestion:
                    content += f"建议: {annotation.suggestion[:50]}"
                
                annot = page.add_freetext_annot(
                    text_point,
                    content,
                    fontsize=9,
                    text_color=self.SEVERITY_COLORS.get(annotation.severity, (0, 0, 0)),
                )
                annot.update()
                
                self.annotations.append(annotation)
                return True
                
            except Exception as e:
                logger.debug(f"通过位置添加批注失败: {e}")
        
        return False
    
    def _parse_page_number(self, location: str) -> Optional[int]:
        """从位置字符串解析页码"""
        # 匹配 "第X页"
        match = re.search(r'第(\d+)页', location)
        if match:
            return int(match.group(1)) - 1  # 转0-based
        
        # 匹配 "page X"
        match = re.search(r'[Pp]age\s*(\d+)', location)
        if match:
            return int(match.group(1)) - 1
        
        return None
    
    def add_annotations_from_issues(
        self, issues: List[Dict]
    ) -> Tuple[int, int, List[Dict]]:
        """批量添加批注"""
        success = 0
        failed = 0
        failed_list = []
        
        for idx, issue in enumerate(issues):
            try:
                severity = Severity(issue.get('severity', 'minor'))
            except ValueError:
                severity = Severity.INFO
            
            annotation = PDFAnnotation(
                id=f"issue_{idx}",
                severity=severity,
                issue_type=issue.get('type', issue.get('issue_type', 'general')),
                description=issue.get('description', ''),
                location=issue.get('location', ''),
                suggestion=issue.get('suggestion', ''),
                highlight_text=issue.get('highlight_text', ''),
                evidence=issue.get('evidence', ''),
            )
            
            if self.add_annotation(annotation):
                success += 1
            else:
                failed += 1
                failed_list.append({
                    'index': idx,
                    'issue': issue
                })
        
        return success, failed, failed_list
    
    def save(self) -> bool:
        """保存PDF"""
        if not self.doc:
            return False
        
        try:
            self.doc.save(self.output_path)
            logger.info(f"PDF已保存: {self.output_path}")
            return True
        except Exception as e:
            logger.error(f"保存PDF失败: {e}")
            return False
    
    def get_result(self) -> AnnotationResult:
        """获取批注结果"""
        return AnnotationResult(
            success=len(self.annotations) > 0,
            output_path=self.output_path,
            annotations_added=len(self.annotations),
            annotations_failed=len(self.failed),
            failed_annotations=self.failed,
        )
    
    def close(self):
        """关闭文档"""
        if self.doc:
            self.doc.close()
            self.doc = None
    
    def __enter__(self):
        self.load()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="向PDF文档中注入嵌入式高亮批注"
    )
    parser.add_argument(
        "input_file",
        help="输入PDF文件路径"
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="输出PDF文件路径"
    )
    parser.add_argument(
        "-i", "--issues",
        required=True,
        help="问题列表JSON文件路径"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="静默模式"
    )
    
    args = parser.parse_args()
    
    if not Path(args.input_file).exists():
        print(f"错误: 输入文件不存在: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    
    if not Path(args.issues).exists():
        print(f"错误: 问题文件不存在: {args.issues}", file=sys.stderr)
        sys.exit(1)
    
    # 加载issues
    with open(args.issues, 'r', encoding='utf-8') as f:
        issues = json.load(f)
    
    if not issues:
        print("警告: 问题列表为空")
        sys.exit(0)
    
    # 创建批注器
    annotator = PDFAnnotator(args.input_path, args.output)
    
    # 批量添加
    success, failed, failed_list = annotator.add_annotations_from_issues(issues)
    
    # 保存
    if annotator.save():
        result = annotator.get_result()
        
        if not args.quiet:
            print(f"✓ 批注添加完成")
            print(f"  成功: {success}")
            print(f"  失败: {failed}")
            print(f"  输出: {args.output}")
        
        # 保存结果JSON
        result_path = args.output.replace('.pdf', '_annotation_result.json')
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        if not args.quiet:
            print(f"  结果: {result_path}")
        
        sys.exit(0)
    else:
        print("错误: 保存PDF失败", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
