"""Acceptance tests for SE-0.2 episodic recall and deterministic projection rebuild."""

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
    parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="weilan-episode-test-", dir=str(parent)) as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment["WEILAN_METHOD_HOME"] = str(root / "method-state")
        workspace = str(root / "workspace")
        unrelated = str(root / "unrelated")
        Path(workspace).mkdir()
        Path(unrelated).mkdir()
        scope = "skill-evolution"

        run(["memory-control", "--workspace", workspace, "--scope", scope, "--state", "active", "--directive", "complete SE-0.2"], environment)
        opened = run([
            "open", "--level", "L2", "--workspace", workspace, "--scope", scope,
            "--branch", "main", "--relation", "root", "--problem", "repair projection rebuild gap",
            "--success", "episodic evidence is searchable",
        ], environment)
        frame_id = opened["frame_id"]
        run(["event", "--frame-id", frame_id, "--type", "candidate_admitted", "--field", "candidate_id=reducer", "--field", "description=derive projection from sources"], environment)
        run(["event", "--frame-id", frame_id, "--type", "holder_selected", "--field", "candidate_id=reducer", "--field", "why_reasonable=source backed", "--field", "next_expected_evidence=projection rebuild passes", "--field", "death_line=cannot reproduce state"], environment)
        run(["event", "--frame-id", frame_id, "--type", "discriminating_test_executed", "--field", "test=delete projection and rebuild", "--field", "result=passed"], environment)

        rebuilt = run(["projection-rebuild", "--workspace", workspace, "--scope", scope], environment)
        if rebuilt["episode_count"] != 1:
            raise AssertionError("projection reducer did not consume the scoped Frame")
        recall = run(["memory-recall", "--workspace", workspace, "--scope", scope], environment)
        if recall["activation"]["state"] != "ACTIVE":
            raise AssertionError("rebuilt projection did not satisfy the activation gate")
        projection = recall["projection"]
        if projection.get("derivation") != "deterministic_reducer_from_control_frames_and_active_semantic_memory":
            raise AssertionError("projection was not marked as reducer-derived")
        if projection["focus"] != "repair projection rebuild gap":
            raise AssertionError("projection focus was not derived from the current Frame")

        search = run(["episode-search", "--workspace", workspace, "--scope", scope, "--query", "projection rebuild", "--limit", "5"], environment)
        if not search["results"] or search["results"][0]["frame_id"] != frame_id:
            raise AssertionError("episodic search did not return the matching Frame")
        episode = search["results"][0]
        if not episode["holders"] or not episode["tests"] or not episode["source_snapshot"]["exists"]:
            raise AssertionError("episodic recall lost holder, test, or provenance")

        isolated = run(["episode-search", "--workspace", unrelated, "--scope", scope, "--query", "projection"], environment)
        if isolated["results"]:
            raise AssertionError("episodic memory leaked across workspaces")

        projection_path = Path(rebuilt["path"])
        projection_path.unlink()
        rebuilt_again = run(["projection-rebuild", "--workspace", workspace, "--scope", scope], environment)
        recall_again = run(["memory-recall", "--workspace", workspace, "--scope", scope], environment)
        if recall_again["activation"]["state"] != "ACTIVE" or not Path(rebuilt_again["path"]).is_file():
            raise AssertionError("projection was not rebuildable after cache deletion")

        print(json.dumps({
            "valid": True,
            "projection_rebuildable": True,
            "episodic_recall": True,
            "provenance_preserved": True,
            "cross_workspace_isolated": True,
        }, indent=2))


if __name__ == "__main__":
    main()
