import io
import json
import sys

import weilan_trace


def run_case():
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    fake_stdout = io.TextIOWrapper(stdout_bytes, encoding="gbk", errors="strict")
    fake_stderr = io.TextIOWrapper(stderr_bytes, encoding="gbk", errors="strict")
    try:
        sys.stdout = fake_stdout
        sys.stderr = fake_stderr
        weilan_trace.configure_console_output()
        print(json.dumps({"marker": "虃", "status": "ok"}, ensure_ascii=False))
        sys.stdout.flush()
        stdout_encoding = sys.stdout.encoding.lower().replace("_", "-")
        stderr_encoding = sys.stderr.encoding.lower().replace("_", "-")
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return stdout_bytes.getvalue().decode("utf-8"), stdout_encoding, stderr_encoding


def test_console_output_reconfigures_gbk_streams_to_utf8():
    payload, stdout_encoding, stderr_encoding = run_case()
    assert json.loads(payload)["marker"] == "虃"
    assert stdout_encoding == "utf-8"
    assert stderr_encoding == "utf-8"


def main():
    test_console_output_reconfigures_gbk_streams_to_utf8()


if __name__ == "__main__":
    main()
