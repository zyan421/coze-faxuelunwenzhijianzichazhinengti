# Unified_Report 合并规则（v2.0.0）

v2 相对 v1 的关键变化：

- **§1 章节结构新增⑩"分维度评分表"**（U7），替代原⑦"整体评价"的"总体分档"表述
- **§2 issue 入章规则新增"证据卡"要求**（U10）
- **§4 交叉引用规则新增 citation-crossref 数据源**（U8）
- **§6 issues.json 失败降级保留 v1 规则**
- **§7 匿名化占位符使用规则**（U9 新增）

---

## § 1 9+1 章节固定顺序（v2）

Unified_Report 严格按以下顺序组织：

| 序号 | 章节标题 | 数据来源 |
|---|---|---|
| ① | 论文基本信息与元数据 | 采集 + 节点 A/B/E |
| ② | ⛔ 致命问题（R1–R4 红牌） | 仅 TR（严格证据标准，见 `rules/academic-integrity-guard.md`）|
| ③ | 论文深度审查结论（九大维度） | 仅 TR |
| ④ | 引注格式校对结论 | 仅 CC |
| ⑤ | 联网核实结果（四步式） | TR + 联网 |
| ⑥ | 遗漏但应讨论的问题清单 | TR |
| ⑦ | **分维度评分表**（v2 替代 v1"整体评价"） | TR + CC 综合 |
| ⑧ | 修改优先级 Top 10 | TR + CC |
| ⑨ | 答辩质询问题（5–10 条） | TR |
| ⑩（v2 新增）| 交叉引用对比表 | tools/citation-crossref.py |

**⑦ 不再给总评分档**：避免 skill 越权下"不达标/不通过"的结论。

---

## § 2 issue 入章规则（v2 强化）

| issue 特征 | 入章 | 证据要求（v2 新） |
|---|---|---|
| `source=thesis, severity=fatal` | ② + ③ | **必须**证据链闭环（原文 + 联网或规则） |
| `source=thesis, severity ∈ {major, minor}` | ③ | 至少 1 处证据（原文或规则） |
| `source=citation` 任意 | ④ | 至少 1 处证据 |
| `source=citation, severity=fatal`（批量失范升格） | ② + ④ | 必须证据闭环 |
| 联网核实的事实性 issue | ③ + ⑤ | 必须附 URL |
| `source=thesis, category=policy, 标注"遗漏讨论"` | ⑥ + ③ | |

**同一 issue 可入多章**，完整描述只在一处，其他章节用交叉引用。

---

## § 2.1 证据卡格式（v2 新增）

每条 issue 在报告中展示为"证据卡"，必须包含：

```markdown
### N. 【{category_cn}】（{severity_cn}）— issue_id ｜ group_id: g-NNN

- **定位**：{chapter} 第 {page} 页 {paragraph} 段
- **原文**："{excerpt 原文直引 ≤ 60 字}"（如 scope=chapter/document 则：略）
- **问题**：{problem，≤ 200 字}
- **证据来源**：
  - [原文核对] 第 X 页（anchor_text = "...."）
  - [联网核实] <URL>（若有）
  - [规则依据] rules/XXX.md §Y
  - [文本分析] / [grep 推断]（未联网核实时的必要标注）
- **建议**：
  - {suggestion[0]}
  - （若多条）{suggestion[1]}
  - ...
```

**缺证据来源的 issue 不得以 fatal 入报告**（自动降级为 major）。

---

## § 3 "修改优先级 Top 10" 打分规则

```
score = severity_weight + group_density_bonus + cross_chapter_bonus + evidence_boost

severity_weight: fatal=3.0, major=2.0, minor=1.0
group_density_bonus: 同 group_id 下 issues ≥ 2 时 +0.5
cross_chapter_bonus: locator.chapter 被 ≥ 3 个不同 category 指向时 +0.5
evidence_boost (v2 新): 有 ≥ 1 个 [联网核实] 证据时 +0.3（鼓励证据链完整的指控优先修）
```

取 score 降序前 10，打平时按 `severity → locator.chapter → id` 字典序稳定排序。

---

## § 4 交叉引用规则（v2 数据源）

### § 4.1 判定"同位置"

- **同段落**：locator.chapter + paragraph_index 相同（可以不同 source/category）
- **同脚注**：两条 issue 在 problem 中引用同一脚注号
- **同作者引用**：两条 issue 的 anchor_text 相互覆盖

