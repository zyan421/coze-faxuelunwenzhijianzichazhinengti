# anchor_text 主路径定位机制（v2.5/v2.6 修复）

## 1. 背景：v2.0 批注定位严重错位

v2.0 实测刘梓璇论文时，20 条批注中 17 条挂错位置：

- 6 条批注挤在"湘潭大学毕业论文"封面标题
- 致谢段"感谢母校的每一次托举"挂着"【实证】【致命】" 批注
- 学生和老师**完全无法使用**

## 2. 两个互相叠加的根因

### Bug 1：段索引漏算 sdt 内段

论文的"目录"被 Word 自动放进 `<w:sdt>`（Structured Document Tag，结构化文档标签）容器里——这是 Word 自动生成 TOC 字段时的常见做法。

v2 的 `locate_paragraph()` 函数只取 body 直接子节点的 `<w:p>`，**跳过 sdt 内部的 40+ 段目录段落**。但 Agent 生成 issues.json 时用的是"递归 walk 跳过表格内段"——把 sdt 内段也算了进去。两套算法的索引基数**相差 40+**，所有批注偏移到错误的段。

### Bug 2：anchor_text 字段定义了但根本没用

schema 早就要求每条 issue 必填 `anchor_text` 字段（≤ 60 字符的文本锚点），但 v2 的 `locate_by_issue()` 函数**完全没用这个字段**——纯靠 `paragraph_index` 数字索引。这是**文档与实现的严重脱节**。

## 3. v2.5 的修复方案

### 修复 1：递归收集段落（_collect_body_paragraphs）

```python
def _collect_body_paragraphs(body):
    """递归遍历 body，收集所有非表格内段。能正确处理 <w:sdt> 等容器。"""
    out = []
    def walk(elem, in_table):
        for child in elem:
            if child.tag == f"{W_NS}p" and not in_table:
                out.append(child)
            elif child.tag == f"{W_NS}tbl":
                continue  # 表格内段不计入主索引
            else:
                walk(child, in_table=in_table)
    walk(body, in_table=False)
    return out
```

### 修复 2：anchor_text 升级为主定位手段

新的优先级（`locate_by_issue` 函数）：

1. **表格四字段联合定位**（locate_paragraph_in_cell）—— 最精确
2. **anchor_text 全文搜索**（locate_by_anchor_text）—— v2.5 主路径
3. **paragraph_index 数字索引**（locate_paragraph）—— 仅当 anchor_text 缺失时
4. **章节首段降级**（fallback_to_chapter_head）—— 最后兜底

### 修复 3：目录条目识别（_is_toc_entry）

v2.6 增强：当 anchor_text 在多个段都出现时（如"二元责任规范模式"在目录有、正文也有），优先选择**非目录条目**的段：

```python
def _is_toc_entry(text):
    """识别目录条目：短文本 + 末尾页码"""
    if not text or len(text) > 100:
        return False
    stripped = text.strip()
    if re.search(r"\d{1,4}\s*$", stripped):
        if len(stripped) < 80:
            return True
    return False
```

### 修复 4：字级精确高亮

`determine_offsets()` 改进：若 anchor_text 在段内找得到，直接高亮 anchor 那几个字（而不是整段）：

```python
anchor = issue.get("anchor_text") or ""
if anchor:
    a_start = locate_anchor_offset_in_paragraph(paragraph, anchor)
    if a_start is not None:
        a_len = min(len(anchor.strip()), 30)
        return (a_start, min(a_start + a_len, para_len))
```

## 4. v2.6 的细化优化

- 增强 `_is_toc_entry()` 正则：识别"中文紧贴数字"目录形态（如"...制度构造31"）
- 当 anchor 仅在脚注 XML 中出现（不在主文档 `<w:t>`）时，v2.6 已知局限——`locate_by_anchor_text` 返回 None，issue 退到章节首段。**v2.7 候选**：扩展搜索路径到 `word/footnotes.xml`

## 5. 实测效果

| 版本 | 段落定位算法 | 主定位手段 | 批注命中率 |
|---|---|---|---|
| v2.0 | body 直属（漏 sdt） | paragraph_index 数字索引 | **3/20**（湘大标题 + 致谢段挤批注） |
| v2.5 | 递归（含 sdt） | anchor_text | 20/20 |
| v2.6 | 同上 + 目录条目识别 | anchor_text + 排除目录区 | **22/22**（刘梓璇）/ **27/27**（胡文仲）/ **19/19**（谢佳谕） |

## 6. Agent 生成 anchor_text 的注意事项

写 issues.json 时，每条 issue 的 `anchor_text` 字段应当：

