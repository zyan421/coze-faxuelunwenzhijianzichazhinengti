# docx 批注注入：OOXML 核心算法与工程约束

本规则文件把 `unified-thesis-reviewer` v2 阶段的 docx 批注注入算法沉淀为 agent 可读的规则。脚本实现在 `tools/inject-docx-comments.py`，输入为 `issues.json` 与原 docx，输出为 `{原名}.annotated.docx`。本文件与即将编写的 `rules/xfdf-annotation.md`（v3 阶段 pdf 旁路）协同，共同承担 `issues.json → 带标注副本` 的落地职责。

核心原则：**只用批注（Word Comments），不修改原文**；**副本写入，原文零动**；**纯 stdlib，零第三方依赖**。

---

## §1 概述：批注 vs 修订

Word 的"标注"机制在 OOXML 层面有两套完全不同的实现：

| 机制 | XML 部件 | 语义 | 用户操作 |
|---|---|---|---|
| **批注（Comments）** | `word/comments.xml` + `commentRangeStart/End` + `commentReference` | 附加在原文上的"旁注气泡"，不改原文 | 右键"删除批注" / 逐条"解决" |
| **修订（Track Changes）** | `word/document.xml` 内嵌 `<w:ins>` / `<w:del>` | 建议性的**改写**，会改变原文本身 | "接受修改" / "拒绝修改" |

本 skill **只用批注**：定位是"指出问题 + 给建议"而非替作者改稿；批注可叠加可嵌套；`w:id` 稳定便于 MD 报告交叉引用；删除批注不留历史堆积。**硬性约束**：永远不要在 `document.xml` 里插入 `<w:ins>` / `<w:del>` / `<w:moveFrom>` / `<w:moveTo>`；永远不要修改 `<w:t>` 的文本内容（除 §5 的 run 拆分机械切分外）。

---

## §2 五步算法总览

给定一条 issue（有合法 `locator`）和一份原 docx，注入流程固定为五步：

```
Step 1  段落定位    locator.paragraph_index → 定位 <w:p> 元素
Step 2  run 展平    段内所有 <w:r> → 字符级序列 [(char, run, rPr)]
Step 3  run 拆分    按 char_offset_in_paragraph 把命中 <w:r> 拆成两个新 run
Step 4  三元标记    插入 <w:commentRangeStart>、<w:commentRangeEnd>、<w:commentReference>
Step 5  三部件写回  comments.xml / commentsExtended.xml / commentsIds.xml，
                    并更新 [Content_Types].xml 与 word/_rels/document.xml.rels
```

步骤之间存在严格顺序依赖：**只有先拆 run（Step 3），Step 4 的三元标记才能精确落在段内字符之间的"兄弟位置"**。跳过 Step 3 直接插，标记只能挂到整段起/终，所有字级定位都会退化为段级。

全清单注入时，对每条 issue 独立跑 Step 1–4；Step 5 只在全部 issue 处理完后执行一次。

---

## §3 段落定位规则

`issues.json` 中的 `locator.paragraph_index` 指向 **`word/document.xml` 的 `<w:body>` 直属 `<w:p>` 元素按文档顺序的 0-based 索引**，含空段。

### §3.1 计数规则

- ✅ 计入：`<w:body>` 的直接子 `<w:p>`，无论是否有文本
- ❌ 不计入：`<w:tbl>` 内的 `<w:p>`（表格走 §9 的替代定位）、`<w:sectPr>` / `<w:sdt>` 内的段落
- ❌ 天然不存在：页眉（`header*.xml`）、页脚（`footer*.xml`）、脚注（`footnotes.xml`）、尾注（`endnotes.xml`）、批注自身（`comments.xml`）中的段落 —— 它们属于独立 XML part，不在 `document.xml` 的顺序里

### §3.2 伪代码

