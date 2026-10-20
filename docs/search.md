# 模糊检索与差分索引

[返回 Quick Start](../README.md) · [接口与命令行](api.md) · [开发验证](development.md)

```python
from codex_history_interface import C_CodexInterface, D_ChatId

reader = C_CodexInterface()
chat_ids: list[D_ChatId] = reader.roughMatch("Qt 截骨规划界面", maxCnt=5)
for chat_id in chat_ids:
    print(chat_id.value, reader.getChatTitle(chat_id))

other_ids = reader.roughMatch("markdwon table", maxCnt=3)
```

## 返回值与检索范围

`content` 是待查找的字符串，`maxCnt` 是最多返回多少项。返回 `list[D_ChatId]`，每项是实际的不可变 ID 对象，代表完整对话 ID，不是标题、原始 JSON 或人工整理的摘要。结果按相关性降序排列，相同分数按 ID 字典序排序；不会重复 ID，也不会用零相关记录补足数量。空白、无可检索文字、`maxCnt=0` 或无命中返回 `[]`。非字符串 `content`、非整数或布尔值 `maxCnt` 抛出 `TypeError`，负数 `maxCnt` 抛出 `ValueError`。

`D_ChatId` 是 `str` 的不可变子类，而不是类型别名；`type(chat_ids[0]) is D_ChatId`。使用 `chat_id.value` 或 `str(chat_id)` 可取得原始字符串，也能直接传给 `getChatTitle()`、`getChatMessage()`、两个时间戳方法及 `exportToTex()`。保留字符串相等、哈希和 JSON 序列化行为，原来按字符串 ID 使用搜索结果的代码仍然可用。构造 `D_ChatId(value)` 时拒绝非字符串或空白 ID，不截断、去空格或限制为 UUID。`getChatList()`、`getChatListInTimeRange()` 原有的 `list[str]` 返回类型不变。

默认查询所有未归档、已归档和状态不明的本地对话。标题使用与 `getChatTitle()` 相同的来源优先级，但索引完整存储标题，不受 40 字符的展示截断影响，也不索引人为添加的 `<TitleMissing>` 标记。正文只来自 `getChatMessage()` 同样可见性规则下的用户消息和最终答复，不索引思考、系统提示词、工具输出或原始 JSON 键；仅有标题、没有正文的对话也能命中。JSONL 优先、SQLite 投影回退与覆盖限制警告保持不变。

## 算法

整个检索过程只使用 Python 标准库，不调用模型、嵌入服务、网络或外部检索程序。

- Unicode NFKC 标准化与 `casefold()` 统一全角形式及大小写；保留数字和代码标识符中的文字部分。
- 中日韩文字使用单字和相邻双字特征；英文等文字使用完整词及字符三元组。连续中文、部分短语和有共同字符三元组的英文拼写差异均可匹配，不需要中文分词模型；不声称能识别没有文字重叠的同义改写。
- 倒排表只查命中查询特征的记录，相关性使用字段加权 BM25：稀有特征权重更高，词频有饱和、正文长度有归一化，避免很长的对话靠重复用词占据榜首。标题字段权重为正文的 4 倍，查询完整短语出现在标题时再提升分数；单字/模糊三元组通常低于完整词/双字的权重。
- 仅对候选记录用有界 Top-K 选择，不排序全部历史，也不逐条对整段历史调用编辑距离或 `SequenceMatcher`。

## 持久化索引与目录

首次有效查询建立 SQLite 硬盘倒排索引；以后创建新实例、重启 Python 或通过另一个 CLI 进程查询，均复用同一份索引。仅加载对话标题、字段长度和差分元数据，按查询特征从磁盘读取对应倒排项，不将整个倒排表装入内存。

默认目录为 `Path.home() / ".codex_history_interface" / "indexes"`。家目录由 Python 和当前系统环境定位，可以位于任意盘符或路径；没有用户名、固定 `C:\Users` 根目录或指定对话文件名。目录选择顺序为：

1. `C_SearchConfig(index_directory=...)`，CLI 对应全局 `--index-dir`。
2. 非空环境变量 `CODEX_HISTORY_INDEX_DIR`。
3. 当前用户家目录下的 `.codex_history_interface/indexes`。

```python
from pathlib import Path
from codex_history_interface import C_CodexInterface, C_SearchConfig, I_SearchConfig

config: I_SearchConfig = C_SearchConfig(Path.home() / "my-history-indexes")
reader = C_CodexInterface(search_config=config)
print(config.getIndexDirectory())
print(reader.getSearchIndexPath())
chat_ids = reader.roughMatch("交换子", 10)
```

`I_SearchConfig` 声明配置字段及普通函数 getter，`C_SearchConfig` 在实例字段中提供默认值；没有 `@property`。其他 getter 包括 `getSchemaVersion()`、`getSqliteTimeout()`、`getDefaultMaxCount()`、`getCjkRanges()`、`getTokenPattern()`、`getCjkPattern()`。配置实例在 reader 构造完成后应保持不变；自定义规则可派生配置类，再创建新 reader。

索引文件名包含格式版本及数据源命名空间的 SHA-256；命名空间由规范化数据目录、额外数据库路径集合及分词规则确定。不同数据源互不混用，同一来源的多个 reader/进程可以共享。`getSearchIndexPath()` 返回具体文件路径，但不会立即创建文件；空查询和 `maxCnt=0` 也不会创建索引。

