# codex_history_interface

读取当前用户本地 Codex 历史的 Python 库。采用面向对象设计：抽象类 `I_CodexInterface` 定义三个方法，`C_CodexInterface` 提供只读实现；不依赖 Skill、Codex App 自动化、OpenAI API 或第三方运行时依赖。

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

### 三个接口

| 方法 | 返回值 | 说明 |
| --- | --- | --- |
| `getChatList()` | `list[str]` | 全部本地对话的完整 ID，默认同时包含未归档、已归档和状态不明的记录；按可用更新时间倒序，无数量上限 |
| `getChatTitle(chat_id)` | `str` | 精确的持久化展示名；缺少展示名时返回 `"<TitleMissing>" + 已有标题字符串`，无可用标题时仅返回标记 |
| `getChatMessage(chat_id)` | `list[ChatMessage]` | 按日志顺序排列的用户消息和 Codex 最终答复 |

`chat_id` 必须是 `getChatList()` 返回的完整 ID，不接受标题或 ID 前缀。`ChatMessage` 是普通字典的类型定义，实际返回值可直接序列化：

```python
[
    {"role": "user", "content": "用户的问题"},
    {"role": "assistant", "content": "Codex 的最终回答"},
]
```

不存在的 ID 抛出 `ChatNotFoundError`；数据目录或正文缺失抛出 `HistoryUnavailableError`。有索引但没有正文的对话仍会出现在列表中，不能把缺失正文伪装成完整聊天记录。`TITLE_MISSING` 常量的值就是 `"<TitleMissing>"`。

### 展示名与 Codex 界面

展示名依次读取 **非空的 `threads.name` → `session_index.jsonl` 最新记录的 `thread_name`**。返回原文，不截断、不重新概括、不改变空格或标点；归档记录采用相同规则。

同一 ID 的重复索引记录，若两个更新时间都能解析，则取较新者；时间相同或不可比较时取文件中最后一条。数据库按数值版本由高到低读取，同 ID 不用旧库覆盖新库。

数据库 `threads.title` 可能只是旧标题或初始提示词，因此**绝不直接把它当作当前界面标题**，也不使用摘要、全局描述缓存或第一句话冒充展示名。缺少上述展示名来源时，依次选取 **`session_index.jsonl` 最新记录的 `title` → `threads.title`** 中第一个非空、非纯空白的字符串，并返回 `"<TitleMissing>" + 该字符串`，例如 `"<TitleMissing>原有标题"`。前缀与原标题直接拼接，不插入空格或其他分隔符，原标题保持原样；没有可用标题字符串时只返回 `"<TitleMissing>"`。该前缀明确表示后面的旧标题不是已确认的界面展示名；有可靠展示名时不添加前缀。

每次调用 `getChatTitle()` 都重新读取展示名元数据，下一次调用即可读取已持久化的重命名。尚未写入本机存储的界面变化无法从磁盘获知；此库不声称可以读取 App 的未持久化内存状态。

### 消息筛选

- 保留用户实际发言、Codex 的最终答复；旧版没有 `phase` 标记的普通助手消息也保留。
- 排除分析/推理、`commentary` 进度、系统/开发者提示词、AGENTS 指令、注入的环境上下文、工具调用及工具原始结果、压缩事件等内部记录。
- 合并 response/event/task-complete 的镜像和重复日志副本；同一来源中实际重复发送的消息仍保留，不按相似度去重。
- 原始 JSON 日志和数据库行不混入返回值；用户或助手真正说出的 JSON、代码、数学公式及换行保持原文。本接口不做文档排版，不执行 TeX 编译或语法检查。
- 图片、音频和附件仅返回简短占位文本，不返回二进制或 Base64，也不下载附件。
- 优先读取 JSONL；没有可见日志消息时才回退到 SQLite 历史投影，并发出覆盖限制警告。不会把较旧投影拼到已有日志回答后面。投影的 `final_agent_item_id` 可用时，用它排除同轮次非最终答复。

未知格式、坏行、文件变化、不可读来源等通过 `HistoryReadWarning` 报告，详细诊断可从具体实现的 `reader.diagnostics` 读取。不要把有警告的结果当成无遗漏的完整历史。日志每次按开始读取时的字节边界提取，不声称提供跨文件和数据库的原子快照。

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

三个命令分别对应三个方法，不需要生成内联脚本：

```powershell
codex-history list
codex-history title '完整对话ID'
codex-history messages '完整对话ID'
```

也可使用 `python -m codex_history_interface`。全局 `--home`、可重复的 `--db` 参数放在子命令之前。结果为 UTF-8 JSON 输出到标准输出，错误输出到标准错误且退出码为 `2`；程序本身不向历史目录写文件。

## 开发测试

```powershell
python -m pip install -e .
python -B -m unittest discover -s tests -v
```

测试只使用临时合成数据，不读取真实历史，不联网。真实数据验证另行只读执行，私人历史、标题及正文不随项目发布。兼容性以实际 schema 为准；这些本地持久化格式并非稳定的公共 API。

### 本次验证记录

2026-10-20，在 Windows 上使用 Python 3.12.14、3.13.13，各通过 81 项隔离测试；覆盖接口契约、缺失展示名时的原标题拼接与回退顺序、空标题与非字符串处理、展示名更新、归档记录、消息筛选、镜像合并、只读连接、WAL、自动定位、损坏数据和命令行调用。安装构建后的 wheel 再次运行同一套测试，确认不依赖源码目录或旧 Skill。

另以只读方式核对本机快照：207 个任务（82 个未归档、125 个已归档），全部 ID/标题与独立数据库及索引读取结果一致。其中 188 个有持久化展示名；缺少展示名的 19 个中，11 个返回 `"<TitleMissing>" + 数据库原标题`，8 个没有可用标题字符串、只返回标记。之前在 App 侧栏确认的 23 个标题冲突样本均匹配正确展示名，未声称对 207 个任务逐一进行界面验证。

未归档、已归档各选择一个真实消息样本，分别为 4 条、2 条可见消息，与独立提取的用户/最终答复逐字一致，源 JSONL 前后 SHA-256 不变，零告警。真实历史验证文件只保存在开发工作区，不放入此仓库或分发包。