```python
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def locate_paragraph(document_root, paragraph_index):
    """按 body 直属 <w:p> 的绝对顺序定位第 N 个段落。"""
    body = document_root.find(f"{W_NS}body")
    paragraphs = [p for p in body if p.tag == f"{W_NS}p"]
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return None  # 触发 §11 定位失败降级
    return paragraphs[paragraph_index]
```

注意 `body` 下的 `<w:sectPr>` 与 `<w:tbl>` 会被上面的过滤器自然跳过。

### §3.3 表格内段落的替代定位

当 issue 的段落位于表格单元格，`locator` 必须同时提供 `table_index`、`row`、`col`、`paragraph_index_in_cell` 四字段联合定位（见 §9）。`paragraph_index` 此时应填 `null` 或省略；agent 生成 issues.json 时 **不能** 用 body 级顺序去蒙表格内段落。

---

## §4 run 展平与 offset 定位

### §4.1 为什么需要展平

`<w:r>`（run）是"共享格式的连续字符块"。同一段落常被切成几十个 run——每次换字体、换颜色、换粗体都会断一次 run。字符在段内的绝对位置与 run 的边界**没有任何关系**。

要根据 `char_offset_in_paragraph` 精确定位，必须把段内所有 run 展平为"字符 → 所属 run + 所属 `<w:t>` + run 的 rPr 快照"的序列。

### §4.2 特殊子元素的处理

`<w:r>` 的子元素不止 `<w:t>`。展平规则：

| 子元素 | 展平时当作 |
|---|---|
| `<w:t>` | 逐字符展开 `(char, run, t_elem, idx_in_t)` |
| `<w:br/>` | 1 个 `\n`，`t_elem=None` |
| `<w:tab/>` | 1 个 `\t`，`t_elem=None` |
| `<w:drawing>` / `<w:pict>` / `<w:fldChar>` / `<w:instrText>` | 跳过（0 字符） |

一个 `<w:r>` 可含多个 `<w:t>`，展平时要保留各 `<w:t>` 的独立引用，因为 §5 的拆分要精确切到命中的那一个 `<w:t>`。

### §4.3 伪代码

```python
import copy

def flatten_runs(paragraph):
    """
    返回字符级序列 [(char, run_element, t_element, char_index_in_t, rPr_snapshot)]。
    char_index_in_t=-1 表示该字符来自 <w:br>/<w:tab> 等非 <w:t> 子元素。
    rPr_snapshot 是 deepcopy 的 <w:rPr>，供 §5 拆分新 run 使用。
    """
    seq = []
    for r in paragraph.findall(f"{W_NS}r"):
        rPr = r.find(f"{W_NS}rPr")
        rPr_copy = copy.deepcopy(rPr) if rPr is not None else None
        for child in r:
            if child.tag == f"{W_NS}t":
                for i, ch in enumerate(child.text or ""):
                    seq.append((ch, r, child, i, rPr_copy))
            elif child.tag == f"{W_NS}br":
                seq.append(("\n", r, None, -1, rPr_copy))
            elif child.tag == f"{W_NS}tab":
                seq.append(("\t", r, None, -1, rPr_copy))
            # 其他子元素（drawing/fldChar/sym 等）按 §4.2 表格处理
    return seq
```

`char_offset_in_paragraph = k` 即"命中 `seq[k]`"。`len(seq)` 即为段长度；越界的 offset 触发 §11 降级。

---

## §5 run 拆分

### §5.1 目的与格式保留

为让 `<w:commentRangeStart>` / `<w:commentRangeEnd>` 能精确落在"第 k 个字符的左侧"，要把命中字符所在的 `<w:r>` 在该位置切成两个新 run。切完后，commentRange 标记就可作为这两个 run 的"兄弟节点"直接插入。

**rPr 必须深拷贝**：后半 run 要继承原 run 的全部格式（字体、字号、颜色、粗体、上下标、语言等）。浅拷贝不够——`<w:rFonts>` / `<w:color>` / `<w:sz>` 都是子元素。必须用 `copy.deepcopy()`。

