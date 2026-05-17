# OOXML 样式层核查指引（v2.6 新增）

## 1. 背景

v2.0-v2.5 的 `tools/extract-docx.py` **只读 `<w:t>` 文本节点**，完全忽略 OOXML 的样式与属性层（`<w:pStyle>`、`<w:numPr>`、`<w:rPr>`）。这导致以下 bug 在实战中重复出现：

### Bug 案例 1：刘梓璇论文"第四章遗漏章号"误报

- 现象：v2.5 报告"第四章正文标题遗漏了章号'四、'"
- 实情：第四章标题使用了 Word 自动编号（`<w:numPr>` numId=1），"四、"是渲染时由编号系统注入的字符，文本节点里看不到。其他五章用手写"X、"前缀
- 后果：把"局部使用自动编号"误判为"漏字"——给学生错误指导

### Bug 案例 2：胡文仲论文"章节标题样式混乱"未发现

- 实情：一、二章 pStyle=TOC1（**目录样式被错用作正文章节样式**），三、五章 pStyle=none，第四章独家使用 `<w:numPr>` numId=1。混用容易在以下场景出错：
  - 转 PDF/Markdown/LaTeX 时"四、"丢失
  - 目录交叉引用错位
  - 章节插入/移动时跳号
- v2.5 完全没发现这个问题

### Bug 案例 3：谢佳谕论文"章节标题全部 pStyle=none"

- 实情：七章正文标题全部用 pStyle=none，未应用 Word 内置"标题 1"样式。后果：手敲的目录不会自动联动正文标题
- v2.5 同样无法发现

**统一根因**：抽取链路完全没读 OOXML 的样式属性层。**对法学论文这种规范敏感的文体损失很大**。

## 2. v2.6 强制审查项

每次审查 docx 论文，Agent 必须用以下方法对样式层做核查：

### 2.1 章节标题样式核查

```python
import zipfile, xml.etree.ElementTree as ET
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

with zipfile.ZipFile(docx_path) as z:
    with z.open('word/document.xml') as f:
        tree = ET.parse(f)

body = tree.getroot().find(f'{W}body')

# 收集正文段（不含表格内段）
paras = []
def walk(elem, in_table):
    for c in elem:
        if c.tag == f'{W}p' and not in_table: paras.append(c)
        elif c.tag == f'{W}tbl': continue
        else: walk(c, in_table)
walk(body, False)

# 章节标题检查
for i, p in enumerate(paras):
    text = ''.join(t.text or '' for t in p.iter(f'{W}t')).strip()
    if any(text.startswith(kw) for kw in ['一、', '二、', '三、', '四、', '五、', '六、', '七、']):
        pStyle = p.find(f'.//{W}pStyle')
        numPr = p.find(f'.//{W}numPr')
        style_val = pStyle.attrib.get(f'{W}val') if pStyle is not None else 'none'
        has_num = '✓自动编号' if numPr is not None else ''
        print(f"  段{i}: pStyle={style_val} {has_num} | '{text[:50]}'")
```

### 2.2 检查清单

对照上面的输出，Agent 应在 issues.json 中检查以下情况：

| 情况 | 严重度 | issue 类别 | problem 描述 |
|---|---|---|---|
| 多章中**部分**使用 `<w:numPr>` 自动编号、部分手写 | minor | structure | "章节编号混用：第 X 章使用 Word 自动编号，其余章手写。混用易在转格式、章节移动等场景出错" |
| 章节标题使用 **pStyle=TOC1 或 pStyle=6**（目录样式） | minor | structure | "目录样式被错用作正文章节样式" |
| 章节标题**全部 pStyle=none**（未应用任何标题样式） | minor | structure | "章节标题未应用 Word 内置'标题 1'样式，目录不会自动联动正文标题" |
| 章节标题样式**前后不一致**（如一二章 pStyle=A，三四章 pStyle=B） | minor | structure | "章节标题样式不统一，转 PDF 时书签层级混乱" |

### 2.3 修正诊断的关键原则

**当文本节点中看不到某段开头的"X、"时，第一反应是去检查 `<w:numPr>`，而不是直接判定为"漏字"**。

```python
# 错误的诊断：
# if not text.startswith('四、'):
#     create_issue("第四章遗漏章号'四、'")

# 正确的诊断：
if not text.startswith('四、'):
    numPr = p.find(f'.//{W}numPr')
    if numPr is not None:
        # 自动编号注入的 '四、'，不是漏字
        # 但仍可检查：是否与其他章的编号方式一致
        check_chapter_numbering_consistency()
    else:
        # 确实漏字
        create_issue("第四章正文标题遗漏章号'四、'")
```

## 3. 表格层核查

详见 `rules/table-audit.md`。

## 4. v2.7 候选改造

本规则文件是 v2.6 的过渡方案——通过让 Agent 在审查前手动跑 OOXML 检查代码来弥补抽取层的盲区。**v2.7 计划**：

1. 升级 `tools/extract-docx.py`：输出 JSON 时为每段添加 `style_info` 字段（pStyle, numPr.numId, numPr.ilvl, rPr.bold 等）
2. 新建 `tools/ooxml-style-check.py`：专门扫描"样式不一致"问题，自动生成对应 issue
3. 让 Agent 不再需要手写 XML 解析代码——直接调用工具
