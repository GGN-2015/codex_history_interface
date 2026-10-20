# codex_history_interface

读取当前用户本地 Codex 历史的 Python 库。采用面向对象设计：抽象类 `I_CodexInterface` 定义六个方法，`C_CodexInterface` 提供只读访问和 TeX 导出实现；不依赖 Skill、Codex App 自动化或 OpenAI API。Markdown 转换依赖随安装自动安装的 `markdown-it-py`、`mdit-py-plugins`：成熟解析器负责文档结构，公式正文尽量原样保留，生成器负责包裹和必要转义。未闭合或涉及危险 TeX 操作的个别片段作为原文展示并告警，不阻断整篇导出。不查找或调用编译器，也不生成 PDF；运行导出不需要安装 TeX。

## 安装与使用

要求 Python 3.10 或更高版本。在项目目录执行：

```powershell
python -m pip install -e .
```

```python
from codex_history_interface import C_CodexInterface, I_CodexInterface

reader: I_CodexInterface = C_CodexInterface()
chat_ids = reader.getChatList()

for chat_id in chat_ids:
    print(chat_id, reader.getChatTitle(chat_id))

if chat_ids:
    for message in reader.getChatMessage(chat_ids[0]):
        print(message["role"], message["content"])
```

### 六个接口

| 方法 | 返回值 | 说明 |
| --- | --- | --- |
| `getChatList()` | `list[str]` | 全部本地对话的完整 ID，默认同时包含未归档、已归档和状态不明的记录；按可用更新时间倒序，无数量上限 |
| `getChatTitle(chat_id)` | `str` | 读取持久化展示名；缺少展示名时使用 `"<TitleMissing>" + 已有标题字符串`，无可用标题时仅用标记；最终标题超过 40 字符时取前 40 字符再加 `...` |
| `getChatMessage(chat_id)` | `list[D_ChatMessage]` | 按日志顺序排列的用户消息和 Codex 最终答复 |
| `getLastMessageTimeStamp(chat_id)` | `float \| None` | 用户消息及最终答复中最新的可用 Unix 秒级时间戳；没有可用消息时间时返回 `None` |
| `getCreateTimeStamp(chat_id)` | `float \| None` | 对话创建时间的 Unix 秒级时间戳；没有可用创建时间时返回 `None` |
| `exportToTex(chat_id, output_path, *, overwrite=False)` | `pathlib.Path` | 导出标题、本机时区副标题和按轮次分节的用户/最终答复，返回目标 `.tex` 文件的绝对路径；个别内容的排版兼容问题通过告警报告，不执行编译 |

`chat_id` 必须是 `getChatList()` 返回的完整 ID，不接受标题或 ID 前缀。`D_ChatMessage` 是普通字典的类型定义，实际返回值可直接序列化：

```python
[
    {"role": "user", "content": "用户的问题"},
    {"role": "assistant", "content": "Codex 的最终回答"},
]
```

不存在的 ID 抛出 `ChatNotFoundError`；数据目录或正文缺失抛出 `HistoryUnavailableError`。有索引但没有正文的对话仍会出现在列表中，不能把缺失正文伪装成完整聊天记录。`TITLE_MISSING` 常量的值就是 `"<TitleMissing>"`。

### 展示名与 Codex 界面

展示名依次读取 **非空的 `threads.name` → `session_index.jsonl` 最新记录的 `thread_name`**。读取原文，不重新概括、不改变空格或标点；返回时统一应用下述长度规则，归档记录采用相同规则。

同一 ID 的重复索引记录，若两个更新时间都能解析，则取较新者；时间相同或不可比较时取文件中最后一条。数据库按数值版本由高到低读取，同 ID 不用旧库覆盖新库。

数据库 `threads.title` 可能只是旧标题或初始提示词，因此**绝不直接把它当作当前界面标题**，也不使用摘要、全局描述缓存或第一句话冒充展示名。缺少上述展示名来源时，依次选取 **`session_index.jsonl` 最新记录的 `title` → `threads.title`** 中第一个非空、非纯空白的字符串，并拼接为 `"<TitleMissing>" + 该字符串`，例如 `"<TitleMissing>原有标题"`。前缀与原标题直接拼接，不插入空格或其他分隔符；没有可用标题字符串时只使用 `"<TitleMissing>"`。该前缀明确表示后面的旧标题不是已确认的界面展示名；有可靠展示名时不添加前缀。

