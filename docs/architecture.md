# 架构与复用

[返回 Quick Start](../README.md) · [接口与命令行](api.md) · [TeX 导出](tex-export.md)

公开抽象类使用 `I_` 前缀，具体实现使用 `C_` 前缀，数据类型使用 `D_` 前缀；主要类型位于各自的同名文件中。`Abstract` 定义契约，`ConcreteLocal` 提供本地实现，每一级子包通过 `__init__.py` 和显式 `__all__` 导出所属类型。

```text
codex_history_interface/
├── Abstract/
│   ├── I_CodexInterface.py
│   ├── History/
│   │   ├── I_HistoryRepository.py
│   │   ├── I_SearchConfig.py
│   │   ├── D_ChatId.py
│   │   ├── D_ChatMessage.py
│   │   └── D_ChatExport.py
│   └── Export/
│       └── I_TexExporter.py
├── ConcreteLocal/
│   ├── C_CodexInterface.py
│   ├── C_CommandLine.py
│   ├── History/
│   │   ├── C_HistoryRepository.py
│   │   ├── C_HistoryStore.py
│   │   ├── C_RoughMatchIndex.py
│   │   ├── C_PersistentRoughMatchIndex.py
│   │   ├── C_IndexFileLock.py
│   │   └── C_SearchConfig.py
│   └── Export/
│       ├── C_TexExporter.py
│       ├── C_TexDocumentRenderer.py
│       ├── C_MarkdownLatexRenderer.py
│       └── C_AssetBundle.py
├── client.py
└── interface.py
```

| 类型 | 职责 |
| --- | --- |
| `I_CodexInterface` / `C_CodexInterface` | 提供八方法外观，含时间区间查询及模糊检索；通过组合委托给历史仓库和导出器，不负责数据库解析或 Markdown 排版 |
| `I_HistoryRepository` / `C_HistoryRepository` | 只读查询 ID、展示标题、可见消息与时间，构造带附件来源和保护路径的 `D_ChatExport` 快照 |
| `C_HistoryStore` | 定位数据目录、只读访问 SQLite、索引、活动/归档 JSONL 与投影，并汇集诊断 |
| `C_RoughMatchIndex` | 可独立复用的内存倒排索引，增量维护标题/正文词频，以字符 n-gram 和字段加权 BM25 返回相关性最高的 ID；只依赖 Python 标准库 |
| `C_PersistentRoughMatchIndex` | 默认历史检索使用的 SQLite 子类；倒排项与指纹原子提交，唯一标识/代次检测跨进程变更，只加载命中词项；恢复与事务共用跨进程锁 |
| `C_IndexFileLock` | 内部锁适配器；以稳定的旁路文件承载 Windows/POSIX 操作系统锁，覆盖打开、恢复与事务全过程；超时明确报错，不删除锁文件 |
| `I_SearchConfig` / `C_SearchConfig` | 声明配置字段、提供实例默认值；通过传统 `get...()` 方法读取目录、版本、超时、分词模式和 CLI 默认数量，不使用 `@property` |
| `C_CommandLine` | 构建命令解析器，执行读取、持久化检索及导出；静态 `main()` 作为 console script 与模块入口，不定义模块级业务函数 |
| `D_ChatId` | 不可变的对话 ID 值类型；`roughMatch()` 返回该类型，兼容既有字符串 ID 接口及 JSON 序列化，通过 `.value` 读取原始字符串 |
| `I_TexExporter` / `C_TexExporter` | 从仓库取得快照，验证目标路径，管理附件暂存、发布和失败回滚；不执行编译 |
| `C_TexDocumentRenderer` | 组装标题、秒级本机时间、中文目录和按轮次分节的完整 TeX 文档；每份文档创建独立 Markdown 渲染状态 |
| `C_MarkdownLatexRenderer` | 将 Markdown 结构与公式转换为 TeX，管理该文档内的公式编号和引用 |
| `C_AssetBundle` | 解析、暂存和复制本地附件，生成相对引用；网页链接不下载 |

