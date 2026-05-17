# issues.json 数据契约与自校验（v2.0.0）

本规则是 `unified-thesis-reviewer` 全链路的**数据总线契约**。v2 起 schema_version 依然为 `"1.0"`（向后兼容），但强烈推荐 Agent 使用 v2 的新字段 `anchor_text` 以获得精确的 pdf 批注定位。

本文件定义：
1. 顶层结构与 issue 对象字段（§1–§3）
2. group_id 分组算法（§4）
3. 完整 JSON Schema（draft-07）（§5）
4. Python 自校验函数伪代码（§6）
5. 常见违规与修复表（§7）

## v2.0.0 关键变更

- 每条 issue 新增**推荐字段** `anchor_text`（string, ≤ 60 码点），供 pdf 批注定位使用
- `scope=document/chapter` 时依然要求 `excerpt==""`，但**强烈建议**此时 `anchor_text` 非空（如章节标题原文），这样批注可以高亮到章节标题而非页面左上角便签
- `bbox` 字段从"推荐"降级为"可选（不建议手工填写）"——Agent 侧依赖 PyMuPDF 的 `search_for(anchor_text)` 自动定位比手填 bbox 更准
- 校验失败的降级路径保持不变

---

## § 1 顶层对象

顶层 JSON 对象含两个字段：

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| `schema_version` | string | `"1.0"` | v1/v2 共用 |
| `issues` | array | 0+ issue 对象 | 空清单写 `[]` |

最小合法骨架：

```json
{
  "schema_version": "1.0",
  "issues": []
}
```

---

## § 2 issue 对象字段

**必填字段（11 个）**：`id` / `source` / `category` / `severity` / `scope` / `locator` / `excerpt` / `problem` / `suggestion` / `group_id` / `anchor_text`（v2 新增）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `id` | string | ✅ | 正则 `^(thesis\|citation)-(category_enum)-\d{3}$`；全清单唯一 |
| `source` | enum | ✅ | `{thesis, citation}` |
| `category` | enum | ✅ | 10 值封闭集（见下） |
| `severity` | enum | ✅ | `{fatal, major, minor}` — 严重度判定标尺详见 § 2.0a |
| `scope` | enum | ✅ | `{document, chapter, paragraph, sentence, span}` |
| `locator` | object | ✅ | 见 §3 |
| `excerpt` | string | ✅ | ≤ 60 码点；scope ∈ {document, chapter} 时必为 `""` |
| `problem` | string | ✅ | 1-200 码点非空 |
| `suggestion` | array[string] | ✅ | 长度 1-5，每条 1-500 码点 |
| `group_id` | string | ✅ | 正则 `^g-\d{3,}$` |
| **`anchor_text`** | string | ✅ | ≤ 60 码点；pdf 批注定位的首选锚点，可为空字符串但强烈建议非空 |

### § 2.0a 严重度判定标尺（v2.7 新增）

v2.6 实战暴露的核心失误：Agent 把"看上去不规范的现象"直接升到 `fatal`，导致批注像指控。本节确立判定标尺。

#### fatal（致命）

**实质性影响论文学术诚信或核心结构完整性**。典型情形：

- **学术不端线索**：伪造引用（声称引用某文献但 CNKI / Google Scholar 无法核实存在）、张冠李戴（把张三的观点归为李四）、把经济学家塑造为法学家等身份错配
- **核心框架残缺**：论文明确提出 N 项分类但仅展开 N-1 项；摘要承诺"案例分析与实地调研"但全文无任何调研痕迹
- **核心数据错误**：政府公开数据被错引（如 33929.6 亿写成 33654.6 亿）
- **核心命题与论文自有数据自相矛盾**：摘要主张"一刀切"但样本数据显示 20% 案件不符
- **比较法严重误读**：把美国 DMCA 红旗原则嫁接到德国 §44b TDM 例外
- **结构性遗漏**：研究目的承诺 N 类典型案例但实际跳过其中 K 类

#### major（重要）

**明显规范性问题或重要论证缺陷，但不构成诚信指控**。典型情形：

