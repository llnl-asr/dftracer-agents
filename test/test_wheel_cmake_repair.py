"""The dftracer wheel's CMake package names its build machine; the prerelease install points it inside the wheel."""
from pathlib import Path

from dftracer_agents.mcp_tools.tools.session.install import _repair_wheel_cmake_package


def _wheel(tmp: Path) -> Path:
    pkg = tmp / "site-packages" / "dftracer"
    lib = pkg / "lib64"
    cm = lib / "cmake" / "dftracer"
    cm.mkdir(parents=True)
    for f in ("libdftracer_core.so.4.3.0", "libuv.so", "libdeflate.so.0", "libyaml-cpp.so", "libcpp-logger.so"):
        (lib / f).write_text("")
    (cm / "dftracer-targets-release.cmake").write_text(
        'set_target_properties(dftracer_core PROPERTIES IMPORTED_LOCATION_RELEASE '
        '"/project/build/lib.linux-x86_64-cpython-313/dftracer/lib64/libdftracer_core.so.4.3.0")\n'
        'set_target_properties(dftracer_service PROPERTIES IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/dftracer_service")\n')
    (cm / "dftracer-targets.cmake").write_text(
        'set_target_properties(dftracer_core PROPERTIES INTERFACE_LINK_LIBRARIES '
        '"-lpthread;dl;-L/opt/dftracer-deps/lib64;yaml-cpp;/opt/dftracer-deps/lib/libuv.so;rt;'
        '/opt/dftracer-deps/lib64/libdeflate.so.0;cpp-logger")\n')
    (cm / "dftracer-config.cmake").write_text(
        'find_package(brahma QUIET)\nif (NOT brahma_FOUND)\n    message(FATAL_ERROR "[DFTRACER] brahma is required but '
        'not found. Please ensure brahma is installed.")\nendif ()\n')
    return pkg


def test_the_package_points_inside_the_wheel_and_needs_nothing_from_the_build_machine(tmp_path):
    pkg = _wheel(tmp_path)
    r = _repair_wheel_cmake_package(pkg)
    cm = pkg / "lib64" / "cmake" / "dftracer"
    text = "".join(f.read_text() for f in cm.glob("*.cmake"))
    assert "/project/build" not in text and "/opt/dftracer-deps" not in text
    assert '"${_IMPORT_PREFIX}/lib64/libdftracer_core.so.4.3.0"' in text
    assert "${_IMPORT_PREFIX}/lib64/libuv.so" in text and "${_IMPORT_PREFIX}/lib64/libyaml-cpp.so" in text
    assert "${_IMPORT_PREFIX}/bin/dftracer_service" in text                 # what was already relative stays
    assert "FATAL_ERROR" not in (cm / "dftracer-config.cmake").read_text()
    assert sorted(r["changed"]) == ["dftracer-config.cmake", "dftracer-targets-release.cmake", "dftracer-targets.cmake"]
    assert _repair_wheel_cmake_package(pkg)["changed"] == []               # idempotent


def test_an_install_without_a_cmake_package_is_left_alone(tmp_path):
    (tmp_path / "dftracer" / "lib64").mkdir(parents=True)
    assert _repair_wheel_cmake_package(tmp_path / "dftracer")["skipped"]
