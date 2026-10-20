# codex_history_interface

只读访问当前用户的 Codex 历史，默认包含已归档对话。支持读取消息、模糊检索和 TeX 导出。

## 安装

Python 3.10+，在项目目录执行：

```powershell
python -m pip install -e .
```

## Quick Start

### 1. 用 Python 读取对话

```python
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface()
chat_ids = reader.getChatList()
for chat_id in chat_ids[:5]:
    print(chat_id, reader.getChatTitle(chat_id))

if chat_ids:
    for message in reader.getChatMessage(chat_ids[0]):
        print(message["role"], message["content"])
```

### 2. 按内容查找相关对话

```python
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface()
for chat_id in reader.roughMatch("C*代数 交换子", maxCnt=5):
    print(chat_id.value, reader.getChatTitle(chat_id))
```

首次检索建立 SQLite 硬盘索引，默认位于当前用户的 `~/.codex_history_interface/indexes`；重启后复用，新增或修改的对话按差分更新。可通过 `C_SearchConfig` 或 `CODEX_HISTORY_INDEX_DIR` 自定义位置，见[检索文档](docs/search.md)。

### 3. 导出 TeX

```python
from pathlib import Path
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface()
chat_ids = reader.getChatList()
if chat_ids:
    output = reader.exportToTex(
        chat_ids[0], Path.home() / "codex-exports" / "conversation.tex",
    )
    print(output)
```

输出包含标题、时间、目录、每轮提问与最终答复，并复制可用附件；包本身不编译 PDF。

> **WARNING：不要单独改名、删除或移动导出的 `asset-*` 附件。** 请将 `.tex`、附件和自行编译的 PDF 整套放在同一目录，避免破坏相对链接。

### 4. 使用 CLI

```powershell
codex-history list
codex-history title '完整对话ID'
codex-history messages '完整对话ID'
codex-history rough-match 'C*代数 交换子' --max-count 10
codex-history export-tex '完整对话ID' 'exports/conversation.tex'
```

将“完整对话ID”替换为 `list` 返回的 ID；也可用 `python -m codex_history_interface` 代替 `codex-history`。

## 详细文档

| 内容 | 文档 |
| --- | --- |
| 接口、标题、时间、消息筛选与 CLI | [接口与命令行](docs/api.md) |
| 模糊匹配、差分索引、性能与独立复用 | [检索与索引](docs/search.md) |
| 文档样式、公式、附件和生成规则 | [TeX 导出](docs/tex-export.md) |
| 数据目录、数据库定位与只读边界 | [数据与只读访问](docs/storage.md) |
| 类设计、分层导出与依赖注入 | [架构与复用](docs/architecture.md) |
| 开发安装、回归测试与性能测试 | [开发验证](docs/development.md) |