**标题长度规则**：对最终标题字符串统一按 Python Unicode 码点计数，可能存在的 `"<TitleMissing>"` 前缀也计入长度。不超过 40 字符时原样返回；超过 40 字符时返回前 40 字符加三个英文句点 `...`，结果总长为 43 字符。此规则同时适用于 Python 接口和命令行，只影响返回值，不修改本地历史记录，也不截断消息正文。

每次调用 `getChatTitle()` 都重新读取展示名元数据，下一次调用即可读取已持久化的重命名。尚未写入本机存储的界面变化无法从磁盘获知；此库不声称可以读取 App 的未持久化内存状态。

### 时间戳

```python
created_at = reader.getCreateTimeStamp(chat_id)
last_message_at = reader.getLastMessageTimeStamp(chat_id)
```

- 两个方法均返回 `float` 类型的 Unix 秒级时间戳，保留小数秒；原始毫秒值会换算成秒。ISO 8601 时区偏移会统一到同一时间轴；没有时区的 ISO 时间按 UTC 解释。`None` 表示缺失，合法的 `0.0` 不代表缺失。
- Unix 数值本身不携带时区，不通过加减时差来改变数值。文档中的时间会转换为本机默认时区，显示日期、时间和 UTC 偏移；转换使用该时刻的系统时区规则，不硬编码 `+08:00`。
- 创建时间依次读取 `threads.created_at_ms`、`threads.created_at`；缺失时取各 JSONL `session_meta` 中最早的有效创建时间，优先使用 payload 的时间；仍缺失则尝试索引的 `created_at_ms`、`created_at`。
- 最后消息时间取与 `getChatMessage()` 相同可见性规则下的用户消息、最终答复记录的最大有效时间戳，优先读取记录外层时间，再尝试 payload 时间。没有可见 JSONL 消息时才回退到 SQLite 投影的消息时间；使用投影时发出覆盖限制警告。
- 不将 `updated_at`、标题重命名时间、归档时间、思考/工具事件时间或文件修改时间冒充消息时间或创建时间。可见 JSONL 消息缺少时间时，不用另一份投影的时间填补。仅有元数据或索引的任务仍可查询，缺少相应时间返回 `None`，而不是伪造当前时间。
- 两个方法每次调用都会重新读取本地数据，同时支持未归档和已归档任务；ID 校验与原有方法相同，访问仍严格只读。

### 消息筛选

- 保留用户实际发言、Codex 的最终答复；旧版没有 `phase` 标记的普通助手消息也保留。
- 排除分析/推理、`commentary` 进度、系统/开发者提示词、AGENTS 指令、注入的环境上下文、工具调用及工具原始结果、压缩事件等内部记录。
- 合并 response/event/task-complete 的镜像和重复日志副本；同一来源中实际重复发送的消息仍保留，不按相似度去重。
- 原始 JSON 日志和数据库行不混入返回值；用户或助手真正说出的 JSON、代码、数学公式及换行保持原文。`getChatMessage()` 不转换正文格式；`exportToTex()` 单独负责文档生成与排版兼容处理。
- 图片、音频和附件仅返回简短占位文本，不返回二进制或 Base64，也不下载附件。
- 优先读取 JSONL；没有可见日志消息时才回退到 SQLite 历史投影，并发出覆盖限制警告。不会把较旧投影拼到已有日志回答后面。投影的 `final_agent_item_id` 可用时，用它排除同轮次非最终答复。

未知格式、坏行、文件变化、不可读来源等通过 `HistoryReadWarning` 报告，详细诊断可从具体实现的 `reader.diagnostics` 读取。不要把有警告的结果当成无遗漏的完整历史。日志每次按开始读取时的字节边界提取，不声称提供跨文件和数据库的原子快照。

### TeX 导出

```python
from pathlib import Path
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface()
chat_id = reader.getChatList()[0]
output = reader.exportToTex(chat_id, Path.home() / "Desktop" / "conversation.tex")
```

