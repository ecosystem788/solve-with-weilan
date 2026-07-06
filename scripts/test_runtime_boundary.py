"""Compatibility tests for the shared runtime-core extraction boundary."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import runtime_core
import weilan_trace


LEGACY_COMMANDS = {
    "close", "episode-index", "episode-search", "event", "evidence-capture",
    "evidence-disposition", "evidence-promote", "evidence-show",
    "governance-pressure-invalidate", "governance-pressure-propagate",
    "governance-pressure-record", "governance-show", "governance-target-register",
    "governance-target-transition", "lineage-show", "memory-archive-plan",
    "memory-conflicts", "memory-consolidate", "memory-control", "memory-disposition",
    "memory-index", "memory-recall", "memory-retention-plan", "memory-search",
    "memory-update", "metabolic-abort", "metabolic-commit", "metabolic-contract",
    "metabolic-materialize", "metabolic-plan-transition", "metabolic-prepare",
    "metabolic-propose", "metabolic-recover", "metabolic-run", "metabolic-run-show",
    "metabolic-transaction-show", "open", "persistence-audit",
    "persistence-audit-show", "projection-rebuild", "self-project", "show", "validate",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent")
    args = parser.parse_args()
    parent = Path(args.temp_parent).resolve() if args.temp_parent else Path.home()
    parent.mkdir(parents=True, exist_ok=True)

    cli = weilan_trace.build_parser()
    subcommands = next(action.choices for action in cli._actions if getattr(action, "choices", None))
    missing = sorted(LEGACY_COMMANDS - set(subcommands))
    if missing:
        raise AssertionError(f"legacy CLI commands disappeared: {', '.join(missing)}")

    extracted = (
        "utc_now", "state_root", "canonical_workspace", "normalized_workspace",
        "workspace_key", "normalize_scope", "scope_key", "normalize_branch",
        "file_sha256", "write_json_atomic", "write_event_file_atomic",
        "exclusive_file_lock",
    )
    for name in extracted:
        if getattr(weilan_trace, name) is not getattr(runtime_core, name):
            raise AssertionError(f"{name} is not supplied by runtime_core")

    with tempfile.TemporaryDirectory(prefix="weilan-runtime-boundary-", dir=str(parent)) as temporary:
        root = Path(temporary)
        prior = os.environ.get("WEILAN_METHOD_HOME")
        os.environ["WEILAN_METHOD_HOME"] = str(root / "method-state")
        try:
            if runtime_core.state_root() != root / "method-state":
                raise AssertionError("state-root override changed during extraction")
            target = root / "atomic" / "value.json"
            runtime_core.write_json_atomic(target, {"value": "preserved"})
            if json.loads(target.read_text(encoding="utf-8")) != {"value": "preserved"}:
                raise AssertionError("atomic JSON behavior changed")
            lock = root / "locks" / "scope.lock"
            with runtime_core.exclusive_file_lock(lock):
                if not lock.is_file():
                    raise AssertionError("shared lock was not created")
        finally:
            if prior is None:
                os.environ.pop("WEILAN_METHOD_HOME", None)
            else:
                os.environ["WEILAN_METHOD_HOME"] = prior

    print(json.dumps({
        "valid": True,
        "legacy_command_count": len(LEGACY_COMMANDS),
        "runtime_core_exports": len(extracted),
        "atomic_write_preserved": True,
        "scope_lock_preserved": True,
    }, indent=2))


if __name__ == "__main__":
    main()
