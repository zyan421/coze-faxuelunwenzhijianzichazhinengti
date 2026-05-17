---
name: unified-thesis-reviewer
description: 法学论文"一站式"严格审查 skill（v2.7.0）。用户一次提交论文（docx/pdf/纯文本），本 skill 编排底层的 legal-thesis-reviewer（九大维度深度审查）与 legal-citation-checker（引注格式校对），输出统一 Markdown 报告 + 结构化 issues.json，并生成嵌入式批注文档（docx 的 Word 批注 / pdf 的嵌入式高亮批注），任何阅读器直接打开可见。v2.7 强化项：严重度判定标尺（反向举证原则）、批注挂载位置原则、PDF anchor 空格容错搜索、实战案例库
version: 2.7.0
keywords:
  - 统一审查
  - 一站式
  - 一站式审查
  - 全面审查
  - 综合审查
  - 综合审阅
  - 严格审查
  - 盲审式全面检查
  - 带批注的修订稿
  - 批注版
  - 一次性检查
  - 论文未定稿检查
  - 论文深度审查
  - 引注校对
  - 实质性审查
  - 思想性硬伤
  - OOXML 样式核查
  - thesis all-in-one review
  - unified thesis review
  - annotated pdf
  - annotated docx
depends_on:
  - legal-thesis-reviewer
  - legal-citation-checker
---

# 法学论文统一审查 Skill（Unified Thesis Reviewer v2.7.0）

## v2.7.0 关键升级（相对 v2.6.0）

- **W1 严重度判定标尺 + 反向举证原则**：`rules/issues-schema.md` § 2.0a 新增。明确 fatal/major/minor 的判定标尺，规定 fatal 必须通过"反向举证测试"——能否找到合理解释推翻这个指控？若能，不应定为 fatal。修复 v2.6 把"同段两脚注引同一文献同一页"定为致命级的过度指控
- **W2 批注挂载位置原则**：新增 `rules/annotation-placement.md`。规定三条原则——(1) 挂在读者最该看到的地方而非问题最早出现的地方；(2) 全文级问题挂在论文相关章节的章首段；(3) 比较性问题（"本文 vs 他人"）挂在"贡献说明"的位置。修复 v2.6 把"独创性问题挂在文献综述"和"监察法缺位挂在赵作海案段"两类挂载位置错位
- **W3 PDF anchor 空格容错搜索**：`tools/annotate-pdf.py` 增加 `_generate_space_tolerant_variants()`。PDF 排版常在中文↔数字、中文↔字母边界插入空格（"第188条" → "第 188 条"），导致 PyMuPDF 严格搜索失败。v2.7 自动尝试 4 种空格变体，把 v2.6 实测中 3 条降级为左上角便签的 anchor 全部救回
- **W4 anchor-text-locator 加 PDF 章节**：`rules/anchor-text-locator.md` § 7 新增"PDF 输入的特殊建议"，提示 Agent 优先选连续中文短句作 anchor，避免数字/字母混排
- **W5 实战案例库**：新建 `rules/case-studies.md`。把 v2 系列 7 个真实失误案例化为反面教材（脚注重复指控过严、独创性挂载错位、监察法挂载错位、PDF anchor 失败、藏民终17号编造证据、第四章自动编号误判、"幽灵引用"措辞过激），每个案例标注"指向的规则"，让未来 Agent 能从案例直接学习
- **W6 批注作者署名**：`tools/annotate-pdf.py` 也改为"张老师的AGENT"（v2.6 仅 docx 改了，pdf 路径未同步改）

---

## v2.6.0 关键升级（相对 v2.0.0）

