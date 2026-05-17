# 错误决策树

本文件定义 `unified-thesis-reviewer` 对 8 类常见失败场景的处理路径、日志 schema、按失败类型的交付清单。本文件是 R15 的落地，也是 `rules/orchestration-flow.md` 所有步骤的统一错误出口。

## 索引

- 8 类错误决策树：§1
- JSON Lines 日志 schema：§2
- 按失败类型的交付清单表：§3
- 总原则：§4

---

## §1 八类错误决策树

每个错误给出：识别方式、何时触发、处理动作、用户话术。所有错误路径都遵循"**永不静默丢失**"——任何阶段的失败都保留上一层产物，直到兜底为 MD 报告。

### 错误 1：docx 加密（R2.4）

- **识别**：`zipfile.ZipFile(path)` 抛出密码相关异常；或 docx 内含 `EncryptedPackage` OLE 流
- **触发时机**：§2 输入采集
- **处理动作**：
  - 立即**终止**流程，不进入 §3 及之后
  - 不在聊天中请求用户提供密码（见 steering rule `secrets-in-chat-reflex.md`）
  - 写一条日志（stage=`collect`，level=`error`，event=`docx_encrypted`）
- **用户话术**：

  > 检测到加密的 docx 文件，本 skill 无法读取其内容。请在 Word 中打开该文件 → 文件 → 信息 → 保护文档 → 用密码进行加密 → 删除密码 → 另存为后重新提交。也可以直接把论文全文以纯文本方式粘贴到聊天（但 v2 批注文档将不可用）。

### 错误 2：pdf 扫描件（R2.3）

- **识别**：pdf 文本层可见字符数 **< 200**
- **触发时机**：§2 输入采集
- **处理动作**：
  - 立即**终止**流程
  - 不尝试 OCR
  - 写日志（stage=`collect`，level=`error`，event=`pdf_scanned`）
- **用户话术**：

  > 检测到 pdf 为扫描件（文本层仅 {N} 字符）。本 skill 不自动执行 OCR，请按以下任一方式处理后重试：1) 在 Adobe Acrobat / ABBYY / WPS 中做 OCR，导出可搜索文本的 pdf 或 docx；2) 用 Word 打开 pdf（自带 OCR）另存为 docx；3) 把全文复制粘贴为纯文本（v2/v3 批注不可用）。

### 错误 3：docx 损坏（R15.1）

- **识别**：`zipfile.BadZipFile`、`xml.etree.ElementTree.ParseError`、或 `[Content_Types].xml` 缺失
- **触发时机**：§2 输入采集
- **处理动作**：
  - 立即**终止**流程
  - 写日志（stage=`collect`，level=`error`，event=`docx_corrupted`）
- **用户话术**：

  > docx 文件解析失败（异常类型：{ExceptionType}）。建议在 Word 中打开原文件 → 文件 → 另存为 → docx 后重试，或直接把论文全文粘贴为纯文本。

### 错误 4：pdf 损坏（R15.2）

- **识别**：pdf 文件头不以 `%PDF-` 开头、MediaBox 解析失败、xref 表错乱
- **触发时机**：§2 输入采集
- **处理动作**：
  - 立即**终止**流程
  - 写日志（stage=`collect`，level=`error`，event=`pdf_corrupted`）
- **用户话术**：

  > pdf 文件解析失败（{原因简述}）。建议把它转为 docx 后重试，或直接把论文全文粘贴为纯文本。

### 错误 5：非法学论文（R15.5）

- **识别**：采集阶段的启发式检测——摘要 / 关键词 / 章节标题 / 参考文献类型与法学特征明显不符（如检测到"程序代码""临床诊疗""化学反应方程式"等强特征）
- **触发时机**：§2 输入采集后、§3 节点 A 前
- **处理动作**：
  - **不**终止流程，而是**插入一次询问**
  - 用户回复"继续" → 照常按法学论文审查标准执行，在报告开头注明"按用户确认，虽非典型法学论文仍按法学审查标准执行"
  - 用户回复"不继续" → 终止流程，不产生任何报告
  - 写日志（stage=`collect`，level=`warn`，event=`non_legal_thesis_suspected`）
- **用户话术**：

  > 该文档看起来不像法学论文（识别特征：{具体特征}）。是否继续按法学论文审查标准执行？A) 继续 / B) 不继续

### 错误 6：issues.json 自校验失败（R5.13）

