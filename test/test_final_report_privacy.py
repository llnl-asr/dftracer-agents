"""The final report folder carries no user paths: patches are anonymized, install.sh names no install prefix."""
import getpass

from dftracer_agents.mcp_tools.tools.session import final_report as F


def test_patch_user_paths_are_anonymized(tmp_path):
    ws = tmp_path / "ws"
    p = tmp_path / "annotated.patch"
    user = getpass.getuser()
    p.write_text(f'+  f(ropt_redirect(d, "/p/lustre5/{user}/c1/data", "E", "s"));\n+  g("{ws}/x");\n')
    F._anonymize_path_refs(p, ws)
    text = p.read_text()
    assert user not in text and "/p/lustre5/$USER/c1/data" in text and '"$WS/x"' in text


def test_install_script_names_no_install_prefix(tmp_path):
    F._write_install(tmp_path, {"app_name": "app", "dftracer_install_prefix": "/home/someone/venv"})
    text = "".join(f.read_text() for f in tmp_path.rglob("*") if f.is_file())
    assert "/home/someone/venv" not in text