- **V1 实质性维度专章审查**：新增 `rules/substantive-review.md` 七维度强制清单（选题/论证深度/逻辑周延/研究承诺/遗漏检查/理论实证闭环/建议可行性），实质性问题占比阈值（本科 ≥ 50%、硕士 ≥ 60%、博士 ≥ 65%）
- **V2 OOXML 样式层核查**：新增 `rules/ooxml-style-check.md`。修复"第四章自动编号 numPr 误判为漏字"类 bug，强制 docx 审查检查 pStyle / numPr / rPr 一致性
- **V3 表格审查规则**：新增 `rules/table-audit.md`。基于"藏民终17号"事件——审查表格必须先读表内容、提及脚注号必须先 verify、`[原文核对]` 标签必须有具体证据
- **V4 anchor_text 主路径定位**：`tools/inject-docx-comments.py` v2.6 用 anchor_text 全文搜索作主定位（paragraph_index 仅作向后兼容），批注命中率从 v2.0 的 15% 提升到 v2.6 的 100%（22/22、27/27、19/19 三次实测）。详见 `rules/anchor-text-locator.md`
- **V5 批注作者署名**：从写死的 "unified-thesis-reviewer" 改为 "张老师的agent"，与真人导师批注（署"张庆霖"等）形成视觉区分
- **V6 取消"幽灵引用"概念**：参考文献列出但未脚注引用不再视为问题——参考文献本质是"阅读/参考过的文献清单"。`citation-crossref.py` 保留输出但报告/批注不再生成对应 issue
- **V7 术语风格**：禁用"幽灵""造假""涉嫌"等高刺激词，鼓励用"疑需核实""建议核对""未匹配"等中性表述

---

## v2.0.0 关键升级（相对 v1.0.0）

- **U1 pdf 批注主路径切换**：弃用 XFDF 旁路文件，改用 `tools/annotate-pdf.py` 生成**嵌入式批注 pdf**（任何阅读器直接打开可见）
- **U2 pdf 文本位置自动提取**：新增 `tools/extract-pdf-text.py`，为 Agent 生成 issues.json 提供精确锚点与 bbox 参考
- **U3 时间基准感知**：主工作流 §0 强制获取当前日期，避免把已发生日期误判为"未来"
- **U4 联网核实强制执行**：从"降级为先"改为"强制执行为先"，每条高优先级事项 ≥ 2 次搜索，失败才降级
- **U5 假阳性守门**：致命指控必须证据链闭环，自动推进模式下 fatal 自动降级为 major（除非联网证据）
- **U6 引注方案自动推断**：节点 B 不再默认法学引注手册，基于 citation-crossref 样本**先推断**作者实际方案
- **U7 分维度评分代替总评**：报告⑦不再给"达标 / 不达标"总评，改为 9 个维度 A/B/C/D 独立评分
- **U8 交叉引用机械对比**：新增 `tools/citation-crossref.py` 自动生成"脱钩作者表"（v2.6 起取消"幽灵引用表"），替代 Agent 主观判断
- **U9 匿名化选项**：节点 E 允许用户选择是否对作者/单位/指导教师占位符化
- **U10 证据卡格式**：每条 issue 在报告中必须附"证据来源"标签（联网核实 / 原文核对 / 文本分析 / 规则依据 / 实质性硬伤 / OOXML 分析）

---

## 用途

面向法学院师生，作为**论文未定稿检查的一站式入口**。用户一次提交论文（docx / 文本版 pdf / 粘贴纯文本），skill 完成：

1. **论文深度审查**（九大维度，由 `legal-thesis-reviewer` 承担）：选题 / 结构 / 论证 / 文献综述 / 实证 / 规范适用 / 语言 / 对策 / 学术不端线索
2. **引注格式校对**（按 GB/T 7714 或《法学引注手册》，由 `legal-citation-checker` 承担）
3. **引用交叉对比**（v2 新增，`tools/citation-crossref.py`）：自动生成脚注/参考文献的机械对比表
4. **联网核实**（v2 强化，强制执行）：每条法律名称/案号/学术文献尝试 ≥ 2 次联网核实
5. **统一审查报告**（9+1 章节的 Markdown）
6. **结构化问题清单**（`issues.json`，含 anchor_text + 证据来源标签）
7. **嵌入式批注文档**（v2 新默认）：
   - docx 输入 → `{原名}.annotated.docx`（Word 批注副本）
   - pdf 输入 → `{原名}.annotated.pdf`（嵌入式高亮批注，任何阅读器直接打开）
   - 用户明确要 XFDF → `{原名}.xfdf`（v1 备用路径保留）

---

## 何时激活

