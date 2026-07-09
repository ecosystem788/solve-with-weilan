"""Regression test for contested semantic-memory recall.

The false-zero guard keeps conflicting active memories searchable, but prevents
them from being projected as clean decisions until one side is superseded or
disposed.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def run(arguments, environment):
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()

    temp_parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weilan-false-zero-", dir=str(temp_parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        Path(workspace).mkdir()
        source = root / "source.txt"
        source.write_text("fixture", encoding="utf-8")

        run(
            [
                "memory-control",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--state",
                "active",
                "--directive",
                "exercise contested semantic recall",
            ],
            environment,
        )
        first = run(
            [
                "memory-consolidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--kind",
                "decision",
                "--summary",
                "alpha route is settled",
                "--source",
                str(source),
            ],
            environment,
        )
        second = run(
            [
                "memory-consolidate",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
                "--kind",
                "decision",
                "--summary",
                "beta route contradicts alpha",
                "--source",
                str(source),
                "--conflicts-with",
                first["memory_id"],
            ],
            environment,
        )

        conflicts = run(
            [
                "memory-conflicts",
                "--workspace",
                workspace,
                "--scope",
                "memory-system",
            ],
            environment,
        )
        if not conflicts["unresolved_conflicts"]:
            raise AssertionError("active semantic conflict was not reported")

        search = run(
            ["memory-search", "--workspace", workspace, "--scope", "memory-system", "--query", "route"],
            environment,
        )
        by_id = {item["memory_id"]: item for item in search["results"]}
        if first["memory_id"] not in by_id or second["memory_id"] not in by_id:
            raise AssertionError("contested entries stopped being searchable")
        if by_id[first["memory_id"]].get("contested_with") != [second["memory_id"]]:
            raise AssertionError("first contested entry did not point at the conflicting active entry")
        if by_id[second["memory_id"]].get("contested_with") != [first["memory_id"]]:
            raise AssertionError("second contested entry did not point at the conflicting active entry")

        rebuild = run(
            ["projection-rebuild", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        if not any("contested" in warning for warning in rebuild.get("warnings", [])):
            raise AssertionError("projection rebuild did not warn about contested semantic entries")
        recall = run(
            ["memory-recall", "--workspace", workspace, "--scope", "memory-system"],
            environment,
        )
        projection = recall["projection"]
        if projection["decisions"]:
            raise AssertionError("contested decisions leaked into clean projection decisions")
        contested = projection.get("contested_semantic_entries", [])
        contested_ids = {item["memory_id"] for item in contested}
        if {first["memory_id"], second["memory_id"]} - contested_ids:
            raise AssertionError("projection did not preserve contested entries separately")

        print(json.dumps({
            "false_zero_guard": True,
            "contested_entries_searchable": True,
            "clean_decisions_exclude_contested": True,
            "contested_ids": sorted(contested_ids),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
