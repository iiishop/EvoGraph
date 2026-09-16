"""User-initiated local verification; never executes a model-proposed command."""

import os
import signal
import subprocess
import tempfile
import time

from .repository import root_path


def run_verifier(repository: str, command: list[str], timeout: int = 120) -> dict:
    if not command or not command[0].strip() or len(command) > 64:
        raise ValueError("请输入非空的命令参数数组")
    started = time.monotonic()
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    # A temp file keeps noisy tests from consuming unbounded application memory.
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                command,
                cwd=root_path(repository),
                stdout=output,
                stderr=subprocess.STDOUT,
                shell=False,
                **options,
            )
        except OSError as exc:
            return {"result": "ERROR", "output": str(exc), "duration": time.monotonic() - started}
        try:
            code = process.wait(timeout=timeout)
            result = "PASS" if code == 0 else "FAIL"
            prefix = f"Exit code: {code}\n"
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            result, prefix = "ERROR", f"验证超时（{timeout}s），已终止进程树。\n"
        output.seek(0, 2)
        size = output.tell()
        output.seek(max(0, size - 30000))
        text = output.read().decode("utf-8", errors="replace")
    return {
        "result": result,
        "output": prefix + text,
        "duration": round(time.monotonic() - started, 3),
    }
