# 接口与命令行

[返回 Quick Start](../README.md) · [模糊检索](search.md) · [TeX 导出](tex-export.md)

## Python 接口

| 方法 | 返回值 | 说明 |
| --- | --- | --- |
| `getChatList()` | `list[str]` | 全部本地对话的完整 ID，默认同时包含未归档、已归档和状态不明的记录；按可用更新时间倒序，无数量上限 |
| `getChatListInTimeRange(timestamp1, timestamp2)` | `list[str]` | 返回在 Unix 秒级闭区间内创建或有过修改记录的完整 ID；包含归档对话、去重，顺序与 `getChatList()` 一致 |
| `roughMatch(content, maxCnt)` | `list[D_ChatId]` | 对标题及用户/最终答复做非 AI 模糊检索，按相关性降序返回最多 `maxCnt` 个对话 ID 对象；包含归档对话 |
| `getChatTitle(chat_id)` | `str` | 读取持久化展示名；缺少展示名时使用 `"<TitleMissing>" + 已有标题字符串`，无可用标题时仅用标记；最终标题超过 40 字符时取前 40 字符再加 `...` |
| `getChatMessage(chat_id)` | `list[D_ChatMessage]` | 按日志顺序排列的用户消息和 Codex 最终答复 |
| `getLastMessageTimeStamp(chat_id)` | `float \| None` | 用户消息及最终答复中最新的可用 Unix 秒级时间戳；没有可用消息时间时返回 `None` |
| `getCreateTimeStamp(chat_id)` | `float \| None` | 对话创建时间的 Unix 秒级时间戳；没有可用创建时间时返回 `None` |
| `exportToTex(chat_id, output_path, *, overwrite=False)` | `pathlib.Path` | 导出标题、本机时区副标题和按轮次分节的用户/最终答复，返回目标 `.tex` 文件的绝对路径；个别内容的排版兼容问题通过告警报告，不执行编译 |

`chat_id` 必须是上述列表/检索方法返回的完整 ID，不接受标题或 ID 前缀。`D_ChatMessage` 是普通字典的类型定义，实际返回值可直接序列化：

```python
[
    {"role": "user", "content": "用户的问题"},
    {"role": "assistant", "content": "Codex 的最终回答"},
]
```

不存在的 ID 抛出 `ChatNotFoundError`；数据目录或正文缺失抛出 `HistoryUnavailableError`。有索引但没有正文的对话仍会出现在列表中，不能把缺失正文伪装成完整聊天记录。`TITLE_MISSING` 常量的值就是 `"<TitleMissing>"`。

## 展示名与 Codex 界面

展示名依次读取 **非空的 `threads.name` → `session_index.jsonl` 最新记录的 `thread_name`**。读取原文，不重新概括、不改变空格或标点；返回时统一应用下述长度规则，归档记录采用相同规则。

同一 ID 的重复索引记录，若两个更新时间都能解析，则取较新者；时间相同或不可比较时取文件中最后一条。数据库按数值版本由高到低读取，同 ID 不用旧库覆盖新库。

数据库 `threads.title` 可能只是旧标题或初始提示词，因此**绝不直接把它当作当前界面标题**，也不使用摘要、全局描述缓存或第一句话冒充展示名。缺少上述展示名来源时，依次选取 **`session_index.jsonl` 最新记录的 `title` → `threads.title`** 中第一个非空、非纯空白的字符串，并拼接为 `"<TitleMissing>" + 该字符串`，例如 `"<TitleMissing>原有标题"`。前缀与原标题直接拼接，不插入空格或其他分隔符；没有可用标题字符串时只使用 `"<TitleMissing>"`。该前缀明确表示后面的旧标题不是已确认的界面展示名；有可靠展示名时不添加前缀。

**标题长度规则**：对最终标题字符串统一按 Python Unicode 码点计数，可能存在的 `"<TitleMissing>"` 前缀也计入长度。不超过 40 字符时原样返回；超过 40 字符时返回前 40 字符加三个英文句点 `...`，结果总长为 43 字符。此规则同时适用于 Python 接口和命令行，只影响返回值，不修改本地历史记录，也不截断消息正文。

每次调用 `getChatTitle()` 都重新读取展示名元数据，下一次调用即可读取已持久化的重命名。尚未写入本机存储的界面变化无法从磁盘获知；此库不声称可以读取 App 的未持久化内存状态。

## 时间戳

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

## 时间区间查询

```python
from datetime import datetime
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface()
timestamp1 = datetime(2026, 10, 19, 0, 0, 0).timestamp()
timestamp2 = datetime(2026, 10, 20, 18, 0, 0).timestamp()
for chat_id in reader.getChatListInTimeRange(timestamp1, timestamp2):
    print(chat_id, reader.getChatTitle(chat_id))
```