- 用户明确要求"全面审查 / 一站式 / 综合审查 / 严格审查 / 盲审式检查"
- 用户要求"同时查论文内容和引注格式"
- 用户上传论文并说"给我一份带批注的修订稿 / 批注版"
- 关键词命中 front-matter keywords

**与底层 skill 分工**：
- 仅要求引注格式校对 → 直接调 `legal-citation-checker`
- 仅要求论文内容审查 → 直接调 `legal-thesis-reviewer`
- 要求一站式 → 本 skill 全流程 8 步编排

---

## 主工作流（v2.6，9 步，详见 `rules/orchestration-flow.md`）

**§0 时间基准**（v2 新增）：获取 current_date，为所有时效性判断建立基准

**§1 意图识别**：判断走 Unified / 仅 TR / 仅 CC 三条路径

**§2 输入采集**：读 pdf/docx/纯文本；pdf 时自动调用 `extract-pdf-text.py`

**§3 节点 A + B（v2 B 强化）**：
- 节点 A：确认论文类型（本科/硕士/博士/期刊投稿）
- 节点 B：推断引注方案（GB/T 7714 或法学引注手册或学校自定义），推断不明时默认**双方案对比**

**§3A 节点 C**：超大篇幅（> 15 万字）确认是否分章

**§4 调用 TR**：九大维度审查（含假阳性守门：致命指控必须证据闭环）

**§4.5 实质性维度专章审查**（v2.6 新增）：
- 按 `rules/substantive-review.md` 七维度清单逐项审视论文
- §4.5.1 OOXML 样式层核查（docx）—— 用 `rules/ooxml-style-check.md` 方法
- §4.5.2 表格审查规则 —— 用 `rules/table-audit.md` 方法
- 实质性问题占比阈值：本科 ≥ 50% / 硕士 ≥ 60% / 博士 ≥ 65%

**§5 调用 CC + citation-crossref**：引注校对 + 自动生成交叉对比表（v2.6 取消"幽灵引用"）

**§6 联网核实**（v2 强化）：高优先级 15 项强制执行，每条 ≥ 2 次搜索，多源交叉验证

**§7 合并输出 + 节点 E（v2 新增）**：
- 节点 E：是否匿名化报告
- 输出 MD 报告（9+1 章节）+ issues.json + positions.json + crossref.json

**§8 节点 D**：
- pdf → 生成 `.annotated.pdf`（默认走 `annotate-pdf.py`）
- docx → 生成 `.annotated.docx`（用 v2.6 的 `inject-docx-comments.py`，anchor_text 主路径定位）
- 明确要 XFDF → `generate-xfdf.py`（备用）

## 5 个用户交互节点（v2）

| 节点 | 时机 | 默认值 |
|---|---|---|
| A | 采集完成后 | 基于元数据推断 |
| B | 节点 A 后 | 基于样本**推断**（v2 强化） |
| C | 超过 15 万字时 | 按章拆分 |
| D | 合并完成后 | 生成 `.annotated.pdf` / `.annotated.docx`（v2 默认） |
| E | 节点 D 前（v2 新增） | 不匿名化 |

详见 `templates/interaction-prompts.md`。

---

## 总原则

1. **不重写已有规则库**：深度审查与引注校对规则继承自底层 skill
2. **永不静默丢问题**：任何失败路径都保留上一层产物（MD 是最低兜底）
3. **假阳性守门**（v2 新）：致命指控必须证据链闭环；自动推进模式下 fatal → major 除非联网确证
4. **时效性自检**（v2 新）：每份报告必须对照 current_date 做时间线自检
5. **跨平台可移植**：不依赖特定 agent 平台 API；但 v2 接受 PyMuPDF 作为 pdf 批注的软依赖
6. **版权合规**：引用但不包含 GB/T 7714—2015 / 法学引注手册原文（详见 `LICENSE-NOTICE.md`）

---

## 参考资源

### 规则（rules/）