### §5.2 伪代码

```python
import xml.etree.ElementTree as ET

def split_run_at(paragraph, char_offset):
    """
    在段内 char_offset 位置拆分 <w:r>。返回 (前半 run, 后半 run)。
    边界: offset=0 → (None, first_r); offset=段长 → (last_r, None);
          命中 run 边界 → (prev_r, cur_r) 不新建; 命中 run 中部 → 拆分。
    """
    seq = flatten_runs(paragraph)
    if char_offset == 0:
        return (None, seq[0][1] if seq else None)
    if char_offset >= len(seq):
        return (seq[-1][1] if seq else None, None)

    _, run, t_elem, idx_in_t, rPr = seq[char_offset]
    if seq[char_offset - 1][1] is not run:
        return (seq[char_offset - 1][1], run)  # run 边界, 不需切

    # 拆分 <w:t>: 前半留原位, 后半入新 run
    left_text = (t_elem.text or "")[:idx_in_t]
    right_text = (t_elem.text or "")[idx_in_t:]
    t_elem.text = left_text

    new_r = ET.Element(f"{W_NS}r")
    if rPr is not None:
        new_r.append(copy.deepcopy(rPr))  # 深拷贝保持原格式
    new_t = ET.SubElement(new_r, f"{W_NS}t")
    new_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    new_t.text = right_text

    idx = list(paragraph).index(run)
    paragraph.insert(idx + 1, new_r)
    # 若原 <w:r> 在命中 <w:t> 之后还有其他子元素（第二个 <w:t>、<w:br>），
    # 需挪到 new_r 里保证文本连续性; 实现细节见 tools/inject-docx-comments.py
    return (run, new_r)
```

### §5.3 `xml:space="preserve"` 的强制要求

新建 `<w:t>` 如果首尾有空格或 tab，必须带 `xml:space="preserve"`，否则 XML parser 会吞掉空白。安全做法：**新建的所有 `<w:t>` 一律加该属性**。

---

## §6 commentRange 三元标记插入

### §6.1 三元标记含义

每条批注需要在 `document.xml` 的目标段落内插入**三个**元素，共享同一个 `w:id`：

| 元素 | 位置 | 作用 |
|---|---|---|
| `<w:commentRangeStart w:id="N"/>` | 批注范围起点的左侧 | 标记"从这里开始是批注 N 的范围" |
| `<w:commentRangeEnd w:id="N"/>` | 批注范围终点的右侧 | 标记"批注 N 的范围到这里结束" |
| `<w:r><w:commentReference w:id="N"/></w:r>` | 紧随 `commentRangeEnd` | 气泡锚点，Word 在这里渲染批注气泡 |

**关键约束**：`<w:commentReference>` 必须被包在 `<w:r>` 里，不能作为 `<w:p>` 的直接子元素，否则 Word 打开报"文件损坏"。

### §6.2 "先拆末端再拆起始"原则

插入顺序必须严格遵守：**先 split 末端 offset，再 split 起始 offset**。

原因：先拆起始会让段内多出一个 run（新生的后半 run），再按末端 offset 展平计数时，所有 ≥ 起始 offset 的位置会偏移 +1，导致末端落错。同理，**同段多批注时也必须按 offset 降序处理**（见 §10）。

### §6.3 伪代码

