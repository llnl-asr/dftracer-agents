"""The final_report/ verifier: deterministic checks, does not trust the builder."""
import getpass
from pathlib import Path

from dftracer_agents.mcp_tools.tools.session import final_report as F


def _good(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "baseline").mkdir(parents=True)
    (ws / "opt1").mkdir()
    fr = ws / "final_report"
    sd = fr / "scripts"
    sd.mkdir(parents=True)
    (fr / "config.ini").write_text(f"WORKSPACE_ROOT={ws}\nAPP_NAME=demo\n")
    F._write_lib_load_config(sd)
    for r in ("baseline", "opt1"):
        (sd / f"run_{r}.sh").write_text('#!/bin/bash\nsource "$(dirname "$0")/lib_load_config.sh"\ncd "$WS/build"\n')
    (sd / "install.sh").write_text('#!/bin/bash\nsource "$(dirname "$0")/lib_load_config.sh"\n')
    (sd / "run_all.sh").write_text("#!/bin/bash\nbash run_baseline.sh\nbash run_opt1.sh\n")
    F._write_readme_smoke_test(sd)
    (fr / "README.md").write_text("Edit config.ini and set WORKSPACE_ROOT.\n\nbash scripts/install.sh\nbash scripts/run_all.sh "
                                  "<alloc>\n\nScripts: run_all.sh install.sh run_baseline.sh run_opt1.sh lib_load_config.sh "
                                  "readme_smoke_test.sh. Compare with REPORT.md.\n")
    (fr / "REPORT.md").write_text("# Report\nValidation: passed (reproduced within noise)\n")
    return ws, fr


def test_a_complete_anonymous_folder_passes(tmp_path):
    ws, fr = _good(tmp_path)
    v = F.verify_final_report(fr, ws)
    assert v["status"] == "pass", v["problems"]
    assert v["reproduced"] is True


def test_verifier_catches_leaks_missing_runs_bad_scripts_and_missing_config(tmp_path):
    ws, fr = _good(tmp_path)
    (fr / "REPORT.md").write_text(f"ran in {ws}/build as {getpass.getuser()}\nnot validated\n")
    (fr / "scripts" / "run_opt1.sh").unlink()
    (fr / "scripts" / "run_baseline.sh").write_text("#!/bin/bash\ncd /abs/path && if then\n")
    (fr / "config.ini").write_text("APP_NAME=demo\n")
    v = F.verify_final_report(fr, ws)
    names = {c["name"] for c in v["checks"] if not c["ok"]}
    assert {"privacy", "runs", "syntax", "loader", "config"} <= names
    assert v["status"] == "fail" and v["reproduced"] is False


def test_big_files_are_flagged_not_scanned(tmp_path, monkeypatch):
    ws, fr = _good(tmp_path)
    monkeypatch.setattr(F, "_VERIFY_MAX_BYTES", 10)
    (fr / "patches").mkdir()
    (fr / "patches" / "opt1.patch").write_text("x" * 100)
    v = F.verify_final_report(fr, ws)
    assert any("raw data/logs stay in the session" in p for p in v["problems"])


def test_run_scripts_are_collected_from_the_runs_own_scripts_dir(tmp_path):
    ws = tmp_path / "ws"
    (ws / "baseline" / "scripts").mkdir(parents=True)
    (ws / "baseline" / "scripts" / "ropt_baseline.flux.sh").write_text(f"#!/bin/bash\ncd {ws}/build_ann\n")
    dest = ws / "final_report" / "scripts"
    assert F._collect_scripts(ws, dest, ["baseline"]) == ["run_baseline.sh"]
    t = (dest / "run_baseline.sh").read_text()
    assert str(ws) not in t and "${WS}/build_ann" in t and "lib_load_config.sh" in t
