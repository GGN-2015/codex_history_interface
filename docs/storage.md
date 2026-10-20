# 数据定位与只读访问

[返回 Quick Start](../README.md) · [接口与命令行](api.md) · [模糊检索](search.md)

## 自动定位

默认无需传入路径，定位顺序为：

1. 构造函数的 `codex_home`。
2. 当前进程的非空 `CODEX_HOME`。
3. 当前用户的 `~/.codex`，并兼容当前环境的 `USERPROFILE`/`HOME`。

`~` 通过 `Path.home()` 动态定位，不拼接用户名或固定的 `C:\Users`、`/home`、`/Users` 模板。用户将家目录迁移到其他盘符或目录时同样适用；非标准 Codex 目录用 `codex_home` 或 `CODEX_HOME` 显式指定。WSL、SSH、容器与宿主系统的数据目录是独立的，应在对应环境运行或明确传入路径。

```python
from codex_history_interface import C_CodexInterface

reader = C_CodexInterface(codex_home=r"D:\CodexData")
reader = C_CodexInterface(
    codex_home=r"D:\CodexData",
    databases=[r"E:\CodexSqlite\state_5.sqlite"],
)
```

动态识别 `state*.sqlite`、`state*.db`、`thread_history*.sqlite`，也检查数据根目录的 `sqlite` 子目录；不硬编码数据库版本。非标准数据库位置可以用 `databases` 指定。递归合并 `sessions/**/*.jsonl`、`archived_sessions/**/*.jsonl`、标题索引和历史投影；陈旧的 `rollout_path` 不会阻止从实际目录定位已归档正文。

## 只读边界

所有历史数据库使用 SQLite `mode=ro` 和 `PRAGMA query_only=ON`，历史 JSONL 和 Codex 标题索引仅以二进制只读模式打开。没有归档、重命名、修复、迁移、删除、checkpoint 或写回历史的接口；不会访问 `auth.json`、令牌或其他与历史无关的秘密。

程序管理的检索缓存与原始历史分离，默认写入当前用户的 `~/.codex_history_interface/indexes`，也可通过 `C_SearchConfig`、`CODEX_HISTORY_INDEX_DIR` 或 CLI `--index-dir` 指定任意可写位置。该目录不能位于 Codex 数据目录内，也不能覆盖额外指定的历史数据库。TeX 导出也只能写到历史目录之外。索引路径、隐私和差分机制见[检索文档](search.md)。

SQLite 正常读取 WAL 可能参与锁和共享内存协调；“只读”指不修改历史内容，不等同于保证整个文件系统没有锁/共享内存活动。不可删除 WAL、SHM 或锁文件来解决读取错误。