- 标题与 `getChatTitle()` 使用相同的展示名、缺失前缀和 40 字符截断规则。副标题显示创建时间、最后修改时间，均转换为本机默认时区并带 UTC 偏移；缺失时间明确显示“未知”。
- 最后修改时间优先读取 `threads.updated_at_ms`、`threads.updated_at`，其次取索引对应字段，仍缺失时使用最后可见消息时间。它与 `getLastMessageTimeStamp()` 的“最后用户消息/最终答复时间”不是同一个概念。
- 每次用户提问开始一个 `section`，其中分为“用户提问”和“AI 回答”。同轮多个最终输出完整保留；孤立答复或尚无答复的轮次明确标注，不编造正文。不导出思考、进度、系统/开发者提示词、工具日志或底层历史 JSON。
- Markdown 表格转换为可跨页、重复加粗表头的 `longtable`；保留单元格对齐、行内格式和 `<br>` 换行。公式或行内代码中的 `|` 不会被误分成额外列。
- 行内/独立数学公式及常见 AMS 环境保留为数学排版；支持紧接正文、列表或引用的多行 `\[...\]`，公式中的独立 `=` 行不会误判为 Markdown 标题。相邻公式和公式后的正文完整保留。常见 Unicode 数学符号转为等价标准 TeX，公式内部的空行规整为合法数学空白；不改变数学结论。
- 保留 `\boxed` 方框、数学下划线、上下标、分式、矩阵、对齐、重音等常见公式结构；支持标准颜色名和混色，以及 `\fbox`、`\colorbox`、`\fcolorbox`。`\text{...}` 中的字面下划线等特殊字符安全转义，不把其中的 Unicode 正文误改成数学命令。
- 支持粗体、斜体、`~~删除线~~`，以及无属性的 `<u>`/`<ins>` 下划线、`<s>`/`<del>` 删除线、`<sub>`/`<sup>` 上下标、`<kbd>` 等宽字和 `<mark>` 高亮；其他 HTML 当作文字，不执行脚本、样式或事件属性。
- 行内代码及代码段逐字符转义，保留代码内容、换行和缩进，不执行代码。代码块采用带浅灰背景、边框、语言标记且可跨页的排版，长行允许折行。正文中的长文件名与路径也可以折行。
- Markdown 本地图片/文件链接、文件引用指令、正文中的绝对文件路径，以及可定位的用户附件，自动复制到 `.tex` **同一目录**；输出只引用副本的相对文件名。相对源路径根据会话数据库或日志的原始 `cwd` 定位，不猜测当前进程目录；有歧义时明确告警。代码段内部的路径作为代码原文保留，不按附件抓取。
- 附件副本使用安全文件名及内容哈希，避免同名文件相互覆盖；重复引用同一文件只复制一次，重复导出复用字节一致的副本，已有副本内容不一致时报错而不是覆盖。源文件只读，Codex 数据目录、显式历史数据库和已识别日志不会作为附件复制。
- 本地 PNG/JPEG/PDF 图片用 `includegraphics` 嵌入，其他图片格式复制后作为可点击附件保留。历史记录中明确保存的 base64 图片解码为同目录图片文件，不把 base64 或原始 JSON 写进文档；历史只保留占位符且没有可定位文件的附件不能凭空恢复。缺失、不可用或受保护的本地附件在文档中标注“本地附件不可用，未复制”，并通过 `HistoryReadWarning` 和 `reader.diagnostics` 告警，不保留失效的原始绝对路径链接。
- HTTP/HTTPS 网页、远程图片只生成蓝色高亮的可点击超链接，**不下载页面或远程图片**。本地附件使用相对 `run:` 文件链接；部分 PDF 阅读器可能要求确认后才打开本地附件。
- 成功的导出只保留指定的 UTF-8 `.tex` 和所需附件副本，不生成 PDF、编译日志或辅助文件。目标必须位于 Codex 数据目录之外，不能替换任何已识别的历史文件。默认不覆盖已有 `.tex`，确需覆盖时传入 `overwrite=True`；新目标的父目录会在文档与附件准备完成后创建。发布失败会清理此次新建的附件，不删除先前已有的副本。

#### 安全生成与局部兼容处理

导出不提供 `compiler`、`timeout` 参数，不调用编译器或 AI 服务，也不设置“整篇静态检查失败就停止导出”的末尾关卡。程序在生成各个结构时预防可确定的问题：