- **识别**：`validate_issues_json()` 返回非空错误列表
- **触发时机**：§6 合并输出
- **处理动作**：
  - **仍交付** Markdown Unified_Report（MD 报告照交，不扣留）
  - **不**生成 `issues.json` 最终版（保留草稿供调试，或写为 `issues.json.draft`）
  - 在 MD 报告末尾**附加显著标注块**：

    > ⚠️ 结构化清单未生成，批注文档不可用
    >
    > **失败原因（前 5 条）**：
    > - issues[12]: id 格式不合法：`thesis_structure_012`（应为 `thesis-structure-012`）
    > - issues[38]: locator.paragraph_index 缺失
    > - issues[74]: scope=chapter 但 excerpt 非空
    > - ...

  - **v2/v3 跳过**：节点 D **不**触发（因为无合法 issues.json 可消费）
  - 写日志（stage=`merge`，level=`error`，event=`issues_validation_failed`，issue_ids=[具体错误 issue 的 id 列表]）

### 错误 7：v2 回读失败（R6.23）

- **识别**：`tools/inject-docx-comments.py` 的 `readback_verify()` 返回 `(False, errors)`
- **触发时机**：§7 节点 D 用户选"生成" + 输入是 docx
- **处理动作**：
  - **删除**已生成的 `.annotated.docx` 副本（避免留下坏文件）
  - **保留** MD 报告 + `issues.json` 作为兜底交付
  - **附错误日志**：写一份 `{原名}.annotated.docx.error.log`（格式按 §2），详述失败类型、失败 issue id 清单、可重试命令行
  - 在回复中说明"v2 批注文档生成失败，MD + JSON 仍可用；错误日志见 {路径}"
  - 写日志（stage=`v2`，level=`error`，event=`readback_invariant_fail`，issue_ids=[失败挂载的 issue 的 id 列表]）

### 错误 8：v3 回读失败（R7A.12）

- **识别**：`tools/generate-xfdf.py` 的 `readback_verify_xfdf()` 返回失败 或 annotation 数量与预期不符
- **触发时机**：§7 节点 D 用户选"生成" + 输入是 pdf
- **处理动作**：
  - **保留** MD 报告 + `issues.json`（即便删除 `.xfdf` 也不影响 MD/JSON）
  - **决定**是否删除 `.xfdf`：若只是 annotation 数量比预期少（因 skip/clip 导致），保留 `.xfdf`；若回读解析本身失败，删除
  - **附错误日志**：写一份 `{原名}.xfdf.error.log`（格式按 §2）
  - 在回复中说明"v3 XFDF 生成失败，MD + JSON 仍可用；错误日志见 {路径}"
  - 写日志（stage=`v3`，level=`error`，event=`xfdf_readback_fail`，issue_ids=[skip 或 clip 的 issue 的 id 列表]）

---

## §2 日志 schema

### 2.1 存储格式

- 格式：**JSON Lines**（每行一条完整 JSON 对象）
- 文件名：`.log/unified-reviewer-{YYYYMMDD-HHMMSS}.log`
- 编码：UTF-8 无 BOM
- 一次执行一份日志文件；如已存在则追加

### 2.2 字段约定

每条日志对象**必须**包含以下字段：

| 字段 | 类型 | 取值 / 说明 |
|---|---|---|
| `ts` | string | ISO 8601 时间戳，精确到秒，带时区偏移。例：`"2026-05-13T14:30:00+08:00"` |
| `stage` | string | 枚举：`collect` / `intent` / `interact` / `tr` / `cc` / `merge` / `v2` / `v3` |
| `level` | string | 枚举：`info` / `warn` / `error` |
| `event` | string | 事件类型，见 §2.3 事件枚举 |
| `issue_ids` | array | 相关 issue 的 id 数组；无关时填 `[]` |
| `detail` | string | 人类可读的详情描述（异常类型、触发条件、失败原因）|

### 2.3 事件类型枚举（event）

常用事件：

| event | 对应场景 |
|---|---|
| `docx_encrypted` | 错误 1 |
| `pdf_scanned` | 错误 2 |
| `docx_corrupted` | 错误 3 |
| `pdf_corrupted` | 错误 4 |
| `non_legal_thesis_suspected` | 错误 5 |
| `issues_validation_failed` | 错误 6 |
| `readback_invariant_fail` | 错误 7 |
| `xfdf_readback_fail` | 错误 8 |
| `online_verification_success` | `rules/online-verification-unified.md` 单条成功 |
| `online_verification_failed_per_item` | `rules/online-verification-unified.md` 单条失败 |
| `skipped` | `tools/generate-xfdf.py` 因 no-page-number 跳过 |
| `clipped` | `tools/generate-xfdf.py` 因 bbox 越界 clip |
| `fallback_to_chapter_head` | `tools/inject-docx-comments.py` 因段落定位失败回退 |
| `overflow_top500` | 批注条数 > 500，排序后取前 500 |
| `progress` | 进度反馈（见 `rules/size-tier.md` §3） |