```python
def insert_comment_markers(paragraph, start_offset, end_offset, comment_id):
    """在段内 [start_offset, end_offset) 区间挂载批注 id=comment_id。"""
    assert start_offset < end_offset, "空区间不合法"

    # ① 先拆末端 —— 此时 start_offset 仍然准确
    _, after_end = split_run_at(paragraph, end_offset)
    # ② 再拆起始 —— paragraph 虽多了 end 处的新 run, 但 start < end 不受影响
    before_start, _ = split_run_at(paragraph, start_offset)

    # ③ 插入 commentRangeStart: before_start 的下一个兄弟位置
    crs = ET.Element(f"{W_NS}commentRangeStart")
    crs.set(f"{W_NS}id", str(comment_id))
    idx_start = list(paragraph).index(before_start) + 1 if before_start is not None else 0
    paragraph.insert(idx_start, crs)

    # ④ 插入 commentRangeEnd: after_end 的前一个兄弟位置
    cre = ET.Element(f"{W_NS}commentRangeEnd")
    cre.set(f"{W_NS}id", str(comment_id))
    idx_end = list(paragraph).index(after_end) if after_end is not None else len(list(paragraph))
    paragraph.insert(idx_end, cre)

    # ⑤ 紧随 commentRangeEnd 插入包裹在 <w:r> 里的 commentReference
    ref_r = ET.Element(f"{W_NS}r")
    ref_rPr = ET.SubElement(ref_r, f"{W_NS}rPr")
    ET.SubElement(ref_rPr, f"{W_NS}rStyle").set(f"{W_NS}val", "CommentReference")
    ref = ET.SubElement(ref_r, f"{W_NS}commentReference")
    ref.set(f"{W_NS}id", str(comment_id))
    paragraph.insert(idx_end + 1, ref_r)
```

`<w:rStyle w:val="CommentReference"/>` 是 Word 的内建样式，用于让批注锚点在正文里不可见（否则会显示为一个小字上标标记）。如果 styles.xml 里没有这个样式，Word 会用默认渲染，不影响批注本身功能。

---

## §7 三个 OOXML 部件最小骨架

Word 2016+ 对批注部件的要求分三层：

| 部件 | 最低 Word 版本 | 缺失后果 |
|---|---|---|
| `word/comments.xml` | 所有版本 | 批注完全不显示 |
| `word/commentsExtended.xml` | Word 2013+ | 打开时提示"此文档的批注已更新" |
| `word/commentsIds.xml` | Word 2016+ | 持久化 ID 丢失，多人协作时批注身份漂移 |

本 skill 全部写入，确保副本在 Word 2016+ 与 WPS 最新版中打开零提示。

### §7.1 `word/comments.xml` 最小骨架

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
            xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
            mc:Ignorable="w14">
  <w:comment w:id="0"
             w:author="unified-thesis-reviewer"
             w:initials="UTR"
             w:date="2026-05-12T14:30:00Z"
             w14:paraId="2A3B4C5D">
    <w:p>
      <w:r><w:t xml:space="preserve">【论证深度】【major】论证跳跃：前一段断言"X 导致 Y"但未给出依据。 → 建议补充至少一条权威文献或一条可证伪的经验证据。</w:t></w:r>
      <w:r><w:br/></w:r>
      <w:r><w:t>[id: thesis-argumentation-007]</w:t></w:r>
    </w:p>
  </w:comment>
</w:comments>
```

字段约束：`w:id` 从 0 起全清单唯一递增；`w:author="unified-thesis-reviewer"` / `w:initials="UTR"` 固定值；`w:date` 为 ISO 8601（UTC `Z` 或 `+08:00` 均可）；`w14:paraId` 是 8 位大写十六进制，同一批注中要与 `commentsExtended.xml` / `commentsIds.xml` 里的 `paraId` 一致；**正文换行必须用 `<w:r><w:br/></w:r>`，不得在 `<w:t>` 里塞 `\n`**。

### §7.2 `word/commentsExtended.xml` 最小骨架

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"
                xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w15:commentEx w15:paraId="2A3B4C5D" w15:done="0"/>
</w15:commentsEx>
```

- `w15:paraId`：与 `comments.xml` 的 `w14:paraId` 对应，**每条批注一条 `<w15:commentEx>`**
- `w15:done="0"`：批注未解决；生成时一律写 `0`

### §7.3 `word/commentsIds.xml` 最小骨架

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w16cid:commentsIds xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid"
                    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w16cid:commentId w16cid:paraId="2A3B4C5D" w16cid:durableId="7B3D8E1F"/>
