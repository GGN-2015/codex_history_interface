import argparse
import json
import sys

from ..Abstract.History.I_SearchConfig import I_SearchConfig
from ..errors import CodexHistoryError
from .C_CodexInterface import C_CodexInterface
from .History.C_SearchConfig import C_SearchConfig


class C_CommandLine:
    def __init__(self, config: I_SearchConfig | None = None) -> None:
        self._config = config if config is not None else C_SearchConfig()

    def getParser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="Read-only access to local Codex conversations")
        parser.add_argument("--home", help="Codex data directory; overrides CODEX_HOME")
        parser.add_argument("--db", action="append", default=[], help="An additional SQLite database")
        parser.add_argument("--index-dir", help="Search index directory; overrides CODEX_HISTORY_INDEX_DIR")
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("list", help="Print all conversation IDs, including archived conversations")
        for name in ("title", "messages"):
            command = commands.add_parser(name)
            command.add_argument("chat_id", help="Exact conversation ID from the list command")
        search = commands.add_parser("rough-match", aliases=["search"], help="Find conversation IDs by title and visible messages")
        search.add_argument("content", help="Text to fuzzy-match against all conversations")
        search.add_argument("--max-count", "--max-cnt", "-n", type=int, default=self._config.getDefaultMaxCount(), help="Maximum result count (default: %(default)s)")
        export = commands.add_parser("export-tex", help="Export visible rounds and local assets to .tex without compiling")
        export.add_argument("chat_id", help="Exact conversation ID from the list command")
        export.add_argument("output_path", help="Destination .tex file outside the Codex data directory")
        export.add_argument("--overwrite", action="store_true", help="Replace an existing destination after preparing the document and assets")
        return parser

    def run(self, argv: list[str] | None = None) -> int:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        arguments = self.getParser().parse_args(argv)
        try:
            config = C_SearchConfig(arguments.index_dir) if arguments.index_dir is not None else self._config
            reader = C_CodexInterface(arguments.home, databases=arguments.db, search_config=config)
            if arguments.command == "list":
                result = reader.getChatList()
            elif arguments.command in ("rough-match", "search"):
                result = reader.roughMatch(arguments.content, arguments.max_count)
            elif arguments.command == "title":
                result = reader.getChatTitle(arguments.chat_id)
            elif arguments.command == "export-tex":
                result = str(reader.exportToTex(arguments.chat_id, arguments.output_path, overwrite=arguments.overwrite))
            else:
                result = reader.getChatMessage(arguments.chat_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (CodexHistoryError, OSError, ValueError) as error:
            print(json.dumps({"error": type(error).__name__, "message": str(error)}, ensure_ascii=False), file=sys.stderr)
            return 2

    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        return C_CommandLine().run(argv)
