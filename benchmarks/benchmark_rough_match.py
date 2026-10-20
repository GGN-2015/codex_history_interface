import argparse
import json
import statistics
from time import perf_counter

from codex_history_interface import C_RoughMatchIndex


def run_benchmark(document_count: int, repeats: int) -> dict:
    index = C_RoughMatchIndex()
    body = "Python rendering configuration database caching and Markdown tables. " * 20
    started = perf_counter()
    total_characters = 0
    for number in range(document_count):
        title = f"开发记录 {number}"
        content = body + ("截骨规划界面" if number % 997 == 0 else "")
        total_characters += len(title) + len(content)
        index.updateDocument(f"chat-{number:08d}", title, [content])
    build_seconds = perf_counter() - started
    measurements = {}
    for label, query in (("selective", "截骨规划"), ("common", "Python rendering"), ("typo", "markdwon"), ("absent", "𠀀𠀁𠀂")):
        index.roughMatch(query, 10)
        durations = []
        for _ in range(repeats):
            started = perf_counter()
            results = index.roughMatch(query, 10)
            durations.append((perf_counter() - started) * 1000)
        measurements[label] = {"median_ms": round(statistics.median(durations), 3), "max_ms": round(max(durations), 3), "results": len(results)}
    return {"documents": document_count, "characters": total_characters, "build_seconds": round(build_seconds, 3), "repeats": repeats, "queries": measurements}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the in-memory rough-match index on synthetic conversations.")
    parser.add_argument("--documents", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=25)
    arguments = parser.parse_args()
    if arguments.documents <= 0 or arguments.repeats <= 0:
        parser.error("--documents and --repeats must be positive integers")
    print(json.dumps(run_benchmark(arguments.documents, arguments.repeats), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