</w16cid:commentsIds>
```

- `w16cid:paraId`：对应 `comments.xml` 的 `w14:paraId`
- `w16cid:durableId`：8 位大写十六进制的"持久化 ID"，Word 用它在多人批注合并时识别同一条批注

### §7.4 paraId / durableId 的稳定生成策略

用确定性哈希保证同一 issue 每次运行生成相同的 paraId / durableId：

```python
import hashlib

def stable_para_id(issue_id: str) -> str:
    return hashlib.md5(("para:" + issue_id).encode()).hexdigest()[:8].upper()

def stable_durable_id(issue_id: str) -> str:
    return hashlib.md5(("dur:" + issue_id).encode()).hexdigest()[:8].upper()
```

---

## §8 Content_Types.xml 与 rels 注入

三个批注部件写入 zip 不够，还必须**向 `[Content_Types].xml` 和 `word/_rels/document.xml.rels` 注入声明**，否则 Word 会报文件损坏。

**`[Content_Types].xml` 追加三个 Override**：

```xml
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <!-- 原有 Default / Override 条目原样保留 -->
  <Override PartName="/word/comments.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
  <Override PartName="/word/commentsExtended.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"/>
  <Override PartName="/word/commentsIds.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.commentsIds+xml"/>
</Types>
```

**`word/_rels/document.xml.rels` 追加三个 Relationship**：

```xml
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <!-- 原有 Relationship 条目原样保留 -->
  <Relationship Id="rId100"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
                Target="comments.xml"/>
  <Relationship Id="rId101"
                Type="http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
                Target="commentsExtended.xml"/>
  <Relationship Id="rId102"
                Type="http://schemas.microsoft.com/office/2016/09/relationships/commentsIds"
                Target="commentsIds.xml"/>
</Relationships>
```

**rId 分配策略**：硬编码 `rId100-rId102` **不安全**——原 docx 可能已用到 `rId150`。正确做法是扫描已有 rId，从 max+1 开始分配：

```python
def allocate_rids(rels_root, count=3):
    used = []
    for rel in rels_root:
        rid = rel.get("Id", "")
        if rid.startswith("rId"):
            try: used.append(int(rid[3:]))
            except ValueError: pass
    start = (max(used) if used else 0) + 1
    return [f"rId{start + i}" for i in range(count)]
```

---

## §9 表格单元格定位

**四字段联合约束**：当 issue 的段落位于表格内，`locator` 必须同时给出四字段，且联合约束：

| 字段 | 类型 | 含义 | 范围 |
|---|---|---|---|
| `table_index` | int, 0-based | body 下第几个 `<w:tbl>`（仅 body 直属） | 0 ≤ n < 表格数 |
| `row` | int, 0-based | 表格内第几个 `<w:tr>` | 0 ≤ n < 行数 |
| `col` | int, 0-based | 行内第几个 `<w:tc>` | 0 ≤ n < 列数 |
| `paragraph_index_in_cell` | int, 0-based | 单元格内第几个 `<w:p>` | 0 ≤ n < 段数 |

四字段**必须全部提供**，任一缺失则 locator 非法。

```python
def locate_paragraph_in_cell(document_root, table_index, row, col, p_idx_in_cell):
    body = document_root.find(f"{W_NS}body")
    tables = [t for t in body if t.tag == f"{W_NS}tbl"]
    if table_index >= len(tables): return None
    tbl = tables[table_index]
    rows = [r for r in tbl if r.tag == f"{W_NS}tr"]
    if row >= len(rows): return None
    tr = rows[row]
    cells = [c for c in tr if c.tag == f"{W_NS}tc"]
    if col >= len(cells): return None
    tc = cells[col]
    ps = [p for p in tc if p.tag == f"{W_NS}p"]
    if p_idx_in_cell >= len(ps): return None
    return ps[p_idx_in_cell]