### 2.4 标准日志行示例

```json
{"ts": "2026-05-13T14:30:00+08:00", "stage": "v2", "level": "error", "event": "readback_invariant_fail", "issue_ids": ["thesis-structure-012"], "detail": "commentRangeStart count (12) != commentRangeEnd count (11)"}
{"ts": "2026-05-13T14:30:02+08:00", "stage": "v3", "level": "info", "event": "skipped", "issue_ids": ["thesis-argumentation-038"], "detail": "no-page-number"}
{"ts": "2026-05-13T14:30:05+08:00", "stage": "v3", "level": "warn", "event": "clipped", "issue_ids": ["citation-citation-format-042"], "detail": "bbox out of page bounds, clipped to [0, 100, 612, 500]"}
{"ts": "2026-05-13T14:30:10+08:00", "stage": "merge", "level": "error", "event": "issues_validation_failed", "issue_ids": ["thesis-structure-012", "thesis-argumentation-038"], "detail": "id format invalid; locator.paragraph_index missing"}
```

---

## §3 按失败类型的交付清单表

下表列出 8 类错误发生时的最终交付产物。✅ = 该产物会被交付；❌ = 该产物不会产生或被删除；— = 不适用。

| 失败类型 | MD 报告 | issues.json | .annotated.docx | .xfdf | 错误日志 | 用户动作 |
|---|---|---|---|---|---|---|
| 1. docx 加密 | ❌ | ❌ | ❌ | ❌ | ✅ | 去除密码或提交纯文本 |
| 2. pdf 扫描件 | ❌ | ❌ | ❌ | ❌ | ✅ | 先做 OCR 或转 docx |
| 3. docx 损坏 | ❌ | ❌ | ❌ | ❌ | ✅ | Word 另存为后重试 |
| 4. pdf 损坏 | ❌ | ❌ | ❌ | ❌ | ✅ | 转 docx 或粘贴纯文本 |
| 5. 非法学论文 | ✅（用户确认继续时）或 ❌ | 同左 | 同左 | 同左 | ✅ | 回复 A) 继续 或 B) 不继续 |
| 6. issues.json 自校验失败 | ✅（含失败原因标注块） | ❌（仅保留 `.draft`） | ❌（跳过） | ❌（跳过） | ✅ | 查看 MD 末尾失败原因；修复后重跑 |
| 7. v2 回读失败 | ✅ | ✅ | ❌（已删除） | — | ✅ | 查看错误日志；可用命令行重试 |
| 8. v3 回读失败 | ✅ | ✅ | — | ❌（删除）或 ✅（保留残次） | ✅ | 查看错误日志；可用命令行重试 |

### 3.1 可重试命令行

对于错误 6–8，错误日志**必须**附一条可重试命令行，形如：

```bash
# 错误 7 的重试（修复 issues.json 或换 docx 后）
python3 tools/inject-docx-comments.py 原.docx issues.json 输出.annotated.docx

# 错误 8 的重试
python3 tools/generate-xfdf.py 原.pdf issues.json 输出.xfdf
```

### 3.2 错误日志的文件名规范

- 主日志：`.log/unified-reviewer-{YYYYMMDD-HHMMSS}.log`（所有 event 都写入）
- v2 专用错误日志（错误 7）：`{原名}.annotated.docx.error.log`
- v3 专用错误日志（错误 8）：`{原名}.xfdf.error.log`

专用错误日志是主日志的**子集**，仅包含与本次脚本调用直接相关的错误条目，方便用户定位问题。

---

## §4 总原则

### 4.1 永不静默丢失

任何阶段的失败都**必须**保留上一层产物：

- v3 失败 → 仍交付 MD + JSON + v2 产物（若 docx 输入且已生成）
- v2 失败 → 仍交付 MD + JSON
- issues.json 失败 → 仍交付 MD（末尾附降级标注块）
- 采集阶段失败（错误 1–4）→ 无法产出 MD，但仍写错误日志，且给用户明确的重试路径

### 4.2 不臆测、不伪造

- 不臆测扫描件的内容、不伪造联网核实结果、不代替用户决定是否按法学标准审查
- 所有涉及"无法确定"的信息都要显式标"待核实"或"待用户确认"

### 4.3 错误话术的可操作性

每一条错误话术都必须含**可操作的下一步**（具体菜单路径、具体命令行、具体重试方式），不要只说"请检查后重试"。

### 4.4 版权合规不让步

错误路径中不得临时放宽版权要求——即便是为了给用户一个"完整交付"，也不把 GB/T 7714 原文、《法学引注手册》原文写入错误日志或 MD 报告。