1. **长度 ≤ 30 字**（v2.5 自动截断 30 字以上）
2. **使用论文原文的精确表达**（半角/全角、空格、标点必须与原文一致）
3. **首选**：批注高亮目标的关键词或短语本身（如笔误"是去真实性"）
4. **次选**：能唯一定位段落的特征短语（如"二元责任规范模式的制度构造"）
5. **禁止**：泛泛的概念词（如"实证"、"分析"、"问题"）——容易在多段中误命中

如果同一个 anchor 在多段中都出现，应当：

- 优先匹配正文段（自动排除目录条目）
- 使用更长的特征短语（包含足够上下文）
- 实在不能区分时，结合 `locator.chapter` 字段做章节范围限制（v2.7 候选）

## 7. PDF 输入的特殊建议（v2.7 新增）

### 7.1 PDF 排版中的"隐形空格"陷阱

LaTeX / Word 导出的 PDF 在排版时常在以下位置**插入空格**，而 Agent 写 anchor 时基于"逻辑文本"看不到这些空格：

| 边界类型 | 逻辑文本 | PDF 实际字符 |
|---|---|---|
| 中文 + 数字 | "第188条" | "第 188 条" |
| 数字 + 中文 | "2017年" | "2017 年" |
| 中文 + 英文 | "法官Judge" | "法官 Judge" |
| 中文 + 标点 + 数字 | "（2024）京民终666号" | "（2024）京 民 终 666 号" |

PyMuPDF 的 `search_for` 是字符级严格匹配，遇到 PDF 中的空格变体会**完全搜不到**——v2.6 实测中有 3 条 anchor 因此降级为左上角便签（被用户错过）。

### 7.2 v2.7 两层修复

**工具层修复**（`tools/annotate-pdf.py` 已实现）：
- 新增 `_generate_space_tolerant_variants()` 函数
- 当 anchor 在 PDF 中搜不到时，自动尝试 4 种空格变体（中文↔数字、中文↔字母边界插入空格）
- v2.6 失败的两条 anchor（"该法第188条首次"、"有该法第30条明确列举"）在 v2.7 全部救回

**规则层建议**（Agent 写 anchor 时应当遵循）：

#### 优先级 1：用连续中文（无数字/字母混入）的短句作 anchor

- ❌ "该法第188条首次" → PDF 中是 "该法第 188 条 首次"，严格搜索失败
- ✅ "徇私枉法、滥用裁判权" → 同句中纯中文短语，鲁棒

#### 优先级 2：必须用含数字的 anchor 时，提前考虑空格变体

- 如果必须用数字（如 "33929.6亿元"），尽量选数字两侧本来就有空格的位置
- 或直接信任 v2.7 工具层的容错搜索

#### 优先级 3：避免短全大写英文 + 中文混排

- ❌ "GEMA vs OpenAI 案" → PDF 中可能是 "GEMA  vs  OpenAI 案"，双空格搜不到
- ✅ "Munich Regional Court" 或 "深度参与标准" 等纯英文 / 纯中文短语

### 7.3 anchor 长度建议

- PDF 输入下，anchor 推荐长度 **10-20 字**（既能唯一定位，又不容易跨页或跨段断裂）
- 太长的 anchor（如完整一句 50 字）在 PDF 中可能因换行符插入而搜索失败
- 太短（如 5 字以下）容易在多段误命中

## 8. Agent 生成 anchor_text 的注意事项（所有输入类型通用）

写 issues.json 时，每条 issue 的 `anchor_text` 字段应当：

1. **长度 ≤ 30 字**（v2.5 自动截断 30 字以上；PDF 推荐 10-20 字）
2. **使用论文原文的精确表达**（半角/全角、空格、标点必须与原文一致）
3. **首选**：批注高亮目标的关键词或短语本身（如笔误"是去真实性"）
4. **次选**：能唯一定位段落的特征短语（如"二元责任规范模式的制度构造"）
5. **禁止**：泛泛的概念词（如"实证"、"分析"、"问题"）——容易在多段中误命中
6. **PDF 输入特别注意**：参见 § 7，优先选连续中文短句作 anchor

如果同一个 anchor 在多段中都出现，应当：

- 优先匹配正文段（自动排除目录条目）
- 使用更长的特征短语（包含足够上下文）
- 实在不能区分时，结合 `locator.chapter` 字段做章节范围限制（v2.7 候选）

## 9. 与 v2 的向后兼容

旧的 issues.json（仅有 `paragraph_index`、无 `anchor_text`）仍能工作（走数字索引路径）。但 v2.6 起 schema 已要求 `anchor_text` 必填，**新生成的 issues.json 都应当带 anchor_text**。