```

**嵌套表格**（表格内的表格）的 `locator` 用四字段表达不了。规则：**嵌套表格的 issue 统一走 §11 定位失败降级**，挂到嵌套表所属的外层章节首段，并在批注正文前缀"⚠️ 嵌套表格定位失败"。

---

## §10 同段多批注与跨段批注

**同段多批注：按 offset 降序插入**。同一段落需挂多条批注时，按 `char_offset_in_paragraph` 的起始值降序排列后逐条插入。原理与 §6.2 一致：从后往前插，前面的 offset 不受后面插入操作影响。

```python
def insert_multi_comments_in_paragraph(paragraph, issues_for_this_para):
    # issues_for_this_para: [(start_offset, end_offset, comment_id), ...]
    for start, end, cid in sorted(issues_for_this_para, key=lambda x: -x[0]):
        insert_comment_markers(paragraph, start, end, cid)
```

对人类阅读顺序的要求（R5.14 / R6.14 要求按升序呈现）只影响 `issues.json` 与 MD 报告的排序——实际插入 XML 时**必须降序**。

**嵌套批注允许**：两条批注的区间有包含或部分重叠在 OOXML 层面完全合法，Word 与 WPS 都能正确渲染。示例：批注 7 挂在 [10,50)，批注 8 挂在 [20,30)——XML 上四组标记交错，Word 识别为两条独立批注。

**跨段批注（scope=span）**：

- **连续段落**（中间无跳过）：`<w:commentRangeStart>` 置于起始段首、`<w:commentRangeEnd>` 置于终止段末；中间段落不加任何标记
- **非连续段落**（中间有被跳过的段落）：**不支持**真正的跨段挂载；回退为"只挂起始段，按单段模式处理"，并在批注正文前缀"⚠️ 原计划跨 N 段，已降级为单段..."

判定连续：比较所有段落的 `paragraph_index`，看是否形成 `[n, n+1, n+2, ...]` 的连续序列。

---

## §11 定位失败降级

**触发条件**（任一即算失败，**绝不允许丢弃 issue**）：

1. `paragraph_index` 越界（文档只有 100 段，issue 指到 150）
2. 表格定位四字段越界（`row` 超出行数等）
3. `char_offset_in_paragraph` 越界（段内只有 50 字，offset 指到 80）
4. 嵌套表格（§9.3）
5. 跨段不连续（§10）

**降级策略**（按优先级）：

1. **章节首段**：从 issue 的 `locator.chapter`（若 schema 存在）或文档结构识别对应章节，挂到该章节首段
2. **文档首段**：章节结构也无法识别时，挂到 `document.xml` 的第一段（`paragraph_index=0`）
3. **永远不丢**：两级降级都无法成功时，仍把问题写入 `comments.xml`，挂到文档首段，批注正文顶部加 `⚠️ 精确定位失败...`

降级批注正文前缀格式：

```
⚠️ 精确定位失败, 挂到本章开头: <原批注正文>
⚠️ 精确定位失败, 挂到文档开头: <原批注正文>
⚠️ 嵌套表格定位失败, 挂到外层章节开头: <原批注正文>
⚠️ 原计划跨 N 段, 已降级为单段: <原批注正文>
```

降级后的 issue 仍然占用 500 条上限中的一个名额。

---

## §12 500 条上限与三键稳定排序

**500 条上限**：Word 的批注面板在 500 条以上会明显卡顿（尤其 WPS），用户已不是"逐条处理"而是"淹没在批注海里"。R6.18-19 规定：超过 500 条时按规则选前 500 条导出为 Word 批注，第 501 条及以后**仍然出现在 MD 报告中**，并在 MD 报告里标注"已在 MD 报告呈现, 未导出为 Word 批注"。

**三键稳定排序**（从主键到次键）：

| 顺位 | 字段 | 方向 | 理由 |
|---|---|---|---|
| 1 | `severity` | 降序（`fatal` > `major` > `minor`） | 重要问题优先 |
| 2 | `locator.paragraph_index` | 升序 | 按文档阅读顺序 |
| 3 | `id` | 字典序升序 | 打破前两键相等时的僵局, 保证稳定性 |

三键组合**确定性地**决定先后——同一 issues.json 每次排序结果必须完全一致。

```python
SEVERITY_RANK = {"fatal": 0, "major": 1, "minor": 2}

