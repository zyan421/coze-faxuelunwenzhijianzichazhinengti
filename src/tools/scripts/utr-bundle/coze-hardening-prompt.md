# Coze Agent 硬化任务：从“能回答”升级为真正的 unified-thesis-reviewer

当前项目存在两个严重问题：

1. 用户上传 Word 论文后经常返回“批注文档生成失败，但审查结果没有丢失”。
2. Markdown 审查报告过于浅显，问题发现敷衍，没有达到 `unified-thesis-reviewer` / `legal-thesis-reviewer` 的应有深度。

请不要再做泛泛提示词优化。请按下面的验收标准进行工程级修复。

## 一、先复现并修复 Word 批注失败

请使用我上传的 `论文初稿.docx` 做真实端到端测试。

必须完成：

1. 找到上传文件在 Coze Coding 容器中的真实路径。
2. 用当前项目的 docx 读取函数抽取正文、脚注、段落索引。
3. 生成一个最小合法 `issues.json`，至少含 3 条 issue，每条必须有：
   - `id`
   - `source`
   - `category`
   - `severity`
   - `scope`
   - `locator`
   - `excerpt`
   - `anchor_text`
   - `problem`
   - `suggestion`
   - `group_id`
4. 运行 Word 批注生成函数，必须生成 `.annotated.docx`。
5. 生成后回读校验：
   - `[Content_Types].xml` 可解析
   - `word/document.xml` 可解析
   - `word/comments.xml` 可解析
   - `commentRangeStart` 数量等于 `commentRangeEnd`
   - `w:comment` 数量等于 `w:commentReference`
6. 如果失败，不能吞掉异常；必须输出具体失败文件、函数、堆栈、失败 issue id、定位字段。

硬性要求：

- 不允许继续只返回“批注文档生成失败”的泛化话术。
- 不允许只生成 Markdown 报告而跳过批注文档。
- 不允许修改原文正文，只能生成 Word Comments。
- 不允许把 `anchor_text` 缺失、locator 越界、comments.xml 缺关系声明这类工程错误隐藏起来。

## 二、必须移植真实 unified-thesis-reviewer v2.7 规则

请优先阅读我上传的真实规则文件，而不是自己凭印象写规则。

最低必须实现：

1. `issues.json` 契约
   - 按 `rules/issues-schema.md` 校验。
   - `anchor_text` 必填。
   - fatal / major / minor 按严重度标尺和反向举证原则判定。

2. Word 批注算法
   - 按 `rules/docx-annotation.md` 和 `tools/inject-docx-comments.py` 实现。
   - anchor_text 全文搜索优先；paragraph_index 只作兜底。
   - 定位失败必须降级挂到章节首段或文档首段，绝不丢 issue。

3. 批注挂载位置
   - 按 `rules/annotation-placement.md`。
   - 全文级问题挂到相关章节章首。
   - 比较性 / 贡献评价问题挂到贡献说明位置。
   - 不要机械挂在问题最早出现处。

4. 实质性审查
   - 按 `rules/substantive-review.md`。
   - 每次完整审查至少生成 5-10 条实质性 issue。
   - 本科论文实质性问题占比不得低于 50%；硕士不得低于 60%；博士不得低于 65%。
   - 如果占比不达标，必须触发“再审一轮”。

5. 张老师评阅书风格
   - 读取 `legal-thesis-reviewer.rules/rules/zhangqinglin-patterns.md` 和 `phrase-bank.md`。
   - 重点查：选题问题意识、结构逻辑、论证深度、文献综述、实证/案例、法律规范适用、语言规范、对策建议、学术不端线索。
   - 不要只查错别字、格式、泛泛“论证不足”。

## 三、审查报告必须变深

报告必须至少包含：

1. 论文基本信息：题目、类型推断、篇幅、章节结构、脚注/参考文献统计。
2. 致命/重大问题：只在证据链闭环时使用 fatal；否则降为 major 或标“疑需核实”。
3. 九大维度评分：每个维度 A/B/C/D，不给空泛总评。
4. 实质性硬伤专章：至少 5 条，每条必须有“原文定位 + 冲突点 + 后果评估 + 可操作建议”。
5. 引注格式与交叉引用问题。
6. “遗漏但应讨论”的问题。
7. Top 10 修改优先级。
8. 可供导师讨论/答辩准备的问题。

每条 issue 必须按以下证据标签之一开头：

- `[联网核实]`
- `[原文核对]`
- `[文本分析]`
- `[OOXML分析]`
- `[实质性硬伤]`
- `[元层评估]`
- `[规则依据]`

禁止：

- “本章论证不够深入”这种空话。
- “建议补充更多数据”但不说补什么。
- “国外研究可以更丰富”但不指出关键遗漏。
- 未联网却声称“已核实”。
- 把“可能存在”写成“确定造假”。

## 四、文件上传入口的现实处理

如果 Coze 右侧 Agent 测试面板仍显示“最多支持 0 个文件”，请不要伪装成已经支持平台原生上传。

必须明确区分：

- 代码层已经支持：URL、file_id、直接粘贴文本、本地工程测试文件。
- 平台 UI 层若还未打开：需要用户到 Coze Agent 配置中启用文件上传。

在代码层，必须支持这些输入：

- `.docx`
- 文本型 `.pdf`
- `.txt`
- `.md`
- 文件 URL
- Coze 平台 file_id / 附件对象
- 直接粘贴全文

## 五、本轮必须交付

请完成后给出：

1. 修改了哪些文件。
2. `论文初稿.docx` 端到端测试结果：
   - 是否读取成功
   - 字数
   - 段落数
   - 脚注数
   - 生成 issue 数量
   - 实质性 issue 占比
   - 是否生成 `.annotated.docx`
   - 输出文件路径
3. 如果 `.annotated.docx` 仍失败，给出具体错误堆栈和下一步修复点。
4. 贴出 3 条真实生成的高质量实质性 issue 示例。