可以从顶层、分层父包或同名类型模块导入同一类，例如：

```python
from codex_history_interface.Abstract import I_CodexInterface, I_HistoryRepository, I_TexExporter
from codex_history_interface.ConcreteLocal import C_CodexInterface, C_HistoryRepository, C_TexExporter
from codex_history_interface.ConcreteLocal.Export.C_TexDocumentRenderer import C_TexDocumentRenderer

repository: I_HistoryRepository = C_HistoryRepository()
exporter: I_TexExporter = C_TexExporter(document_renderer=C_TexDocumentRenderer())
reader: I_CodexInterface = C_CodexInterface(repository=repository, tex_exporter=exporter)

chat_ids = reader.getChatList()
if chat_ids:
    reader.exportToTex(chat_ids[0], r"C:\exports\conversation.tex")
```

仓库与导出器也可独立使用，不必创建外观对象：

```python
from codex_history_interface.ConcreteLocal.History import C_HistoryRepository
from codex_history_interface.ConcreteLocal.Export import C_MarkdownLatexRenderer, C_TexDocumentRenderer, C_TexExporter

repository = C_HistoryRepository()
chat_ids = repository.getChatList()
if chat_ids:
    snapshot = repository.getChatExport(chat_ids[0])
    source = C_TexDocumentRenderer().render(
        snapshot.title, snapshot.messages, snapshot.created_at, snapshot.modified_at,
        message_timestamps=snapshot.message_timestamps,
    )
    output = C_TexExporter().exportToTex(repository, chat_ids[0], r"C:\exports\conversation.tex")

fragment = C_MarkdownLatexRenderer().render("**强调**与公式 $x^2$。")
```

`C_TexDocumentRenderer.render()` 只返回 TeX 字符串，不读取历史、不创建文件；需要复制附件时使用 `C_TexExporter.exportToTex()`。`C_TexExporter.exportDocument()` 也能直接接收调用方提供的标题、消息、时间与 `bases`/`protected` 路径，按相同的暂存、相对引用和覆盖规则发布文件，不要求有 Codex 数据库。`protected` 中的文件或目录不能作为输出或附件来源。

扩展时实现 `I_HistoryRepository` 可替换数据来源，实现 `I_TexExporter` 可替换整个导出策略；也可以派生 `C_TexDocumentRenderer`、通过 `renderer_type` 注入 `C_MarkdownLatexRenderer` 子类，或通过 `asset_bundle_type` 注入 `C_AssetBundle` 子类。各对象独立持有状态，文档之间不共享公式/消息计数。新增检索、配置和 CLI 组件将逻辑放在类中，不定义模块级变量、常量或函数；配置通过实例字段和普通 getter 提供。已有历史/导出兼容辅助函数及公开属性保持兼容。自定义历史仓库也需要实现抽象方法 `getChatListInTimeRange()` 和 `roughMatch()`，后者必须返回 `list[D_ChatId]`，保证注入后仍提供相同的查询语义与返回类型。

兼容原来的 `from codex_history_interface import C_CodexInterface`、`client.C_CodexInterface`、`interface.I_CodexInterface` / `D_ChatMessage`，这些入口导出相同的类对象。构造函数支持 `codex_home`、`databases`，以及关键字注入参数 `repository`、`tex_exporter`、`search_config`；注入仓库时不能同时指定本地数据路径或搜索配置。`search_config` 接受 `I_SearchConfig`，默认 `C_SearchConfig()`；索引位置用 `reader.getSearchIndexPath()` 查询。

`D_ChatExport.message_timestamps` 按位置与 `messages` 一一对应，保存 Unix 秒级时间；`None` 表示该条消息没有时间，空元组表示快照未提供时间信息。独立调用渲染器或 `exportDocument()` 时，通过同名关键字参数传入，以显示真实提问时间；`getChatMessage()` 的字典结构仍只有 `role` 和 `content`。