- **论证缺反方**：政策建议未回应任何明显反驳
- **法规版本混淆 / 法规名称错误**："《生成式人工智能服务提供者服务管理办法》"（正式名是"《生成式人工智能服务管理暂行办法》"）
- **理论框架与实证脱节**：第二章建立的分类在第三章实证中"空转"
- **同主题前序研究未对话**：与本文议题高度重叠的标志性前序研究全文未引（如证券虚假陈述实证研究，鲍彩慧 2017 年 806 份判决书研究）
- **参考文献信息严重缺失**：缺期刊名、缺卷期号
- **英文摘要严重语法/术语问题**：关键词使用错误词性、Abstract 逻辑连词错位
- **重要案例承载过多论点**：同一案例被 4 次引用支撑 3 个不同命题
- **数据可重现性问题**：表格算式不自洽（49/424=11.56% 写成 8.65%）

#### minor（轻微）

**可改可不改的优化项**。典型情形：

- **个别笔误**：错字、漏字、的/地/得混用
- **措辞偏弱**：法规中"不得"被弱化为"不可"
- **致谢简略**：但当前致谢已达基本规范
- **单条引注格式瑕疵**：缺一个空格、标点不规范
- **章节标题样式混用**（OOXML 问题）：转 PDF/Markdown 时可能轻微出错
- **概念前后用字不一致**：郭锋 / 郭峰、阿里云案 / 阿里案
- **同页连续两脚注引用同一文献同一页**：本身不构成规范违反，但建议检查是否需合并

#### 反向举证原则（v2.7 强制）

**凡升级为 fatal 的判断，必须满足"反向举证测试"：能否找到合理解释推翻这个指控？**

- 如果能：**不应**定为 fatal
- 例 1：同段两脚注引同一文献同一页 → 合理解释：该段论点都源自该文献该页，正常学术写作。**不构成 fatal**（v2.7 改判为 minor）
- 例 2：参考文献某条 CNKI 搜不到 → 反向解释：可能是论文 2026 年 3 月完稿引用 2026 年 1 月刚发的新刊，CNKI 索引延迟。**不应直接定 fatal**，应标 major 并请作者补出处链接
- 例 3：作者身份不符（经济学家被塑造为法学家）→ 反向解释：找不到合理替代解释。**fatal 成立**

**规则**：写 fatal 级 issue 时，Agent 必须在 problem 字段中或内部思考中能明确回答"为什么这不能用 X 解释推翻"。

---

### § 2.1 category 封闭集

```
structure / argumentation / literature-review / empirical / legal-norms /
language / policy / academic-integrity / citation-format / citation-missing-info
```

### § 2.2 anchor_text vs excerpt 的关系（v2 重点）

两个字段都承载"原文片段"，但用途不同：

| 字段 | 用途 | scope=document/chapter 时 |
|---|---|---|
| `excerpt` | **审查报告展示用**：读者看报告时的原文直引证据 | 必须为空（避免占位文案） |
| `anchor_text` | **脚本定位用**：`tools/annotate-pdf.py` 的 PyMuPDF 搜索锚点 | 应填**章节标题原文**（如 "参考文献"、"第 5 章") |

**建议的填写策略**：

- 段/句/字级问题（scope=paragraph/sentence/span）：
  `anchor_text = excerpt`（两者一致）或 `anchor_text = excerpt 中最独特的 10-20 字`
- 章级问题（scope=chapter）：
  `excerpt = ""`（schema 强制）；`anchor_text = 章节标题原文`（如 "4.2.1 信托登记与股权登记的区分"）
- 全文性问题（scope=document）：
  `excerpt = ""`；`anchor_text = 最接近该问题的具体锚点`（如 "参考文献" 章节标题）

### § 2.3 长度上限

- `excerpt` ≤ 60 Unicode 码点
- `anchor_text` ≤ 60 Unicode 码点
- `problem` 1–200 码点
- `suggestion[i]` 1–500 码点（数组 1–5 条）

---

## § 3 locator 子对象

