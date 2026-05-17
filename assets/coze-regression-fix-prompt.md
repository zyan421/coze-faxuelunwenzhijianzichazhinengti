# Coze Agent 回归失败修复：真实预览上传仍未通过

刚才在右侧 Agent 效果测试中，我上传了真实文件 `论文初稿.docx` 并发起完整审查。页面显示工具调用链包括：

- `read_paper_file`
- `generate_issues_json`
- `generate_annotated_docx`
- `generate_annotated_docx`
- `generate_markdown_report`

但是最终结果仍然失败：

> 批注文档生成失败，但审查结果没有丢失。我先返回 Markdown 审查报告、问题清单和优先修改指引。

这说明上轮“加固包整合完成”只是本地 fixture 测通，并没有让真实 Agent 预览工作流通过。

## 一、必须修复的回归问题

### 1. 批注文档仍然生成失败

当前不能再吞掉异常。请定位真实预览调用中 `generate_annotated_docx` 失败的具体原因，并修复。

重点检查：

- `generate_issues_json` 输出是否符合 `utr-bundle/unified-thesis-reviewer/tools/inject-docx-comments.py` 的 schema 校验。
- 当前预览结果里出现的是 `ISS-001`、中文严重程度“严重/建议”、中文问题维度，这明显不是 UTR 内部 schema。
- `inject-docx-comments.py` 需要的是：
  - `schema_version: "1.0"`
  - `issues: [...]`
  - id 形如 `thesis-argumentation-001` / `citation-citation-format-001`
  - `source` 为 `thesis` 或 `citation`
  - `category` 为枚举值
  - `severity` 为 `fatal` / `major` / `minor`
  - `scope` 为 `document` / `chapter` / `paragraph` / `sentence` / `span`
  - `locator.chapter` 必须是非空字符串
  - `locator.paragraph_index` 必须是整数
  - `anchor_text` 必填且能在 docx 文本中定位
  - `suggestion` 必须是字符串数组，不是单个字符串
  - `group_id` 形如 `g-001`

请把“面向用户的中文表格 issue”和“内部用于批注注入的 UTR schema issue”分离：

- 内部：严格 UTR schema，供 `inject-docx-comments.py` 使用。
- 外部：可以转换成中文表格展示给用户。

### 2. 失败时必须返回真实堆栈

如果 `.annotated.docx` 仍失败，Agent 回复里必须包含：

- 失败函数名
- 失败 issue id
- issues_json 校验错误
- Python exception 类型和 message
- stderr / traceback
- 失败前生成的临时文件路径

禁止再返回泛化话术“批注文档生成失败，但审查结果没有丢失”。

### 3. 质量门禁没有生效

本次预览请求明确要求：

- 不少于 8 条 issues
- 实质性问题不少于 5 条

但 Agent 只返回 7 条。请把质量门禁写成代码级校验：

- 若完整审查 issue 数 < 8，自动二次生成。
- 若实质性 issue 数 < 5，自动二次生成。
- 若本科论文实质性 issue 占比 < 50%，自动二次生成。
- 若 `anchor_text` 为空或无法定位，不得进入批注生成；必须修复定位或降级到章节首段。

### 4. 严重事实指控过于冒进

本次结果把以下内容列为严重问题：

- “2024 年未发布的财政部政府采购统计数据”
- “2026 年未来时间发布的虚构审计案例”
- “2 篇 2026 年未发表的虚构文献”

请注意当前日期是 `2026-05-18`。凡涉及“未发布”“虚构”“学术不端”的判断，必须先调用 `verify_fact` 联网核实，并按 UTR v2.7 的“严重度反向举证原则”处理：

- 没有联网证据闭环，不得使用“虚构”“学术不端”这类确定性表述。
- 自动推进模式下，fatal 必须降为 major，除非联网核实闭环。
- 不要把可疑问题写成定罪式结论。

## 二、本轮必须新增/修改的代码能力

1. 新增 `normalize_issues_for_annotation(raw_issues, paper_text, paragraphs)`：
   - 把模型生成的中文问题表转换为严格 UTR schema。
   - 自动补齐 `chapter`、`paragraph_index`、`anchor_text`、`excerpt`、`group_id`。
   - 校验失败时返回可读错误，不得静默。

2. 新增 `validate_annotation_issues(issues_json, docx_path)`：
   - 调用或复用 `inject-docx-comments.py` 的 `validate_issues_json`。
   - 检查 `anchor_text` 是否能在提取文本中找到。
   - 输出不合格 issue 列表和修复建议。

3. 修改 `generate_annotated_docx`：
   - 只接受严格 UTR schema。
   - 注入前先保存 `debug.issues.json`。
   - 捕获异常时把 stderr / traceback 返回给 Agent。
   - 成功后必须回读校验：
     - `[Content_Types].xml`
     - `word/document.xml`
     - `word/comments.xml`
     - `commentRangeStart == commentRangeEnd`
     - `w:comment == w:commentReference`
   - 成功时返回 `.annotated.docx` 的可下载链接或明确路径。

4. 修改 `test_run` 或新增真实回归测试：
   - 用右侧预览同一份 `论文初稿.docx` 的文件对象 / file_id 路径模拟真实调用。
   - 断言：
     - 读取成功
     - issue 数 >= 8
     - 实质性 issue 数 >= 5
     - 生成 `.annotated.docx`
     - 不出现“批注文档生成失败”
     - 若失败，输出完整堆栈

## 三、本轮验收标准

完成后请返回：

1. 修改的文件列表。
2. 真实预览失败的根因。
3. 新的内部 `issues.json` 示例 2 条，必须是严格 UTR schema。
4. `.annotated.docx` 是否生成成功。
5. 回读校验结果。
6. 新一轮 Agent 预览测试结果：
   - issue 总数
   - 实质性 issue 数
   - 是否有批注文档链接
   - 是否仍出现“批注文档生成失败”

如果平台测试面板仍显示“最多支持 0 个文件”，也请如实说明；不要把“实际附件可挂上”包装成“平台上传数量限制已修复”。