- Markdown 标题、表格、列表、引用、代码等结构由 `markdown-it-py` 及数学插件解析，再按 token 生成 LaTeX。不使用对整条消息统一转义或正则替换的方式转换文档。
- 公式命令、参数写法、常规空白和注释尽量原样保留：`\frac12` 仍是 `\frac12`，`\mathclap`、`\coloneqq`、`\varinjlim` 等不会因为旧白名单未收录而变成普通文字。不再递归解释数学命令参数，也不判断上下标、列数或数学含义是否正确。
- 正文、代码和明确的数学文本参数分别转义，避免把 `$a_b$` 中的下标当作正文下划线。必要的兼容处理仅包括常见 Unicode 数学符号转换、数学空行规整、文本参数的字面特殊字符，以及为末尾注释补充换行，防止它吞掉生成的闭合定界符。
- 生成器根据上下文选择行内/显示公式包裹；完整的 AMS 显示环境不再嵌进 `\[...\]`。在标题、表格单元格或行内样式里，`equation` 去掉显示外壳，`align` 等换成对应的行内兼容环境，保留其中的公式正文。
- 公式标签统一分配不重复的内部名称；引用支持跨消息和前向解析，优先匹配同一条消息中的定义，再匹配前文或后文。没有对应定义的引用输出带原始名称的可见提示并告警，不生成悬空引用。代码中的类似字符串仅作为原文展示。
- 表格根据列数计算正的列宽和合适的列间距。列表/引用超出原生环境的嵌套预算时，改用缩进段落，保留编号和正文，而不是生成过深的 LaTeX 列表或中止导出。
- 只保留轻量的片段边界保护：分组/环境不配对、嵌套的数学定界符，或明确的文件读写、执行、宏定义等 TeX 操作，仅该片段标注为“公式原文”，转义后保留并发出 `TexExportWarning`，其他内容继续正常排版。普通的未知数学命令不会触发该告警；此保护不是完整的 TeX 解释器或编译沙箱。
- 无法继续递归的个别样式片段同样保留原文并告警；不能直接写入 TeX 的控制字符显示为 `[U+XXXX]`，不悄悄删除内容。告警包含轮次、消息角色及相关公式位置，详细信息保存在 `reader.diagnostics`。

这些处理是可复现的生成规则，不是模型自动修复。公式原文若本身缺少命令参数，程序不猜测补全；使用模板之外的宏时，也不会自动加载未知宏定义或宏包。历史读取失败、禁止的输出位置、未授权的覆盖、附件发布冲突和 I/O 故障仍按异常报告；内容兼容告警不等同于文件操作成功。内部文档结构检查用于回归测试，不作为运行时的整篇拒绝导出条件。

输出以内容和排版语义接近 Codex 界面为目标，而不是复刻客户端的像素布局。回归测试验证生成结构，不运行 TeX 或验证 PDF 外观；外部字体、宏包及最终分页仍取决于用户的排版环境。

用户若自行用外部工具排版，生成文件面向 XeLaTeX，使用 `ctexart`、Fandol 字体、`amsmath`、`amssymb`、`mathtools`、`bm`、`longtable`、`booktabs`、`array`、`geometry`、`graphicx`、`xcolor`、`ulem`、`tcolorbox`（含 `breakable`）和 `hyperref`。项目不会检查或安装这些 TeX 依赖，也不会下载网络资源。

### 自动定位

默认无需传入路径，定位顺序为：

1. 构造函数的 `codex_home`。
2. 当前进程的非空 `CODEX_HOME`。
3. 当前用户的 `~/.codex`，并兼容当前环境的 `USERPROFILE`/`HOME`。

Windows 通常为 `C:\Users\当前用户\.codex`，macOS/Linux 通常为 `/Users/当前用户/.codex` 或 `/home/当前用户/.codex`。WSL、SSH、容器与宿主系统的数据目录是独立的，应在对应环境运行或明确传入路径。

```python
reader = C_CodexInterface(codex_home=r"D:\CodexData")
reader = C_CodexInterface(
    codex_home=r"D:\CodexData",
    databases=[r"E:\CodexSqlite\state_5.sqlite"],
)
```

动态识别 `state*.sqlite`、`state*.db`、`thread_history*.sqlite`，也检查数据根目录的 `sqlite` 子目录；不硬编码数据库版本。非标准数据库位置可以用 `databases` 指定。递归合并 `sessions/**/*.jsonl`、`archived_sessions/**/*.jsonl`、标题索引和历史投影；陈旧的 `rollout_path` 不会阻止从实际目录定位已归档正文。

### 只读边界

所有历史数据库使用 SQLite `mode=ro` 和 `PRAGMA query_only=ON`，正文/索引仅以二进制只读模式打开。没有归档、重命名、修复、迁移、删除、checkpoint 或写回历史的接口；不会访问 `auth.json`、令牌或其他与历史无关的秘密。