| 字段 | 类型 | docx 语义 | pdf 语义 | 备注 |
|---|---|---|---|---|
| `chapter` | string | 章节号或标题，如 `"3.2.1"` 或 `"参考文献"` | 同左 | **必填** |
| `paragraph_index` | int ≥ -1 | `<w:p>` 序号 | 抽取阶段切分的段序号 | 必填；表格 locator 时 = -1 |
| `sentence_index` | int ≥ 0 | 段内第 N 句 | 同左 | 可选 |
| `char_offset_in_paragraph` | int ≥ 0 | 段内字符偏移 | 同左 | 可选 |
| `page_number` | int ≥ 1 | docx 可选 | pdf 必填 | pdf 下是批注起始搜索页 |
| `bbox` | array[4] | — | `[x0,y0,x1,y1]` 左下原点 y 向上 | **v2 不推荐手填**；annotate-pdf 用 search_for 自动定位更准 |
| `table_index / row / col / paragraph_index_in_cell` | int ≥ 0 | 表格单元格联合定位 | — | 四字段联合出现 |

**关键约束**（与 v1 一致）：

- 表格四字段**要么全出现、要么全不出现**；出现时 `paragraph_index = -1`
- pdf 输入下 `page_number` 必填
- `bbox` 非必填；若存在则四元素为 number

### § 3.1 最小必要字段

| 输入形态 | locator 最小必要字段 |
|---|---|
| docx 正文段落 | chapter + paragraph_index ≥ 0 |
| docx 表格段落 | chapter + paragraph_index = -1 + 四字段 |
| pdf 任意段落 | chapter + paragraph_index ≥ 0 + **page_number** |

### § 3.2 anchor_text 可以比 locator 更重要（v2 原则）

v2 的 `tools/annotate-pdf.py` 实际使用顺序为：

1. 首选 anchor_text 在 page_number 附近搜
2. 失败则 anchor_text 全文搜
3. 失败则 excerpt 全文搜
4. 失败则用 bbox
5. 全部失败 → 章节首段左上便签

因此：**page_number 推测不准没关系，anchor_text 准即可**。这大幅降低了 Agent 对"精确页码"的要求。

---

## § 4 group_id 分组算法

**分组键**：`(source, category, locator.chapter, locator.paragraph_index)`

- 相同分组键的 issues → 共享 group_id
- 不同分组键 → 分配不同 group_id（即使结构相似）
- group_id 格式：`g-NNN`（三位零填充）

### § 4.1 分配伪代码

```python
def assign_group_ids(issues: list[dict]) -> None:
    key_to_gid: dict[tuple, str] = {}
    next_n = 1
    for it in issues:
        loc = it.get("locator", {})
        key = (
            it.get("source"),
            it.get("category"),
            loc.get("chapter"),
            loc.get("paragraph_index"),
        )
        if key not in key_to_gid:
            key_to_gid[key] = f"g-{next_n:03d}"
            next_n += 1
        it["group_id"] = key_to_gid[key]
```

**Agent 侧建议**：生成 issues.json 前，用上面伪代码统一分配 group_id，避免手填不一致触发自校验失败。

---