缓存保存完整标题、词项、词频、字段长度、来源路径和差分指纹，不保存整段消息、系统提示或思考正文。词项仍可能包含敏感内容，应将索引目录视为私人历史数据，不共享或提交到 Git。索引必须位于只读 Codex 数据目录之外；程序仅写入自己管理的缓存，不修改原始历史。

## 差分更新与复杂度

每次查询以文件列表、大小、纳秒级修改/状态时间及文件标识检查变更，覆盖 SQLite 主库、WAL/回滚日志、会话索引、活动/归档日志及已发现的外部日志。未变更时只做文件元数据检查，不重读 JSONL 或 SQLite 消息正文。新增对话只建立新对话的索引；已有 JSONL 追加或改写，只重新解析、分词并替换该对话的倒排项，不清空整份索引。单纯修改标题只重建标题特征，单纯修改时间不会重新分词正文。

SQLite 回退正文使用按对话 ID 计算的 BLAKE2 内容指纹，覆盖相关消息行、顺序字段和 `thread_turns.final_agent_item_id`。共享投影库发生变化时，需要只读检查依赖该库的各对话指纹，但仅重新解析和分词指纹变化的对话；新增行、原地修改或删除一项不会重建其他对话。检查指纹不解析 JSON、不调用可见消息合并或分词；这仍可能读取该库中回退对话的原始行，不意味着数据库发生变化后完全不读取无关行。库文件指纹不变时复用缓存的对话指纹；已有可见 JSONL 的对话不计算未使用的投影正文指纹。读取期间来源发生变化时，下一次查询重试相关对话，不把旧正文永久绑定到新指纹。索引仍以对话为增量单位，并非仅对新增字节分词；变更时的元数据刷新和日志头发现也不是全文索引重建。

令 `T` 为首次参与索引的文字量、`F` 为历史来源文件数、`D` 为对话数、`V` 为索引词项数、`U` 为不同查询特征数、`P` 为查询特征对应的倒排项数、`M` 为候选对话数、`K=maxCnt`：首次分词为 `O(T)`，另有 SQLite 倒排写入成本，磁盘空间与不同“特征—对话”组合数成正比。缓存后的查询约为 `O(F + D + |content| + U log U + U log V + P + M log K)`，其中 `F + D` 包含来源检查及轻量元数据处理；不随全部消息正文长度增长。查询特征排序保证评分稳定；`K=1` 时选择为线性，`K>=M` 时最多对候选排序。常见词可能命中大多数对话，但仍不扫描消息正文。

SQLite 事务将单轮差分倒排项和来源指纹一起提交，失败时一起回滚；写锁协调不同进程，未变更的查询不会改写索引内容。另一个进程提交后，现有 reader 会检测缓存代次并重新加载轻量元数据。来源读取警告会随索引保留，并在新 reader 中通过 `HistoryReadWarning` 和 `diagnostics` 报告。

损坏、校验失败或版本不兼容的缓存会保留为同目录的 `.invalid-随机标识` 备份，告警后重新建立；磁盘不可写、数据库锁超时等操作错误直接报告，不伪装成空结果或静默退回全量内存检索。文件指纹不是跨数据库/日志的原子快照，也不能发现刻意保留全部指纹的原地改写。需要强制重建时，先停止访问该缓存的进程，再删除 `getSearchIndexPath()` 返回的专属索引文件；下次检索重建。不要删除或修改 Codex 原始数据库及其 WAL/SHM 文件。

## CLI 检索

```powershell
codex-history rough-match 'C*代数 交换子' --max-count 10
codex-history search 'Qt 截骨规划' -n 5
codex-history --home 'D:\CodexData' --index-dir 'E:\HistoryIndexes' search 'Markdown 表格'
```

`search` 是 `rough-match` 的别名；`--max-count` 也可写成 `--max-cnt` 或 `-n`，默认返回最多 10 个 ID。标准输出是 UTF-8 JSON ID 数组，可将其中的完整 ID 传给 `title`、`messages` 或 `export-tex`。全局目录参数放在子命令之前。

## 独立复用

`C_RoughMatchIndex` 是可独立复用的纯内存算法核心；默认历史 reader 使用它的 SQLite 子类 `C_PersistentRoughMatchIndex`，不是内存缓存模式。无需 Codex 数据库时也可自行选择持久化核心：

```python
from codex_history_interface import C_PersistentRoughMatchIndex

index = C_PersistentRoughMatchIndex("my-documents")
index.updateDocument("chat-1", "截骨规划", ["用户的问题", "最终答复"])
chat_ids = index.roughMatch("截骨", 5)
print(index.getIndexPath())
```

纯内存版本示例：

```python
from codex_history_interface import C_RoughMatchIndex

index = C_RoughMatchIndex()
index.updateDocument("chat-1", "截骨规划", ["用户的问题", "最终答复"])
index.updateDocument("chat-2", "Markdown 表格", ["导出数学公式"])
chat_ids = index.roughMatch("截骨", 5)
index.updateDocument("chat-1", "新的标题")
index.removeDocument("chat-2")
```

`contents=None`（省略第三个参数）保留已索引正文，传 `[]` 明确清空正文。纯检索核心的合成数据性能测试可通过以下命令复现；它同时测量稀有查询、常见查询、拼写差异和无命中查询，不包含历史 JSON 解析及文件元数据检查成本：

```powershell
python benchmarks/benchmark_rough_match.py --documents 10000 --repeats 25
```
