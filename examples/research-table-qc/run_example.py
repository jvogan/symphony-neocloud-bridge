"""Run the count-table example in a fresh local workspace."""
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "research-table-qc"


def main() -> int:
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="research-table-qc-", dir=runtime))
    copied = workspace / "examples" / "research-table-qc"
    copied.mkdir(parents=True)
    for name in ("counts.csv", "workflow.py"):
        shutil.copyfile(EXAMPLE / name, copied / name)
    result = subprocess.run([
        str(ROOT / "bin" / "cloud-bridge"), "run-local",
        str(EXAMPLE / "launch_manifest.json"),
        "--repo-dir", str(workspace), "--runtime-dir", str(workspace / "runner"),
    ], check=False)
    print(f"Run directory: {workspace.relative_to(ROOT)}")
    if result.returncode == 0:
        print(f"Report: {(workspace / 'runpod-execution/artifacts/report.md').relative_to(ROOT)}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