## § 5 完整 JSON Schema（draft-07）

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "https://unified-thesis-reviewer/schema/issues-1.0.json",
  "title": "unified-thesis-reviewer issues.json (v2)",
  "type": "object",
  "required": ["schema_version", "issues"],
  "additionalProperties": false,
  "properties": {
    "schema_version": { "type": "string", "const": "1.0" },
    "issues": {
      "type": "array",
      "items": { "$ref": "#/$defs/Issue" }
    }
  },
  "$defs": {
    "Issue": {
      "type": "object",
      "required": [
        "id", "source", "category", "severity", "scope",
        "locator", "excerpt", "problem", "suggestion", "group_id", "anchor_text"
      ],
      "additionalProperties": false,
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^(thesis|citation)-(structure|argumentation|literature-review|empirical|legal-norms|language|policy|academic-integrity|citation-format|citation-missing-info)-\\d{3}$",
          "maxLength": 80
        },
        "source": { "type": "string", "enum": ["thesis", "citation"] },
        "category": {
          "type": "string",
          "enum": [
            "structure", "argumentation", "literature-review",
            "empirical", "legal-norms", "language", "policy",
            "academic-integrity", "citation-format", "citation-missing-info"
          ]
        },
        "severity": { "type": "string", "enum": ["fatal", "major", "minor"] },
        "scope": {
          "type": "string",
          "enum": ["document", "chapter", "paragraph", "sentence", "span"]
        },
        "locator": { "$ref": "#/$defs/Locator" },
        "excerpt": { "type": "string", "maxLength": 60 },
        "anchor_text": { "type": "string", "maxLength": 60 },
        "problem": { "type": "string", "minLength": 1, "maxLength": 200 },
        "suggestion": {
          "type": "array",
          "minItems": 1,
          "maxItems": 5,
          "items": { "type": "string", "minLength": 1, "maxLength": 500 }
        },
        "group_id": {
          "type": "string",
          "pattern": "^g-\\d{3,}$",
          "maxLength": 20
        }
      }
    },
    "Locator": {
      "type": "object",
      "required": ["chapter", "paragraph_index"],
      "additionalProperties": false,
      "properties": {
        "chapter": { "type": "string", "minLength": 1, "maxLength": 60 },
        "paragraph_index": { "type": "integer", "minimum": -1 },
        "sentence_index": { "type": "integer", "minimum": 0 },
        "char_offset_in_paragraph": { "type": "integer", "minimum": 0 },
        "page_number": { "type": "integer", "minimum": 1 },
        "bbox": {
          "type": "array",
          "minItems": 4, "maxItems": 4,
          "items": { "type": "number" }
        },
        "table_index": { "type": "integer", "minimum": 0 },
        "row": { "type": "integer", "minimum": 0 },
        "col": { "type": "integer", "minimum": 0 },
        "paragraph_index_in_cell": { "type": "integer", "minimum": 0 }
      }
    }
  }
}
```

---

## § 6 自校验函数伪代码

`tools/inject-docx-comments.py` 和 `tools/annotate-pdf.py`（v2 新增）应各自保留一份。关键变化：新增对 `anchor_text` 的必填校验。

```python
import re

ENUM_SOURCE = {"thesis", "citation"}
ENUM_CATEGORY = {
    "structure", "argumentation", "literature-review",
    "empirical", "legal-norms", "language", "policy",
    "academic-integrity", "citation-format", "citation-missing-info",
}
ENUM_SEVERITY = {"fatal", "major", "minor"}
ENUM_SCOPE = {"document", "chapter", "paragraph", "sentence", "span"}
ID_PATTERN = re.compile(
    r"^(thesis|citation)-"
    r"(structure|argumentation|literature-review|empirical|legal-norms|"
    r"language|policy|academic-integrity|citation-format|citation-missing-info)"
    r"-\d{3}$"
)
GROUP_ID_PATTERN = re.compile(r"^g-\d{3,}$")
CELL_KEYS = {"table_index", "row", "col", "paragraph_index_in_cell"}

REQUIRED_FIELDS = (
    "id", "source", "category", "severity", "scope",
    "locator", "excerpt", "problem", "suggestion", "group_id",
    "anchor_text",  # v2 新增必填
)