SQLite 正常读取 WAL 可能参与锁和共享内存协调；“只读”指不修改历史内容，不等同于保证整个文件系统没有锁/共享内存活动。不可删除 WAL、SHM 或锁文件来解决读取错误。

## 命令行（可选）

列表、标题、消息和 TeX 导出均有命令行入口，不需要生成内联脚本；两个数值时间戳方法通过 Python 接口调用：

```powershell
codex-history list
codex-history title '完整对话ID'
codex-history messages '完整对话ID'
codex-history export-tex '完整对话ID' 'C:\exports\conversation.tex'
```

也可使用 `python -m codex_history_interface`。全局 `--home`、可重复的 `--db` 参数放在子命令之前。导出子命令只接受 `--overwrite` 可选开关，不接受 `--compiler` 或 `--timeout`。结果为 UTF-8 JSON 输出到标准输出（导出成功时为目标绝对路径）；个别内容的 `TexExportWarning` 输出到标准错误，成功导出的退出码仍为 `0`。真正的操作错误输出到标准错误且退出码为 `2`；程序本身不向历史目录写文件。

## 开发测试

```powershell
python -m pip install -e .
python -B -m unittest discover -s tests -v
```

测试只使用临时合成数据，不读取真实历史，不联网，也不调用 TeX 编译器。导出测试在空 `PATH` 且编译器查找/子进程调用被禁止时仍可运行；没有因缺少 TeX 而跳过的测试。真实数据验证另行只读执行，私人历史、标题及正文不随项目发布。兼容性以实际 schema 为准；这些本地持久化格式并非稳定的公共 API。

### 本次验证记录

2026-10-20，在 Windows 上使用 Python 3.12.14、3.13.13，各通过 296 项测试，零跳过。其中 179 项覆盖 TeX 生成、附件打包、局部兼容处理、结构检查和排版。其余回归覆盖六个抽象接口、创建及最后消息时间、秒/毫秒/ISO 8601 换算与缺失值、时间来源优先级、标题 39/40/41 字符边界、中文与 emoji 字符计数、缺失展示名时的原标题拼接与回退顺序、展示名更新、归档记录、消息筛选、镜像合并、只读连接、WAL、自动定位、损坏数据和命令行调用。测试命令如上；生成规则和结构测试不等同于实际运行编译器。

导出回归覆盖多行/相邻公式和长链接、复杂的两轮中文/表格/公式/代码文档、归档文档、边界损坏或危险公式片段保留原文且继续导出其余内容、颜色/方框/下划线/代码块样式、单元格里的数学与代码竖线，以及图片/附件/网页链接在原始文件删除且导出目录搬移后的相对引用。还覆盖数学命令和参数原样保留、未知命令不误报、Markdown/代码/公式各自转义、注释不吞定界符或注册引用、显示环境在不同上下文中的包裹、重复标签、跨消息前向引用、缺失引用、宽表格、深层列表/引用、告警与 CLI 成功退出、附件同名冲突、重复导出、失败回滚、原始 cwd、缺失附件、嵌入图片解码和远程资源不下载。验证只检查代码行为和生成文本结构，不声称检查了最终 PDF 的外观。

另对既有真实示例进行只读纯导出复验：4 轮、8 条可见消息，4 个附件副本，源日志 SHA-256 不变。在禁止编译器查找和子进程调用的条件下通过静态检查；一个原始文件此前已不存在，保留明确的缺失附件告警，不替换或编造文件。

另以只读方式核对本机快照：207 个任务（82 个未归档、125 个已归档），全部 ID/标题与独立数据库及索引读取并应用相同截断规则后的结果一致。其中 188 个有持久化展示名；缺少展示名的 19 个中，11 个使用 `"<TitleMissing>" + 数据库原标题` 作为截断前的标题，8 个没有可用标题字符串、只返回标记。全部任务中有 19 个标题超过 40 字符，正确返回前 40 字符加 `...`。之前在 App 侧栏确认的 23 个标题冲突样本按相同截断规则比对均匹配，未声称对 207 个任务逐一进行界面验证。

未归档、已归档各选择一个真实消息样本，分别为 4 条、2 条可见消息，与独立提取的用户/最终答复逐字一致；两个样本的创建时间、最后消息时间也与独立只读提取结果一致。源 JSONL 前后 SHA-256 不变，零告警。真实历史验证文件只保存在开发工作区，不放入此仓库或分发包。
