from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote_from_bytes


SEARCH_ENDPOINT = "https://s.1688.com/selloffer/offer_search.htm?keywords="
PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
SEARCH_BUCKETS = ("primary", "secondary", "alternative", "oem")


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


def build_search_url(keyword: str) -> str:
    if not keyword:
        raise ValueError("关键词不能为空。")
    if PERCENT_ESCAPE.search(keyword):
        raise ValueError("只接受原始关键词，不能传入已经包含 %XX 的编码文本。")

    try:
        keyword_bytes = keyword.encode("gbk", errors="strict")
        round_trip = keyword_bytes.decode("gbk", errors="strict")
    except UnicodeError as error:
        raise ValueError(f"关键词无法使用 GBK 编码：{error}") from error

    if round_trip != keyword:
        raise ValueError("关键词 GBK 编码往返校验失败。")

    encoded = quote_from_bytes(keyword_bytes, safe="-._~")
    return f"{SEARCH_ENDPOINT}{encoded}"


def prepare_tasks(tasks: object) -> tuple[dict[str, object], int, int]:
    if not isinstance(tasks, dict):
        raise ValueError("tasks.json 顶层必须是 JSON 对象。")

    groups = tasks.get("groups")
    if not isinstance(groups, list):
        raise ValueError("tasks.json.groups 必须是数组。")

    url_cache: dict[str, str] = {}
    item_count = 0

    for group_position, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"groups[{group_position}] 必须是对象。")

        group_label = group.get("index", group_position + 1)
        search_terms = group.get("search_terms")
        if not isinstance(search_terms, dict):
            raise ValueError(f"第 {group_label} 组的 search_terms 必须是对象。")

        for bucket in SEARCH_BUCKETS:
            items = search_terms.get(bucket)
            if not isinstance(items, list):
                raise ValueError(
                    f"第 {group_label} 组的 search_terms.{bucket} 必须是数组。"
                )

            prepared_items: list[dict[str, object]] = []
            for item_position, item in enumerate(items):
                extras: dict[str, object] = {}
                if isinstance(item, str):
                    term = item
                elif isinstance(item, dict):
                    term = item.get("term")
                    extras = {
                        key: value
                        for key, value in item.items()
                        if key not in {"term", "search_url"}
                    }
                else:
                    raise ValueError(
                        f"第 {group_label} 组 search_terms.{bucket}"
                        f"[{item_position}] 必须是字符串或对象。"
                    )

                if not isinstance(term, str) or not term:
                    raise ValueError(
                        f"第 {group_label} 组 search_terms.{bucket}"
                        f"[{item_position}] 缺少非空 term。"
                    )

                try:
                    if term not in url_cache:
                        url_cache[term] = build_search_url(term)
                    search_url = url_cache[term]
                except ValueError as error:
                    raise ValueError(
                        f"第 {group_label} 组 search_terms.{bucket}"
                        f"[{item_position}]：{error}"
                    ) from error

                prepared_items.append(
                    {"term": term, "search_url": search_url, **extras}
                )
                item_count += 1

            search_terms[bucket] = prepared_items

    tasks["search_url_encoding"] = "gbk-percent"
    return tasks, item_count, len(url_cache)


def update_tasks_file(tasks_path: Path) -> tuple[int, int]:
    if not tasks_path.is_file():
        raise ValueError(f"tasks.json 不存在或不是文件：{tasks_path}")

    try:
        tasks = json.loads(tasks_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取 tasks.json：{error}") from error

    prepared, item_count, unique_count = prepare_tasks(tasks)
    serialized = json.dumps(prepared, ensure_ascii=False, indent=2) + "\n"

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=tasks_path.parent,
            prefix=f".{tasks_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        os.replace(temporary_path, tasks_path)
        temporary_path = None
    except OSError as error:
        raise ValueError(f"无法原子更新 tasks.json：{error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return item_count, unique_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="一次性为 tasks.json 中的全部搜索词生成 GBK 搜索 URL。"
    )
    parser.add_argument("tasks_json", type=Path, help="待原地更新的 tasks.json 路径")
    args = parser.parse_args()

    try:
        item_count, unique_count = update_tasks_file(args.tasks_json.resolve())
    except ValueError as error:
        parser.exit(2, f"错误：{error}\n")

    print(f"已处理 {item_count} 个搜索项（{unique_count} 个唯一关键词）。")


if __name__ == "__main__":
    main()