def validate_issues_json(data, *, input_is_pdf: bool = False) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["top-level must be JSON object"]
    if data.get("schema_version") != "1.0":
        errors.append(f"schema_version must be '1.0'")
    issues = data.get("issues")
    if not isinstance(issues, list):
        return errors + ["issues must be an array"]

    seen_ids = set()
    group_key_to_gid = {}
    gid_to_group_key = {}

    for idx, it in enumerate(issues):
        ctx = f"issues[{idx}]"
        if not isinstance(it, dict):
            errors.append(f"{ctx}: must be object")
            continue

        # 必填字段
        for field in REQUIRED_FIELDS:
            if field not in it:
                errors.append(f"{ctx}: missing required field '{field}'")

        # 枚举
        if it.get("source") not in (None,) and it["source"] not in ENUM_SOURCE:
            errors.append(f"{ctx}.source: invalid")
        if it.get("category") not in (None,) and it["category"] not in ENUM_CATEGORY:
            errors.append(f"{ctx}.category: invalid")
        if it.get("severity") not in (None,) and it["severity"] not in ENUM_SEVERITY:
            errors.append(f"{ctx}.severity: invalid")
        if it.get("scope") not in (None,) and it["scope"] not in ENUM_SCOPE:
            errors.append(f"{ctx}.scope: invalid")

        # id 格式 + 唯一
        if isinstance(it.get("id"), str):
            if not ID_PATTERN.match(it["id"]):
                errors.append(f"{ctx}.id: pattern mismatch")
            if it["id"] in seen_ids:
                errors.append(f"{ctx}.id: duplicate")
            seen_ids.add(it["id"])

        # scope-excerpt 依赖
        if it.get("scope") in ("document", "chapter"):
            if it.get("excerpt", "") != "":
                errors.append(f"{ctx}.excerpt: scope={it['scope']} requires empty")

        # 长度上限
        for fld, maxlen in (("excerpt", 60), ("anchor_text", 60), ("problem", 200)):
            val = it.get(fld)
            if isinstance(val, str) and len(val) > maxlen:
                errors.append(f"{ctx}.{fld}: length > {maxlen}")

        # suggestion
        sug = it.get("suggestion")
        if isinstance(sug, list):
            if not (1 <= len(sug) <= 5):
                errors.append(f"{ctx}.suggestion: length not in [1,5]")
            for j, s in enumerate(sug):
                if not isinstance(s, str) or len(s) == 0 or len(s) > 500:
                    errors.append(f"{ctx}.suggestion[{j}]: invalid")

        # locator
        loc = it.get("locator")
        if not isinstance(loc, dict):
            errors.append(f"{ctx}.locator: must be object")
            continue

        chapter = loc.get("chapter")
        if not isinstance(chapter, str) or not chapter:
            errors.append(f"{ctx}.locator.chapter: must be non-empty string")
        pidx = loc.get("paragraph_index")
        if not isinstance(pidx, int) or isinstance(pidx, bool) or pidx < -1:
            errors.append(f"{ctx}.locator.paragraph_index: invalid")

        # 表格四字段
        present_cell = CELL_KEYS & set(loc.keys())
        if present_cell and present_cell != CELL_KEYS:
            errors.append(f"{ctx}.locator: table-cell keys incomplete")
        if present_cell == CELL_KEYS and loc.get("paragraph_index") != -1:
            errors.append(f"{ctx}.locator: table-cell requires paragraph_index==-1")

        # pdf 下 page_number 必填
        if input_is_pdf and "page_number" not in loc:
            errors.append(f"{ctx}.locator.page_number: required for pdf")
        if "page_number" in loc:
            pn = loc["page_number"]
            if not isinstance(pn, int) or isinstance(pn, bool) or pn < 1:
                errors.append(f"{ctx}.locator.page_number: invalid")

        # bbox
        if "bbox" in loc:
            bb = loc["bbox"]
            if not isinstance(bb, list) or len(bb) != 4:
                errors.append(f"{ctx}.locator.bbox: must be array of length 4")

        # group_id 一致性
        gid = it.get("group_id")
        if isinstance(gid, str) and GROUP_ID_PATTERN.match(gid):
            gkey = (it.get("source"), it.get("category"),
                    loc.get("chapter"), loc.get("paragraph_index"))
            if gkey in group_key_to_gid and group_key_to_gid[gkey] != gid:
                errors.append(f"{ctx}.group_id: inconsistent with key {gkey}")
            elif gid in gid_to_group_key and gid_to_group_key[gid] != gkey:
                errors.append(f"{ctx}.group_id: reused across different keys")
            else:
                group_key_to_gid[gkey] = gid
                gid_to_group_key[gid] = gkey

    return errors
