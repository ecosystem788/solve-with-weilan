"""Regression tests for the WeiLan Memory 0.2 activation gate."""

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


def assert_state(result, expected):
    actual = result["activation"]["state"]
    if actual != expected:
        raise AssertionError(f"expected {expected}, got {actual}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()

    if args.temp_parent:
        temp_parent = Path(args.temp_parent).resolve()
    elif os.environ.get("CODEX_HOME"):
        temp_parent = Path(os.environ["CODEX_HOME"]).resolve().parent
    else:
        temp_parent = Path.home()
    temp_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-memory-test-", dir=str(temp_parent)) as temporary:
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = temporary

        no_context = run(["memory-recall", "--workspace", r"D:\MemoryTest\None"], environment)
        assert_state(no_context, "NO_CONTEXT")

        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Paused",
                "--scope", "fusion", "--state", "paused", "--directive", "pause fusion",
            ],
            environment,
        )
        paused = run(
            ["memory-recall", "--workspace", r"D:\MemoryTest\Paused", "--scope", "fusion"],
            environment,
        )
        assert_state(paused, "PAUSED")

        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Confirm",
                "--scope", "memory", "--state", "active", "--directive", "wait for confirmation",
                "--resume-requires-confirmation",
            ],
            environment,
        )
        run(
            [
                "memory-update", "--workspace", r"D:\MemoryTest\Confirm", "--scope", "memory",
                "--focus", "confirmation test", "--status", "ready", "--next", "wait",
            ],
            environment,
        )
        confirm = run(
            ["memory-recall", "--workspace", r"D:\MemoryTest\Confirm", "--scope", "memory"],
            environment,
        )
        assert_state(confirm, "CONFIRM_REQUIRED")

        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Stale",
                "--scope", "memory", "--state", "active", "--directive", "first control",
            ],
            environment,
        )
        run(
            [
                "memory-update", "--workspace", r"D:\MemoryTest\Stale", "--scope", "memory",
                "--focus", "stale test", "--status", "v1", "--next", "step",
            ],
            environment,
        )
        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Stale",
                "--scope", "memory", "--state", "active", "--directive", "new control",
            ],
            environment,
        )
        stale = run(
            ["memory-recall", "--workspace", r"D:\MemoryTest\Stale", "--scope", "memory"],
            environment,
        )
        assert_state(stale, "STALE")

        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Active",
                "--scope", "memory", "--state", "active", "--directive", "continue memory",
            ],
            environment,
        )
        run(
            [
                "memory-update", "--workspace", r"D:\MemoryTest\Active", "--scope", "memory",
                "--focus", "active test", "--status", "current", "--next", "continue",
            ],
            environment,
        )
        active = run(
            ["memory-recall", "--workspace", r"D:\MemoryTest\Active\child", "--scope", "memory"],
            environment,
        )
        assert_state(active, "ACTIVE")
        if not active["activation"]["continuation_allowed"]:
            raise AssertionError("ACTIVE must allow continuation")

        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Multi",
                "--scope", "memory-system", "--state", "active", "--directive", "continue memory",
            ],
            environment,
        )
        run(
            [
                "memory-update", "--workspace", r"D:\MemoryTest\Multi", "--scope", "memory-system",
                "--focus", "memory system", "--status", "active", "--next", "continue",
            ],
            environment,
        )
        run(
            [
                "memory-control", "--workspace", r"D:\MemoryTest\Multi",
                "--scope", "local-llm-fusion", "--state", "paused", "--directive", "pause model",
            ],
            environment,
        )
        multi = run(["memory-recall", "--workspace", r"D:\MemoryTest\Multi"], environment)
        assert_state(multi, "ACTIVE")
        if multi["activation"].get("scope") != "memory-system":
            raise AssertionError("the unique ACTIVE scope was not selected")

        print(
            json.dumps(
                {
                    "valid": True,
                    "states": ["NO_CONTEXT", "PAUSED", "CONFIRM_REQUIRED", "STALE", "ACTIVE"],
                    "unique_active_scope": "memory-system",
                    "cross_workspace_isolated": not no_context["matched"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