### § 4.2 交叉引用格式

> （详见 §X 第 N 条，group_id: g-NNN；同指向 anchor_text "…"）

### § 4.3 citation-crossref.py 数据源（U8）

§⑩ 章节必须直接呈现 `{pdf}.crossref.json` 的对比表：

- **疑似脱钩作者表**（in_text_not_in_refs）：列出作者名 + 正文上下文片段 + "建议补入参考文献"——这是**真问题**，应在 issues.json 中生成对应的 citation-missing-info 类 issue
- **匹配成功表**（matched）：给统计数据（作者数、引用次数），不单独展开

**v2.6 起取消"幽灵引用表"**：`in_refs_not_in_text`（参考文献有但正文未脚注引用的条目）不再视为问题。参考文献本质上是"阅读/参考过的文献清单"，并不要求每条都在脚注中显式调用——作者完全可能真读过、参考过，但行文中未必用到引注。仅"脚注引但参考文献漏"（in_text_not_in_refs）才是真规范问题。

**Agent 的主观判断"脱钩"必须与 crossref 机械对比结果一致**；不一致时以 crossref 为准。

---

## § 5 group_id 在 v1/v2 的呈现

- v1/v2 都**不合并**同 group_id 的 issue
- 每条 issue 独立占一个条目
- 条目末尾括注 `group_id: g-NNN`
- 交叉引用时用 group_id 简化位置描述

---

## § 6 issues.json 失败降级

`validate_issues_json` 返回非空错误时：

1. Unified_Report 照常完整交付（9+1 章节）
2. 末尾附加：

    ```markdown
    ## ⚠️ 结构化清单未生成，批注文档不可用

    **失败原因**（前 5 条）：

    - issues[N]: …
    - ...

    **可重试**：修复后运行：

    ```bash
    python3 tools/annotate-pdf.py 原.pdf issues.json 输出.annotated.pdf
    ```
    ```

---

## § 7 匿名化占位符（v2 新增）

当节点 E 选择"匿名化"时，所有涉及个人身份的信息在报告中用占位符：

| 原值 | 占位符 |
|---|---|
| 作者姓名（首页识别） | `{{author}}` |
| 培养单位（首页识别） | `{{institution}}` |
| 指导教师 | `{{supervisor}}` |
| 学号 | `{{student_id}}` |
| 完成日期 | `{{completion_date}}` |
| 论文涉及的真实案件当事人姓名（非学者） | `{{case_party_N}}` |

**不匿名化**：

- 公开学者姓名（王利明、孙宪忠、Beauchamp 等已发表著作的引用）
- 公开机构名称（最高人民法院、北京市人大等）
- 法律名称、案号

---

## § 8 ⑦ 分维度评分表（v2 替代总评）

v2 取消"不达标/合格/优秀"等总体评分。改为**分 9 个维度独立评分**：

```markdown
### ⑦ 分维度评分表

| 维度 | 评分 | 说明 |
|---|---|---|
| ① 选题与问题意识 | A / B / C / D | ... |
| ② 结构与逻辑 | A / B / C / D | ... |
| ③ 论证深度 | A / B / C / D | ... |
| ④ 文献综述 | A / B / C / D | ... |
| ⑤ 实证 / 案例 | A / B / C / D | ... |
| ⑥ 法律规范适用 | A / B / C / D | ... |
| ⑦ 语言与学术规范 | A / B / C / D | ... |
| ⑧ 对策建议 | A / B / C / D | ... |
| ⑨ 引注格式 | A / B / C / D | ... |

- A：基本无问题或轻微建议
- B：有改进空间但不影响整体质量
- C：需重要修改
- D：需根本性重写或核实

**由用户 / 导师综合这 9 个维度独立判断整体是否达标**。本 skill 不做总体定性。
```

---

## § 9 合并执行顺序

1. 收 TR 产物
2. 收 CC 产物
3. 读 citation-crossref.json（§⑩ 材料）
4. 读 pdf-positions.json（若有，辅助锚点生成）
5. 把两类问题转为 issue 对象
6. 调用 `assign_group_ids()` 分配 group_id
7. 执行 `validate_issues_json(input_is_pdf=...)` 自校验
8. 按 §1 9+1 章节填入模板
9. 按 §3 打分 + §4 交叉引用 + §8 分维度评分
10. 匿名化处理（节点 E 选中时）→ 占位符替换
11. 输出 MD 报告