```

---

## § 7 常见违规与修复表

| 违规场景 | 错误消息 | 修复 |
|---|---|---|
| 缺 `anchor_text` 字段 | `missing required field 'anchor_text'` | 补 anchor_text（章级问题填章节标题，段级问题填 excerpt 或其前 20 字）|
| `id` 写成 `thesis_structure_001` | `pattern mismatch` | 短横线分隔 |
| `scope=chapter` 但 `excerpt="整章内容"` | `scope=chapter requires empty` | excerpt 置 `""`，把文字放 anchor_text |
| 表格 locator 只写 `table_index` | `table-cell keys incomplete` | 补齐四字段 |
| 两 issue 同四元组但 group_id 不同 | `group_id: inconsistent` | 重跑 `assign_group_ids()` |
| pdf 输入下 issue 无 `page_number` | `page_number: required for pdf` | 补 page_number（Agent 侧根据提取文本的位置推断）|
| `problem` 超 200 码点 | `length > 200` | 缩到 200 内，细节挪到 suggestion |

---

## § 8 对下游工具的契约

- `tools/annotate-pdf.py`（v2 主力）消费 issues.json，按 anchor_text 优先搜索定位
- `tools/inject-docx-comments.py`（**v2.6 重大改造**，docx 输入走此路径）消费 issues.json：anchor_text 优先全文搜索定位，paragraph_index 退为兼容兜底。详见 `rules/anchor-text-locator.md`
- `tools/generate-xfdf.py`（v2 降级为备用，用户明确要 XFDF 时才调用）
- `tools/extract-pdf-text.py`（v2 新增）供 Agent 读取 pdf 精确位置信息以生成高质量 anchor_text 和 bbox

相关规则：
- `rules/orchestration-flow.md` —— v2 主工作流改用 annotate-pdf 作 pdf 批注主路径
- `rules/pdf-annotation.md` —— v2 新增，取代 v1 的 xfdf-annotation.md
- `rules/academic-integrity.md` —— 假阳性防控条款（v2 新增）
- `rules/anchor-text-locator.md`（v2.6 新增） —— docx 批注 anchor_text 主路径定位机制详解
- `rules/substantive-review.md`（v2.6 新增） —— 实质性维度审查七维度清单
- `rules/ooxml-style-check.md`（v2.6 新增） —— OOXML 样式层核查
- `rules/table-audit.md`（v2.6 新增） —— 表格审查规则

---

## § 9 术语风格指南（v2.6 新增）

写 issue 的 `problem` 与 `suggestion` 字段时，遵循以下风格：

### 禁用的高刺激词

| 禁用 | 替代表述 |
|---|---|
| "幽灵引用"/"ghost reference" | "未在脚注中调用的参考文献条目"（且 v2.6 起这不再视为问题，不应生成对应 issue） |
| "造假"/"伪造" | "建议核实是否真实存在"/"经检索未直接命中" |
| "涉嫌" | "疑需进一步核实" |
| "明显" | "可能" |
| "显然" | "或/似乎" |
| "确实" | "据 X 资料显示" |

### 鼓励的中性表述

- "本表/本段未在 CNKI 直接检索到精确匹配，请提供出处链接以证实存在"
- "[联网核实]Wouter van der Wielen 确有其人，但其身份为经济学家，专攻宏观经济，未发表过 AI 著作权论文。建议提供具体出处或删除此引用"
- "原文表标题与表内容不一致——表标题写 X，表内案号列实际为 Y。建议同步更新"
- "建议核对原始判决/原始文献后再做决定"

### 标签使用规范

每条 `problem` 字段开头应使用方括号标签明确证据来源：

- `[联网核实]` —— 已通过 web_search 或 web_fetch 验证，必须附搜索结论
- `[原文核对]` —— 已读取论文具体段/表/脚注，必须有具体引述
- `[文本分析]` —— 基于文本扫描（如笔误检测），无需外部证据
- `[OOXML分析]` —— 基于 OOXML 样式属性核查，必须附 pStyle/numPr 等具体技术细节
- `[实质性硬伤]` —— 实质性维度问题（论证缺陷、自相矛盾、研究承诺失约等），必须有具体冲突点
- `[元层评估]` —— 对论文整体写作风格的判断（如 AI 生成嫌疑），用于全文性的 issue
- `[规则依据]` —— 引注规范类问题，必须引用具体规范条款（如 GB/T 7714 - 2015 §5.2）

**严格禁止**：使用 `[原文核对]` 标签但实际没读原文、使用 `[联网核实]` 标签但实际未联网搜索——这是"自报家门式守门"的典型失败，详见 `rules/academic-integrity-guard.md`。
