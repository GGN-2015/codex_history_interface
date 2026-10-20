import argparse
import json
import sys

from .client import C_CodexInterface
from .errors import CodexHistoryError


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read-only access to local Codex conversations")
    parser.add_argument("--home", help="Codex data directory; overrides CODEX_HOME")
    parser.add_argument("--db", action="append", default=[], help="An additional SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Print all conversation IDs, including archived conversations")
    for name in ("title", "messages"):
        command = commands.add_parser(name)
        command.add_argument("chat_id", help="Exact conversation ID from the list command")
    export = commands.add_parser("export-tex", help="Export visible rounds and local assets to .tex without compiling")
    export.add_argument("chat_id", help="Exact conversation ID from the list command")
    export.add_argument("output_path", help="Destination .tex file outside the Codex data directory")
    export.add_argument("--overwrite", action="store_true", help="Replace an existing destination after preparing the document and assets")
    arguments = parser.parse_args(argv)
    try:
        reader = C_CodexInterface(arguments.home, databases=arguments.db)
        if arguments.command == "list":
            result = reader.getChatList()
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


if __name__ == "__main__":
    raise SystemExit(main())