- `orchestration-flow.md` — v2 主工作流 8 步
- `intent-recognition.md` — 意图识别
- `input-collection.md` — 输入采集
- `issues-schema.md` — issues.json 契约（v2 新增 anchor_text 字段）
- `report-merging.md` — Unified_Report 合并规则（v2 ⑩ 新增交叉引用表）
- `network-tool-discovery.md` — 联网工具识别
- `online-verification-unified.md` — 联网核实（v2 强制执行）
- `size-tier.md` — 篇幅分档
- `docx-annotation.md` — docx 批注算法
- `pdf-annotation.md` — **v2 新增，取代 xfdf-annotation.md**
- `xfdf-annotation.md` — v1 备用，用户明确要 XFDF 时使用
- `academic-integrity-guard.md` — **v2 新增：假阳性守门**
- `error-handling.md` — 错误决策树

### 模板（templates/）

- `unified-report-template.md` — 9+1 章节骨架（v2 新增⑩交叉引用表）
- `annotation-body-template.md` — 三路径共用的批注正文模板
- `interaction-prompts.md` — 5 个交互节点话术（v2 新增节点 E）
- `issues-json-skeleton.json` — 空 issues.json 骨架
- `readme-section-import-xfdf.md` — XFDF 导入指引（v2 降为备用）

### 工具脚本（tools/）

- `extract-docx.py` — docx 文本抽取（纯 stdlib，v1 保留）
- `extract-pdf-text.py` — **v2 新增**：pdf 文本 + 坐标提取（PyMuPDF）
- `inject-docx-comments.py` — docx 批注注入（纯 stdlib，v1 保留）
- `annotate-pdf.py` — **v2 新增主力**：pdf 嵌入式批注（PyMuPDF）
- `generate-xfdf.py` — v1 备用：XFDF 旁路文件（纯 stdlib）
- `citation-crossref.py` — **v2 新增**：引用交叉对比（PyMuPDF）
- `build-bundle.py` — 打包脚本
- `bundle-verify.py` — 发布前扫描
- `make-dist.py` — 生成 repo/ 分发目录

### 底层 skill bundle（由 build-bundle.py 填充）

- `_bundled/legal-thesis-reviewer.rules/`
- `_bundled/legal-citation-checker.rules/`

### 示例（examples/）

- `example-unified-review.md` — 端到端示例
- `example-issues.json` — 示例 issues.json

---

## 依赖

### 运行时依赖

| 依赖 | 必要性 | 用途 |
|---|---|---|
| Python 3.8+ | 必需 | 所有脚本 |
| `PyMuPDF` | **强推荐**（v2 新） | `annotate-pdf.py` / `extract-pdf-text.py` / `citation-crossref.py` |
| stdlib 其他 | 自带 | docx / xfdf 路径 |

**安装 PyMuPDF**：

```bash
pip install --user PyMuPDF
```

**无 PyMuPDF 时的降级**：

- docx 输入 → 走 `inject-docx-comments.py`（纯 stdlib），功能完整
- pdf 输入 → 退回 v1 XFDF 路径 `generate-xfdf.py`（纯 stdlib），但无法生成嵌入式 pdf

---

## 跨平台支持

| 平台 | 安装位置 | PyMuPDF 可用性 |
|---|---|---|
| Kiro | `.kiro/skills/unified-thesis-reviewer/` | 需 pip install |
| Claude Code | `.claude/skills/unified-thesis-reviewer/` | 需 pip install |
| Hermes | 注入 system prompt | 视宿主环境 |
| OpenClaw | `/skills/unified-thesis-reviewer/` | 视宿主环境 |
| Coze | 知识库 + 插件 | Coze 插件内 pip install |
| IMA | zip 解压 | 需本机 pip install |

命令行入口（任何具备 Python 3.8+ 的环境）：

```bash
# v2 pdf 批注（推荐）
python3 tools/annotate-pdf.py 原.pdf issues.json 输出.annotated.pdf

# v2 pdf 文本位置提取
python3 tools/extract-pdf-text.py 原.pdf 输出.positions.json

# v2 引用交叉对比
python3 tools/citation-crossref.py 原.pdf 输出.crossref.json

# v1 docx 批注（保留）
python3 tools/inject-docx-comments.py 原.docx issues.json 输出.annotated.docx

# v1 XFDF 备用路径
python3 tools/generate-xfdf.py 原.pdf issues.json 输出.xfdf
```
