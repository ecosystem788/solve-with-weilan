"""Acceptance tests for SE-0.4 prospective memory and causal event cycles."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def completed(arguments, environment):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def run(arguments, environment):
    result = completed(arguments, environment)
    if result.returncode:
        raise AssertionError(f"command failed: {result.stderr}\n{result.stdout}")
    return json.loads(result.stdout)


def register(workspace, scope, source, goal_ref, kind, name, environment, not_before=None):
    arguments = [
        "prospective-register", "--workspace", workspace, "--scope", scope,
        "--goal-ref", goal_ref, "--description", f"future intention {goal_ref}",
        "--event-kind", kind, "--event-name", name,
        "--death-line", "collapse if the condition becomes invalid",
        "--source", source,
    ]
    if not_before:
        arguments.extend(["--not-before", not_before])
    return run(arguments, environment)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="weilan-prospective-", dir=str(parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        scope = "skill-evolution"
        Path(workspace).mkdir()
        Path(unrelated).mkdir()
        source = root / "authority.txt"
        source.write_text("authorized fixture", encoding="utf-8")

        run([
            "memory-control", "--workspace", workspace, "--scope", scope,
            "--state", "active", "--directive", "run prospective fixture",
        ], environment)

        register(workspace, scope, str(source), "goal:user-ready", "user", "build_ready", environment)
        observed = run([
            "prospective-observe", "--workspace", workspace, "--scope", scope,
            "--kind", "user", "--name", "build_ready", "--source", str(source),
        ], environment)
        if observed["cycle"]["status"] != "READY":
            raise AssertionError("one matching user condition did not produce a ready cycle")
        causal_event_id = observed["causal_event_id"]
        before_transition = run([
            "prospective-show", "--workspace", workspace, "--scope", scope,
        ], environment)
        if before_transition["goals"]["goal:user-ready"]["state"] != "ACTIVE":
            raise AssertionError("observed event auto-transitioned a future goal")
        satisfied = run([
            "prospective-transition", "--workspace", workspace, "--scope", scope,
            "--goal-ref", "goal:user-ready", "--state", "satisfied",
            "--causal-event-id", causal_event_id, "--reason", "condition verified",
            "--source", str(source),
        ], environment)
        if satisfied["goal"]["state"] != "SATISFIED":
            raise AssertionError("matching causal event did not support explicit satisfaction")

        register(workspace, scope, str(source), "goal:tool-a", "tool", "tests_green", environment)
        register(workspace, scope, str(source), "goal:tool-b", "tool", "tests_green", environment)
        ambiguous = run([
            "prospective-observe", "--workspace", workspace, "--scope", scope,
            "--kind", "tool", "--name", "tests_green", "--source", str(source),
        ], environment)
        if ambiguous["cycle"]["status"] != "AMBIGUOUS" or ambiguous["cycle"]["plans"]:
            raise AssertionError("multiple matching goals did not stop as ambiguous")

        register(workspace, scope, str(source), "goal:old", "environment", "schema_changed", environment)
        register(workspace, scope, str(source), "goal:new", "environment", "schema_v2", environment)
        superseded = run([
            "prospective-transition", "--workspace", workspace, "--scope", scope,
            "--goal-ref", "goal:old", "--state", "superseded",
            "--replacement-goal-ref", "goal:new", "--reason", "replacement registered",
            "--source", str(source),
        ], environment)
        if superseded["goal"]["state"] != "SUPERSEDED":
            raise AssertionError("future goal supersession failed")
        collapsed = run([
            "prospective-transition", "--workspace", workspace, "--scope", scope,
            "--goal-ref", "goal:new", "--state", "collapsed",
            "--reason", "environment condition invalidated", "--source", str(source),
        ], environment)
        if collapsed["goal"]["state"] != "COLLAPSED":
            raise AssertionError("future goal collapse failed")

        register(
            workspace, scope, str(source), "goal:clock-due", "clock", "review_due",
            environment, "2000-01-01T00:00:00+00:00",
        )
        clock = run([
            "prospective-observe", "--workspace", workspace, "--scope", scope,
            "--kind", "clock", "--name", "review_due", "--goal-ref", "goal:clock-due",
            "--source", str(source),
        ], environment)
        if clock["cycle"]["status"] != "READY":
            raise AssertionError("authorized due clock condition did not inject one event")
        register(
            workspace, scope, str(source), "goal:clock-future", "clock", "future_due",
            environment, "2999-01-01T00:00:00+00:00",
        )
        early_clock = completed([
            "prospective-observe", "--workspace", workspace, "--scope", scope,
            "--kind", "clock", "--name", "future_due", "--goal-ref", "goal:clock-future",
            "--source", str(source),
        ], environment)
        if early_clock.returncode == 0 or "not currently true" not in early_clock.stderr:
            raise AssertionError("future clock condition injected an unauthorized event")

        isolated = run([
            "prospective-show", "--workspace", unrelated, "--scope", scope,
        ], environment)
        if isolated["goals"] or isolated["causal_events"]:
            raise AssertionError("prospective memory leaked across workspaces")

        run([
            "memory-control", "--workspace", workspace, "--scope", scope,
            "--state", "paused", "--directive", "pause prospective fixture",
        ], environment)
        paused_write = completed([
            "prospective-register", "--workspace", workspace, "--scope", scope,
            "--goal-ref", "goal:blocked", "--description", "must not write",
            "--event-kind", "user", "--event-name", "blocked",
            "--death-line", "never", "--source", str(source),
        ], environment)
        if paused_write.returncode == 0 or not any(
            marker in paused_write.stderr for marker in ("paused", "explicitly active")
        ):
            raise AssertionError("paused scope accepted a prospective write")

        print(json.dumps({
            "valid": True,
            "future_goal_lifecycle": True,
            "unified_event_kinds": ["user", "tool", "environment", "clock"],
            "explicit_transition_required": True,
            "ambiguous_cycle_stops": True,
            "future_clock_blocked": True,
            "paused_scope_blocked": True,
            "cross_workspace_isolated": True,
            "background_loop": False,
        }, indent=2))


if __name__ == "__main__":
    main()
