"""Regression tests for append-only Frame repair overlays."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("weilan_trace.py")


def run_cli(home, *args, check=True):
    env = {**os.environ, "WEILAN_METHOD_HOME": str(home)}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        capture_output=True,
        env=env,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="weilan-frame-repair-") as temporary:
        home = Path(temporary)
        opened = json.loads(
            run_cli(
                home,
                "open",
                "--workspace",
                str(home / "workspace"),
                "--level",
                "L2",
                "--problem",
                "exercise frame repair",
                "--success",
                "repair overlay validates",
            ).stdout
        )
        frame_id = opened["frame_id"]
        bad_event = run_cli(
            home,
            "event",
            "--frame-id",
            frame_id,
            "--type",
            "holder_selected",
            "--field",
            "note=invalid holder event",
            check=False,
        )
        if bad_event.returncode == 0:
            raise AssertionError("runtime accepted an invalid holder_selected event")

        close_guard = json.loads(
            run_cli(
                home,
                "open",
                "--workspace",
                str(home / "workspace"),
                "--level",
                "L2",
                "--problem",
                "exercise close guard",
                "--success",
                "close rejects invalid historical events",
            ).stdout
        )
        close_guard_path = Path(close_guard["path"])
        close_guard_lines = [
            json.loads(line) for line in close_guard_path.read_text(encoding="utf-8").splitlines()
        ]
        close_guard_corrupt = {
            **close_guard_lines[0],
            "event_id": "close-guard-corrupt-holder",
            "event_type": "holder_selected",
            "data": {"note": "missing required holder fields"},
        }
        close_guard_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in [close_guard_lines[0], close_guard_corrupt]
            ),
            encoding="utf-8",
        )
        close_guard_result = run_cli(
            home,
            "close",
            "--frame-id",
            close_guard["frame_id"],
            "--outcome",
            "success",
            "--verdict",
            "should fail",
            check=False,
        )
        if close_guard_result.returncode == 0 or "frame validation failed before close" not in close_guard_result.stderr:
            raise AssertionError("close accepted a frame with invalid holder_selected data")

        # Simulate the historical corrupt event shape without using the guarded command path.
        frame_path = Path(opened["path"])
        lines = [json.loads(line) for line in frame_path.read_text(encoding="utf-8").splitlines()]
        corrupt = {
            **lines[0],
            "event_id": "corrupt-holder-event",
            "event_type": "holder_selected",
            "data": {"note": "historical corrupt holder"},
        }
        closed = {
            **lines[0],
            "event_id": "closed-event",
            "event_type": "frame_closed",
            "data": {"outcome": "success", "verdict": "historical close"},
        }
        frame_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in [lines[0], corrupt, closed]),
            encoding="utf-8",
        )
        invalid = run_cli(home, "validate", "--frame-id", frame_id, "--require-closed", check=False)
        if invalid.returncode == 0 or "holder_selected requires candidate_id or holder" not in invalid.stdout:
            raise AssertionError("corrupt frame was not detected before repair")

        repair = run_cli(
            home,
            "frame-repair",
            "--frame-id",
            frame_id,
            "--target-event-id",
            "corrupt-holder-event",
            "--reason",
            "supersede historical missing holder_selected fields",
            "--field",
            "candidate_id=repaired-holder",
            "--field",
            "why_reasonable=repair preserves historical assessment holder",
            "--field",
            "next_expected_evidence=frame validates after overlay",
            "--field",
            "death_line=repair remains invalid if required holder fields are absent",
            "--require-closed",
        )
        repair_payload = json.loads(repair.stdout)
        if repair_payload["frame_id"] != frame_id:
            raise AssertionError("repair receipt frame mismatch")
        valid = run_cli(home, "validate", "--frame-id", frame_id, "--require-closed")
        if not json.loads(valid.stdout)["valid"]:
            raise AssertionError("repair overlay did not validate frame")
        shown = json.loads(run_cli(home, "show", "--frame-id", frame_id).stdout)
        repaired_holder = next(item for item in shown if item["event_id"] == "corrupt-holder-event")
        if repaired_holder["data"].get("candidate_id") != "repaired-holder":
            raise AssertionError("show did not apply repair overlay")

    print(json.dumps({
        "valid": True,
        "invalid_holder_rejected": True,
        "close_guard_rejected_invalid_holder": True,
        "repair_overlay": True,
    }, indent=2))


if __name__ == "__main__":
    main()