- 输入是 Unix 秒级数值（`int` 或 `float`），示例中的无时区 `datetime` 按本机默认时区转换为时间戳；不手动增加或减去时差，不将输入猜测为毫秒。原始存储中的秒、毫秒和 ISO 8601 时间则按现有规则统一到 Unix 秒。
- 区间包含两个端点：`timestamp1 <= 时间 <= timestamp2`；两者相等可查询某一精确时刻，小数秒不会被取整。起点大于终点、NaN、无穷大或无法转换为有限浮点数的整数抛出 `ValueError`；字符串、布尔值、`datetime` 等非数值参数抛出 `TypeError`。
- 查询 SQLite 会话的创建、更新、归档时间，完整 `session_index.jsonl`（包括同一 ID 的旧记录），活动和归档 JSONL 的事件时间，以及 SQLite 历史投影的记录时间、轮次开始/结束时间和条目时间；同一时间字段有有效毫秒值时优先使用毫秒值。即使最后修改时间已经超出区间，只要中间某次修改仍有区间内的时间记录，也会命中。
- 修改记录包括已保存的重命名、归档及内部运行事件的时间。这只用于筛选对话 ID，不改变 `getChatMessage()` 和 TeX 导出的用户/最终答复筛选规则，也不返回思考正文或原始 JSON。
- 默认包含未归档、已归档和状态不明的对话。每个 ID 只返回一次，按 `getChatList()` 的顺序排列；没有匹配记录时返回 `[]`。每次查询只刷新一次历史清单，所有访问保持只读。
- 仅根据本地仍保存的实际时间记录匹配；不把创建至最后更新之间的整个跨度当成持续修改，不使用文件修改时间，不猜测缺失的时间。若中间修改记录已被清理，仅凭当前 `updated_at` 无法还原这段历史。不可读或损坏来源仍通过 `HistoryReadWarning` 和 `diagnostics` 报告。

## 消息筛选

- 保留用户实际发言、Codex 的最终答复；旧版没有 `phase` 标记的普通助手消息也保留。
- 排除分析/推理、`commentary` 进度、系统/开发者提示词、AGENTS 指令、注入的环境上下文、工具调用及工具原始结果、压缩事件等内部记录。
- 合并 response/event/task-complete 的镜像和重复日志副本；同一来源中实际重复发送的消息仍保留，不按相似度去重。
- 原始 JSON 日志和数据库行不混入返回值；用户或助手真正说出的 JSON、代码、数学公式及换行保持原文。`getChatMessage()` 不转换正文格式；`exportToTex()` 单独负责文档生成与排版兼容处理。
- 图片、音频和附件仅返回简短占位文本，不返回二进制或 Base64，也不下载附件。
- 优先读取 JSONL；没有可见日志消息时才回退到 SQLite 历史投影，并发出覆盖限制警告。不会把较旧投影拼到已有日志回答后面。投影的 `final_agent_item_id` 可用时，用它排除同轮次非最终答复。

未知格式、坏行、文件变化、不可读来源等通过 `HistoryReadWarning` 报告，详细诊断可从具体实现的 `reader.diagnostics` 读取。不要把有警告的结果当成无遗漏的完整历史。日志每次按开始读取时的字节边界提取，不声称提供跨文件和数据库的原子快照。

## 命令行

列表、标题、消息、模糊检索和 TeX 导出均有命令行入口，不需要生成内联脚本；两个数值时间戳方法及时间区间查询通过 Python 接口调用：

```powershell
codex-history list
codex-history title '完整对话ID'
codex-history messages '完整对话ID'
codex-history rough-match 'C*代数 交换子' --max-count 10
codex-history search 'Qt 截骨规划' -n 5
codex-history export-tex '完整对话ID' 'C:\exports\conversation.tex'
```

也可使用 `python -m codex_history_interface`。全局 `--home`、可重复的 `--db` 以及 `--index-dir` 参数放在子命令之前；分别指定历史数据、额外数据库及持久化检索目录。`search` 是 `rough-match` 的别名；数量参数 `--max-count` / `--max-cnt` / `-n` 默认为 10。结果为按相关性排列的完整 ID JSON 数组，不是标题。Python 侧通过 `C_SearchConfig` / `I_SearchConfig` 配置，`reader.getSearchIndexPath()` 返回索引文件路径，详见[检索文档](search.md)。

导出子命令只接受 `--overwrite` 可选开关，不接受 `--compiler` 或 `--timeout`。结果为 UTF-8 JSON 输出到标准输出（导出成功时为目标绝对路径）；读取、缓存及个别内容的警告输出到标准错误，成功操作的退出码仍为 `0`。操作错误输出 JSON 到标准错误且退出码为 `2`；参数解析错误由 argparse 报告。程序本身不向历史目录写文件。CLI 实现位于 `C_CommandLine`，可直接调用 `C_CommandLine.main(argv)`；`__main__` 仅转发入口，不定义全局业务函数。