def sort_and_cap(issues, cap=500):
    def sort_key(i):
        return (
            SEVERITY_RANK.get(i["severity"], 99),
            i.get("locator", {}).get("paragraph_index") or 10**9,
            i["id"],
        )
    sorted_issues = sorted(issues, key=sort_key)
    return sorted_issues[:cap], sorted_issues[cap:]  # (kept, dropped)
```

`paragraph_index` 为 null（表格内 issue）时用大常量压到末尾，避免抛异常。

---

## §13 白名单 part 保留

用 `zipfile` 复制原 docx 时，**绝不能只复制"自己认识的 part"**。原 docx 可能含自定义 XML、主题、样式、嵌入字体、图片等——漏任何一个，副本都会损坏或丢内容。

至少以下 part 按原样复制到副本 zip：

```
word/styles.xml            word/settings.xml        word/numbering.xml
word/fontTable.xml         word/webSettings.xml     word/theme/*
word/header*.xml           word/footer*.xml
word/footnotes.xml         word/endnotes.xml
word/media/*               word/embeddings/*        word/charts/*
word/diagrams/*            word/_rels/*             customXml/*
docProps/app.xml           docProps/core.xml        docProps/custom.xml
```

**推荐实现**：用"排除列表"而非"包含列表"——默认把原 zip 所有 part 原样写入副本，只对以下几个 part 做特殊处理：

- `word/document.xml` → 插入三元标记后写入
- `[Content_Types].xml` → 追加三个 Override 后写入
- `word/_rels/document.xml.rels` → 追加三个 Relationship 后写入
- `word/comments.xml` / `word/commentsExtended.xml` / `word/commentsIds.xml` → **新建**写入

```python
import zipfile

PARTS_TO_REWRITE = {
    "word/document.xml", "[Content_Types].xml", "word/_rels/document.xml.rels",
    "word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml",
}

def write_annotated_docx(src_path, dst_path, new_parts):
    """new_parts: dict of {part_name: bytes} for parts to rewrite/create."""
    with zipfile.ZipFile(src_path, "r") as src, \
         zipfile.ZipFile(dst_path, "w", zipfile.ZIP_DEFLATED) as dst:
        existing = set(src.namelist())
        for name in src.namelist():
            if name in PARTS_TO_REWRITE:
                dst.writestr(name, new_parts[name])
            else:
                dst.writestr(name, src.read(name))  # 原样复制
        for name in PARTS_TO_REWRITE:  # 原 zip 不存在的新 part
            if name not in existing:
                dst.writestr(name, new_parts[name])
```

---

## §14 回读三不变式

副本生成后**必须**重新打开校验。三个不变式全部通过才算成功，任一失败则删除副本并回报失败 issue id 清单（R6.22-23）。

| # | 不变式 | 检查方法 |
|---|---|---|
| 1 | **XML 可解析** | 所有 6 个核心 part（`[Content_Types].xml` / `word/document.xml` / `word/comments.xml` / `word/commentsExtended.xml` / `word/commentsIds.xml` / `word/_rels/document.xml.rels`）均 `ET.parse()` 不报错 |
| 2 | **Start/End 成对** | `<w:commentRangeStart>` 数量 == `<w:commentRangeEnd>` 数量 |
| 3 | **comment / reference 一致** | `<w:comment>` 数量 == `<w:commentReference>` 数量 |

```python
def verify_invariants(dst_path):
    """返回 (ok: bool, errors: list[str])."""
    errors = []
    required = [
        "[Content_Types].xml", "word/document.xml",
        "word/comments.xml", "word/commentsExtended.xml",
        "word/commentsIds.xml", "word/_rels/document.xml.rels",
    ]
    with zipfile.ZipFile(dst_path, "r") as z:
        for p in required:
            try:
                ET.fromstring(z.read(p))
            except ET.ParseError as e:
                errors.append(f"{p} 解析失败: {e}")
        doc = ET.fromstring(z.read("word/document.xml"))
        comments = ET.fromstring(z.read("word/comments.xml"))
        n_start = len(doc.findall(f".//{W_NS}commentRangeStart"))
        n_end = len(doc.findall(f".//{W_NS}commentRangeEnd"))
        if n_start != n_end:
            errors.append(f"commentRangeStart={n_start} 但 commentRangeEnd={n_end}")
        n_comment = len(comments.findall(f".//{W_NS}comment"))
        n_ref = len(doc.findall(f".//{W_NS}commentReference"))
        if n_comment != n_ref:
            errors.append(f"w:comment={n_comment} 但 w:commentReference={n_ref}")
    return (len(errors) == 0, errors)
```

**失败处置**：任一不变式失败，**必须删除损坏副本**后向 Unified_Reviewer 回报失败 issue id 清单：

```python
ok, errors = verify_invariants(dst_path)
if not ok:
    os.remove(dst_path)
    raise AnnotationError(f"回读校验失败, 已删除副本:\n" + "\n".join(errors))
```

目的：**绝不让用户拿到一个"看起来像 docx 但打开会报错"的文件**。宁可让用户看到"生成失败"的明确错误，也不让他在 Word 里遇到"文件已损坏"的黄色警告条。

---

## §15 对脚本的契约

本规则文件对 `tools/inject-docx-comments.py`（T2.2-T2.11 阶段实现）提出以下硬性契约：

| 契约 | 落实位置 |
|---|---|
| 只依赖 stdlib（`zipfile` / `xml.etree.ElementTree` / `copy` / `re` / `hashlib` / `os` / `sys` / `json` / `argparse`） | `import` 段 |
| CLI: `python3 inject-docx-comments.py <原.docx> <issues.json> <输出.annotated.docx>` | `argparse` |
| 只用批注，不产生 `<w:ins>` / `<w:del>` | §1 |
| 段落定位按 §3.2 | `locate_paragraph` |
| run 展平按 §4.3 | `flatten_runs` |
| run 拆分深拷贝 rPr | `split_run_at` |
| 三元标记"先拆末端后拆起始"、`<w:commentReference>` 包在 `<w:r>` 里 | `insert_comment_markers` |
| 三部件 XML 骨架严格按 §7 | `build_comments_xml` / `build_comments_extended_xml` / `build_comments_ids_xml` |
| Content_Types / rels 注入 rId 按 max+1 分配 | `allocate_rids` |
| 表格定位按 §9.2 | `locate_paragraph_in_cell` |
| 同段多批注按 offset 降序插入 | §10.1 |
| 跨段连续才走真跨段，非连续降级为单段 | §10.3 |
| 定位失败降级到章节首段 / 文档首段 | §11.2 |
| 500 条上限 + 三键稳定排序 | `sort_and_cap` |
| 白名单 part 全量保留 | `write_annotated_docx` |
| 回读三不变式通过才保留副本 | `verify_invariants` + `os.remove` |

脚本应在关键步骤有明确日志（stderr），便于故障排查。

与本文件协同的其他规则文件：

- `rules/issues-schema.md`：定义 issues.json 的字段契约，本文件所有 `locator.*` 字段都在其中定义
- `rules/xfdf-annotation.md`（T3 阶段编写）：pdf 旁路的 XFDF 2.0 方案，与本文件的 docx 路径并行，共同覆盖 v2 / v3 的所有输入格式
- `rules/error-handling.md`：定义注入失败时如何回报到 Unified_Reviewer 主流程
