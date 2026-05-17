# 表格审查规则（v2.6 新增）

## 1. 背景：藏民终17号事件复盘

v2.5 审查刘梓璇论文时，Agent 对一个表格做了**双重错误的诊断**：

### 表 3 真实情况

- **表标题**：写"表3（2024）豫民终86号案和（2025）藏民终17号案二审法院的不同意见"
- **表内"案号"列**：实际比较的是"（2024）豫民终86号"vs"（2023）鲁民终356号"

### Agent v2.5 的双重错误

错误 1（**症状识别错**）：Agent 看到表标题里的"藏民终17号"，又因为论文别处出现过"桂民终17号"，就**脑补**了"省份代字笔误（藏→桂）"这个解释。

错误 2（**证据指向编造**）：批注里写"脚注 46/47 均为'(2025)桂民终17号'"——但那页根本没有脚注 46/47，是 Agent 从论文别处看到过相关案号就**凭印象瞎填**了证据。

### 真相

表标题和表内容**整段错配**——可能作者改动过表内容后忘了改标题，或拷贝其他段时未更新。

### 根因

v2 / v2.5 的审查流程**没有强制要求审查表格时先读表格内容**。Agent 只看到表标题就开始诊断，犯了"凭文字推理代替核对原文"的错。

## 2. v2.6 强制规则

### 规则 1：审查表格时必须先读表格内容再评价表标题

当 Agent 准备对表标题、表说明、表脚注做任何 issue 时，**必须先用以下方法读取表格内容**：

```python
import zipfile, xml.etree.ElementTree as ET
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

with zipfile.ZipFile(docx_path) as z:
    with z.open('word/document.xml') as f:
        tree = ET.parse(f)

body = tree.getroot().find(f'{W}body')

# 列出所有表格 + 上下文
elems = list(body)
for i, e in enumerate(elems):
    if e.tag == f'{W}tbl':
        # 表前段（通常是标题）
        if i > 0:
            prev = elems[i-1]
            title = ''.join(t.text or '' for t in prev.iter(f'{W}t'))
            print(f"\n=== 表 #{i} 标题段 ===\n  {title}")
        # 表内行
        print(f"=== 表 #{i} 内容 ===")
        for row_i, tr in enumerate([c for c in e if c.tag == f'{W}tr']):
            cells = []
            for tc in [c for c in tr if c.tag == f'{W}tc']:
                ctext = ''.join(t.text or '' for t in tc.iter(f'{W}t'))
                cells.append(ctext[:60])
            print(f"  行{row_i}: {' | '.join(cells)}")
```

读完之后，对照检查：

- 表标题描述的对象 = 表内容实际呈现的对象吗？
- 表标题的关键字（案号、年份、地区、变量名）= 表内对应字段吗？
- 表说明、表脚注引用的内容 = 表中实际数据吗？

### 规则 2：提及脚注号时必须先 verify 脚注实际内容

当 Agent 在 issue 的 `problem` 字段提及"脚注 N""第 N 页""表 N"等具体定位时，**必须先读取该脚注/页/表的实际内容**：

```python
# 读脚注
import zipfile, xml.etree.ElementTree as ET
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

with zipfile.ZipFile(docx_path) as z:
    if 'word/footnotes.xml' in z.namelist():
        with z.open('word/footnotes.xml') as f:
            tree = ET.parse(f)
        for fn in tree.getroot().findall(f'{W}footnote'):
            fn_id = fn.attrib.get(f'{W}id')
            text = ''.join(t.text or '' for t in fn.iter(f'{W}t'))
            print(f"  脚注 {fn_id}: {text[:80]}")
```

**禁止凭印象写"脚注 46 引用了 X"——必须先确认脚注 46 真的是 X 才能这么写**。

## 3. 表格审查的 7 项核查点

每个表都按以下清单核查：

| # | 核查点 | 例子 |
|---|---|---|
| 1 | 表标题与表内容的对象一致 | 标题"案 A vs 案 B" → 表内数据真的是 A 和 B |
| 2 | 表内列名清晰、可理解 | "得分"应改为"得分（满分 100）" |
| 3 | 数字相加是否等于合计行 | 子项相加 ≠ 总计是常见的实证型论文 bug |
| 4 | 百分比的分母是否明确 | "上诉率 8.65%" → 分母是什么 N |
| 5 | 时间范围是否与正文一致 | 表内"2023-2025" → 摘要里"2023-2024"？ |
| 6 | 表脚注引用真实存在 | 表注"数据来源：XXX 报告" → 该报告真存在吗？ |
| 7 | 表说明的术语与正文一致 | "上诉率"/"上诉比"是否前后用同一术语 |

## 4. 何时是真问题，何时是假阳性

**真问题示例**（应该写 issue）：

- 表标题"豫86 vs 藏17"，表内"豫86 vs 鲁356" → 标题与内容错配，应同步更新
- 表 1 上诉率 49/424=11.56%，但论文写 8.65% → 算式不可复现，分母口径未明
- 表脚注 4 引用"《XX 法学》2023 年第 5 期"，该期刊该期不存在 → 引注伪造

**假阳性示例**（不应写 issue）：

- 表标题用"原告"，表内用"投资者" → 二者在该论文语境下同义，不算错配
- 数字相差 0.01-0.02 → 四舍五入误差，正常

## 5. 与 `academic-integrity-guard.md` 的配合

本规则与 v2 已有的"假阳性守门"规则配合：

- 提到 `[原文核对]` 标签的 issue，必须真做了原文核对（包括表格、脚注、参考文献）
- 提到 `[联网核实]` 标签的 issue，必须真做了联网搜索（且引述搜索结果时不能编造）
- 二者结合：**`[原文核对]` 表格类 issue 必须有具体表内容引述作为证据**

## 6. v2.7 候选改造

把表格审查流程工具化：

1. 新建 `tools/extract-tables.py`：扫描 docx，输出 `{文件}.tables.json` 含每个表的标题、列名、行数据、表注
2. Agent 写涉及表格的 issue 时，强制引用该 JSON 中的实际数据作为 `excerpt`
3. 新建 `tools/audit-issues.py`：发布前自审脚本，扫描 issues.json 中"脚注 N""表 N"的引用，反查实际内容是否一致
