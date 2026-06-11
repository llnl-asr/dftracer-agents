import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from dftracer_agents import goose_pipeline
from dftracer_agents.mcp_servers.modules.shared import run_shell_command


DETECT_PAYLOAD = {
    "summary": "detected project facts",
    "language": "cpp",
    "build_system": "cmake",
    "uses_mpi": True,
    "mpi_detection": "optional",
    "uses_hip": False,
    "dftracer_flags": {
        "DFTRACER_ENABLE_MPI": "ON",
        "DFTRACER_ENABLE_HIP_TRACING": "OFF",
        "DFTRACER_ENABLE_DYNAMIC_DETECTION": "ON",
        "DFTRACER_BUILD_PYTHON_BINDINGS": "OFF",
        "DFTRACER_BUILD_TYPE": "RelWithDebInfo",
    },
    "notes": [],
    "handoff": {
        "language": "cpp",
        "build_system": "cmake",
        "uses_mpi": True,
        "mpi_detection": "optional",
        "uses_hip": False,
        "dftracer_flags": {
            "DFTRACER_ENABLE_MPI": "ON",
            "DFTRACER_ENABLE_HIP_TRACING": "OFF",
            "DFTRACER_ENABLE_DYNAMIC_DETECTION": "ON",
            "DFTRACER_BUILD_PYTHON_BINDINGS": "OFF",
            "DFTRACER_BUILD_TYPE": "RelWithDebInfo",
        },
    },
}
BUILD_SETUP_PAYLOAD = {
    "summary": "build setup ready",
    "needs_docs": False,
    "needs_docs_reason": "",
    "commands": ["cmake -S . -B build"],
    "notes": [],
    "handoff": {
        "commands": ["cmake -S . -B build"],
        "install_prefix": "/tmp/install",
        "needs_docs": False,
        "needs_docs_reason": "",
    },
}
DEFAULT_RUN_PAYLOAD = {
    "summary": "default run found",
    "run_cmd": "ctest --output-on-failure",
    "notes": [],
    "handoff": {"run_cmd": "ctest --output-on-failure"},
}
ANNOTATE_PAYLOAD = {
    "summary": "annotation plan created",
    "ok": True,
    "annotation": {"files": []},
    "patch": "",
    "notes": [],
    "handoff": {"ok": True, "language": "cpp", "patch_applied": False},
}
REBUILD_PAYLOAD = {
    "summary": "dftracer build prepared",
    "commands": ["cmake --build build"],
    "notes": [],
    "handoff": {"commands": ["cmake --build build"], "install_prefix": "/tmp/install"},
}
POSTPROCESS_PAYLOAD = {
    "summary": "postprocess planned",
    "commands": ["dftracer-postprocess"],
    "notes": [],
    "handoff": {
        "post_dir": "/tmp/post",
        "compacted_trace_dir": "/tmp/post/compacted",
        "index_dir": "/tmp/post/index",
    },
}
DFANALYZER_PAYLOAD = {
    "summary": "analysis planned",
    "commands": ["dfanalyzer"],
    "notes": [],
    "handoff": {"analysis_dir": "/tmp/analysis", "commands": ["dfanalyzer"]},
}
PAYLOAD_BY_RECIPE = {
    "10_detect_stage.yaml": DETECT_PAYLOAD,
    "20_build_setup_stage.yaml": BUILD_SETUP_PAYLOAD,
    "30_default_run_stage.yaml": DEFAULT_RUN_PAYLOAD,
    "41_annotate_c_cpp_stage.yaml": ANNOTATE_PAYLOAD,
    "50_build_with_dftracer_stage.yaml": REBUILD_PAYLOAD,
    "60_postprocess_stage.yaml": POSTPROCESS_PAYLOAD,
    "70_dfanalyzer_stage.yaml": DFANALYZER_PAYLOAD,
}


class GooseOutputParsingTests(unittest.TestCase):
    def test_parse_goose_transcript_extracts_final_stage_json(self) -> None:
        transcript = json.dumps({
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolRequest",
                            "toolCall": {
                                "status": "success",
                                "value": {
                                    "name": "recipe__final_output",
                                    "arguments": {
                                        "stage": "test_default_build_setup",
                                        "summary": "planned build",
                                        "needs_docs": False,
                                        "needs_docs_reason": "",
                                        "commands": ["/usr/bin/make"],
                                        "notes": ["ok"],
                                        "handoff": {
                                            "commands": ["/usr/bin/make"],
                                            "install_prefix": "/tmp/install",
                                            "needs_docs": False,
                                            "needs_docs_reason": "",
                                        },
                                    },
                                },
                            },
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({
                                "stage": "test_default_build_setup",
                                "summary": "planned build",
                                "needs_docs": False,
                                "needs_docs_reason": "",
                                "commands": ["/usr/bin/make"],
                                "notes": ["ok"],
                                "handoff": {
                                    "commands": ["/usr/bin/make"],
                                    "install_prefix": "/tmp/install",
                                    "needs_docs": False,
                                    "needs_docs_reason": "",
                                },
                            }),
                        }
                    ],
                },
            ]
        })

        payload = goose_pipeline._parse_goose_json_text(transcript)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["stage"], "test_default_build_setup")
        self.assertEqual(payload["handoff"]["install_prefix"], "/tmp/install")



class GooseStageInputTests(unittest.TestCase):
    def test_build_setup_uses_compact_repo_summary(self) -> None:
        defaults = {
            "name": "demo",
            "repo_url": "https://example.invalid/demo.git",
            "repo_ref": "main",
            "language": "cpp",
            "workspace_root": "/tmp/workspace",
            "repo_dir": "/tmp/workspace/source/demo",
            "venv_dir": "/tmp/workspace/venv",
            "trace_dir": "/tmp/workspace/traces/run1",
            "post_dir": "/tmp/workspace/artifacts/post",
            "compacted_trace_dir": "/tmp/workspace/artifacts/post/compacted",
            "analysis_dir": "/tmp/workspace/artifacts/analysis",
            "repo_summary_json": json.dumps([
                "src/main.cpp",
                "README.md",
                "configure",
                "configure.ac",
                "Makefile.am",
                "doc/user/install.rst",
                "random/data.txt",
                "bootstrap",
                "python/tool.py",
            ]),
            "repo_attrs_json": json.dumps({
                "language": "cpp",
                "languages_used": ["build", "cpp", "python"],
                "has_cpp": True,
                "has_python": True,
                "has_cmake": False,
                "has_pyproject": False,
                "uses_mpi": True,
                "uses_hip": False,
            }),
        }

        params, stage_input = goose_pipeline.build_goose_stage_params(
            "test_default_build_setup",
            defaults=defaults,
            pipeline_context_file=Path('/tmp/context.txt'),
            stage_results={
                "detect": {
                    "stage": "detect",
                    "summary": "detected",
                    "handoff": {
                        "language": "cpp",
                        "build_system": "autotools",
                        "uses_mpi": True,
                        "mpi_detection": "optional",
                        "uses_hip": False,
                        "dftracer_flags": {},
                    },
                }
            },
            feedback_context={"stage": "test_default_build_setup", "has_feedback": False, "prior_feedback": []},
        )

        compact_summary = json.loads(params["repo_summary_json"])
        self.assertIn("README.md", compact_summary)
        self.assertIn("configure.ac", compact_summary)
        self.assertIn("Makefile.am", compact_summary)
        self.assertNotIn("random/data.txt", compact_summary)
        self.assertEqual(stage_input["planning_constraints"]["avoid_delegation"], True)
        self.assertIn("README.md", stage_input["doc_targets"])
        self.assertEqual(stage_input["repo_reference"]["repo_url"], "https://example.invalid/demo.git")


class GooseStageArtifactTests(unittest.TestCase):
    def test_stage_artifacts_are_written_under_workspace(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]
        skipped_feedback = {
            "prompt": "review",
            "prompted": False,
            "what_worked": "",
            "what_did_not_work": "",
            "next_time": "",
            "rating": "neutral",
            "status": "prompt_skipped",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            repo_dir = workspace_root / "source" / "demo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "README.md").write_text("build docs\n", encoding="utf-8")

            fake_exec = {
                "stage": "build_app",
                "ok": True,
                "steps": [
                    {
                        "ok": True,
                        "command": "cmake -S . -B build",
                        "cwd": str(repo_dir),
                        "returncode": 0,
                        "stdout": "configured",
                        "stderr": "",
                    }
                ],
                "effective_commands": ["cmake -S . -B build"],
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "1"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in payloads]), patch.object(
                goose_pipeline,
                "execute_pipeline_stage",
                return_value=fake_exec,
            ), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                result = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    repo_dir=str(repo_dir),
                    venv_dir=str(workspace_root / "venv"),
                    trace_dir=str(workspace_root / "traces" / "run1"),
                    post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                    compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                    analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                    progress=False,
                    feedback_prompt=False,
                )

            stage_dir = Path(result["artifacts"]["test_default_build_setup"]["stage_dir"])
            self.assertTrue(stage_dir.is_relative_to((workspace_root / "artifacts" / goose_pipeline.DEFAULT_TERMINAL_RUN_ID / "stage_logs").resolve()))
            self.assertTrue((stage_dir / "stage_input.json").exists())
            self.assertTrue((stage_dir / "stage_result.json").exists())
            self.assertTrue((stage_dir / "planned_commands.txt").exists())
            self.assertTrue((stage_dir / "execution_result.json").exists())
            self.assertTrue((stage_dir / "execution_steps.log").exists())


class GooseStageExecutionTests(unittest.TestCase):
    def test_build_setup_stage_executes_planned_commands(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]

        skipped_feedback = {
            "prompt": "review",
            "prompted": False,
            "what_worked": "",
            "what_did_not_work": "",
            "next_time": "",
            "rating": "neutral",
            "status": "prompt_skipped",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            repo_dir = workspace_root / "source" / "demo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "README.md").write_text("build docs\n", encoding="utf-8")

            fake_exec = {
                "stage": "build_app",
                "ok": True,
                "steps": [{"ok": True, "command": "cmake -S . -B build"}],
                "effective_commands": ["cmake -S . -B build"],
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "1"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in payloads]), patch.object(
                goose_pipeline,
                "execute_pipeline_stage",
                return_value=fake_exec,
            ) as execute_stage, patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                result = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    repo_dir=str(repo_dir),
                    venv_dir=str(workspace_root / "venv"),
                    trace_dir=str(workspace_root / "traces" / "run1"),
                    post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                    compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                    analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                    progress=False,
                    feedback_prompt=False,
                )

            self.assertTrue(execute_stage.called)
            self.assertIn("test_default_build_setup", result["executions"])
            self.assertTrue(result["executions"]["test_default_build_setup"]["ok"])

    def test_build_setup_execution_failure_raises_with_artifact_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]
        skipped_feedback = {
            "prompt": "review",
            "prompted": False,
            "what_worked": "",
            "what_did_not_work": "",
            "next_time": "",
            "rating": "neutral",
            "status": "prompt_skipped",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            repo_dir = workspace_root / "source" / "demo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "README.md").write_text("build docs\n", encoding="utf-8")

            failing_exec = {
                "stage": "build_app",
                "ok": False,
                "steps": [
                    {
                        "ok": False,
                        "command": "cmake -S . -B build",
                        "cwd": str(repo_dir),
                        "returncode": 1,
                        "stdout": "",
                        "stderr": "boom",
                    }
                ],
                "failed_step": {"command": "cmake -S . -B build", "stderr": "boom"},
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "1"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in payloads]), patch.object(
                goose_pipeline,
                "execute_pipeline_stage",
                return_value=failing_exec,
            ), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                with self.assertRaisesRegex(RuntimeError, r"execution failed at test_default_build_setup.*execution_steps\.log"):
                    goose_pipeline.run_terminal_goose_pipeline(
                        root=repo_root,
                        name="demo",
                        repo_url="https://example.invalid/demo.git",
                        repo_ref="main",
                        language="cpp",
                        workspace_root=str(workspace_root),
                        repo_dir=str(repo_dir),
                        venv_dir=str(workspace_root / "venv"),
                        trace_dir=str(workspace_root / "traces" / "run1"),
                        post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                        compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                        analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                        progress=False,
                        feedback_prompt=False,
                    )

    def test_build_setup_execution_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {"DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False):
            self.assertFalse(goose_pipeline._stage_should_execute("test_default_build_setup"))


class GooseTerminalPipelineIntegrationTests(unittest.TestCase):
    def test_terminal_runner_invokes_goose_stage_recipes_with_ior_defaults(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_goose = tmp_path / "fake-goose"
            invocations_log = tmp_path / "invocations.jsonl"

            fake_goose.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import sys

                    log_path = pathlib.Path(os.environ["DFTRACER_GOOSE_INVOCATIONS"])
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(sys.argv[1:]) + "\\n")

                    if "info" in sys.argv[1:]:
                        print("fake goose info")
                        raise SystemExit(0)
                    recipe = pathlib.Path(sys.argv[sys.argv.index("--recipe") + 1]).name
                    payloads = json.loads(os.environ["DFTRACER_GOOSE_PAYLOADS_JSON"])
                    print(json.dumps(payloads[recipe]))
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake_goose.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_BASE_URL": "https://example.invalid/v1",
                    "OPENAI_MODEL": "test-model",
                    "DFTRACER_GOOSE_BIN": str(fake_goose),
                    "DFTRACER_GOOSE_INVOCATIONS": str(invocations_log),
                    "DFTRACER_GOOSE_PAYLOADS_JSON": json.dumps(PAYLOAD_BY_RECIPE),
                    "DFTRACER_GOOSE_FEEDBACK_PROMPT": "0",
                    "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0",
                }
            )

            result = subprocess.run(
                ["bash", str(repo_root / "scripts" / "run_goose_pipeline.sh")],
                cwd=str(repo_root),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("[goose-pipeline] feedback_db:", result.stderr)
            self.assertIn("[goose-pipeline][detect] command:", result.stderr)
            self.assertIn("[goose-pipeline][detect] using local heuristic fast-path", result.stderr)
            self.assertIn('"detect": {', result.stdout)
            self.assertIn('"dfanalyzer": {', result.stdout)

            lines = invocations_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 7)

            info_argv = json.loads(lines[0])
            self.assertIn("info", info_argv)

            argv = json.loads(lines[1])
            self.assertIn("run", argv)
            self.assertIn("--with-builtin", argv)
            self.assertIn("summon", argv)
            self.assertIn("--recipe", argv)
            self.assertIn(str(repo_root / "goose" / "recipes" / "subrecipes" / "20_build_setup_stage.yaml"), argv)
            self.assertIn("name=ior", argv)
            self.assertIn("repo_url=https://github.com/hpc/ior", argv)
            self.assertIn("repo_ref=4.0.0", argv)
            self.assertIn("language=cpp", argv)
            self.assertIn(f"repo_dir={repo_root / 'workspaces' / 'ior' / 'source' / 'ior'}", argv)

    def test_terminal_runner_maps_livai_env_for_python_entrypoint(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_goose = tmp_path / "fake-goose"
            invocations_log = tmp_path / "invocations.jsonl"

            fake_goose.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import json
                    import os
                    import pathlib
                    import sys

                    log_path = pathlib.Path(os.environ["DFTRACER_GOOSE_INVOCATIONS"])
                    payload = {
                        "argv": sys.argv[1:],
                        "env": {
                            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
                            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
                            "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", ""),
                            "GOOSE_PROVIDER": os.environ.get("GOOSE_PROVIDER", ""),
                            "GOOSE_MODEL": os.environ.get("GOOSE_MODEL", ""),
                        },
                    }
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload) + "\\n")

                    if "info" in sys.argv[1:]:
                        print("fake goose info")
                        raise SystemExit(0)
                    recipe = pathlib.Path(sys.argv[sys.argv.index("--recipe") + 1]).name
                    payloads = json.loads(os.environ["DFTRACER_GOOSE_PAYLOADS_JSON"])
                    print(json.dumps(payloads[recipe]))
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            fake_goose.chmod(0o755)

            env = os.environ.copy()
            env.pop("OPENAI_BASE_URL", None)
            env.pop("OPENAI_API_KEY", None)
            env.pop("OPENAI_MODEL", None)
            env.pop("GOOSE_PROVIDER", None)
            env.pop("GOOSE_MODEL", None)
            env.update(
                {
                    "LIVAI_BASE_URL": "https://livai-api.llnl.gov/v1",
                    "LIVAI_API_KEY": "livai-test-key",
                    "LIVAI_MODEL": "gpt-5.4",
                    "DFTRACER_GOOSE_BIN": str(fake_goose),
                    "DFTRACER_GOOSE_INVOCATIONS": str(invocations_log),
                    "DFTRACER_GOOSE_PAYLOADS_JSON": json.dumps(PAYLOAD_BY_RECIPE),
                    "DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS": "1",
                    "DFTRACER_GOOSE_FEEDBACK_PROMPT": "0",
                    "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0",
                }
            )

            result = subprocess.run(
                ["bash", str(repo_root / "scripts" / "run_goose_pipeline.sh")],
                cwd=str(repo_root),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("[goose-pipeline] environment: OPENAI_BASE_URL=set", result.stderr)
            self.assertIn("OPENAI_MODEL=gpt-5.4", result.stderr)

            first_call = json.loads(invocations_log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(first_call["env"]["OPENAI_BASE_URL"], "https://livai-api.llnl.gov/v1")
            self.assertTrue(first_call["env"]["OPENAI_API_KEY"])
            self.assertEqual(first_call["env"]["OPENAI_MODEL"], "gpt-5.4")
            self.assertEqual(first_call["env"]["GOOSE_PROVIDER"], "openai")
            self.assertEqual(first_call["env"]["GOOSE_MODEL"], "gpt-5.4")

    def test_feedback_memory_is_persisted_and_injected_into_later_runs(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            feedback_db = Path(tmp_dir) / "feedback.json"
            workspace_root = Path(tmp_dir) / "workspace"

            first_feedback = {
                "prompt": "review",
                "prompted": True,
                "what_worked": "CMake guess was correct",
                "what_did_not_work": "Need stronger MPI note",
                "next_time": "Prefer MPI-aware defaults",
                "rating": "mixed",
                "status": "recorded",
            }
            skipped_feedback = {
                "prompt": "review",
                "prompted": False,
                "what_worked": "",
                "what_did_not_work": "",
                "next_time": "",
                "rating": "neutral",
                "status": "prompt_skipped",
            }
            feedbacks = [first_feedback] + [skipped_feedback] * 6

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in payloads]), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=feedbacks,
            ):
                goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    progress=False,
                    feedback_db_path=str(feedback_db),
                    feedback_prompt=True,
                )

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in payloads]), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                second = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    progress=False,
                    feedback_db_path=str(feedback_db),
                    feedback_prompt=False,
                )

            stored = json.loads(feedback_db.read_text(encoding="utf-8"))
            detect_memory = stored["stage_memory"]["detect"][-1]
            self.assertEqual(detect_memory["what_worked"], "CMake guess was correct")
            self.assertEqual(detect_memory["next_time"], "Prefer MPI-aware defaults")
            prior_feedback = second["stage_inputs"]["detect"]["feedback_context"]["prior_feedback"]
            self.assertEqual(prior_feedback[-1]["what_worked"], "CMake guess was correct")
            self.assertTrue(second["feedback_db"].endswith("feedback.json"))

    def test_pipeline_uses_workspace_local_context_and_stage_cwd(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            captured = {}

            def fake_run(stage_name: str, **kwargs):
                captured.setdefault("calls", []).append({
                    "stage": stage_name,
                    "cwd": kwargs["cwd"],
                    "env": dict(kwargs["env"]),
                })
                return payloads[len(captured["calls"]) - 1], 0.1

            skipped_feedback = {
                "prompt": "review",
                "prompted": False,
                "what_worked": "",
                "what_did_not_work": "",
                "next_time": "",
                "rating": "neutral",
                "status": "prompt_skipped",
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=fake_run), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                result = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    repo_dir=str(workspace_root / "source" / "demo"),
                    venv_dir=str(workspace_root / "venv"),
                    trace_dir=str(workspace_root / "traces" / "run1"),
                    post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                    compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                    analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                    progress=False,
                    feedback_prompt=False,
                )

            self.assertTrue(Path(result["context_file"]).resolve().is_relative_to((workspace_root / ".goose" / "pipeline_contexts").resolve()))
            self.assertEqual(Path(captured["calls"][0]["cwd"]).resolve(), workspace_root.resolve())
            self.assertEqual(captured["calls"][0]["stage"], "test_default_build_setup")
            self.assertEqual(Path(captured["calls"][0]["env"]["DFTRACER_WORKSPACE_ROOT"]).resolve(), workspace_root.resolve())
            self.assertIn("/usr/bin", captured["calls"][0]["env"]["PATH"])
            self.assertIn("/bin", captured["calls"][0]["env"]["PATH"])
            self.assertEqual(result["stages"]["detect"]["build_system"], "unknown")
    def test_local_detect_collects_all_repo_languages(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        repo_payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            repo_dir = workspace_root / "source" / "demo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "main.cpp").write_text("#include <mpi.h>\nint main(){return 0;}\n", encoding="utf-8")
            (repo_dir / "driver.py").write_text("print('hello')\n", encoding="utf-8")
            (repo_dir / "helper.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            (repo_dir / "configure.ac").write_text("AC_INIT([demo],[1.0])\n", encoding="utf-8")
            (repo_dir / "Makefile.am").write_text("bin_PROGRAMS = demo\n", encoding="utf-8")

            skipped_feedback = {
                "prompt": "review",
                "prompted": False,
                "what_worked": "",
                "what_did_not_work": "",
                "next_time": "",
                "rating": "neutral",
                "status": "prompt_skipped",
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in repo_payloads]), patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                result = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    repo_dir=str(repo_dir),
                    venv_dir=str(workspace_root / "venv"),
                    trace_dir=str(workspace_root / "traces" / "run1"),
                    post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                    compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                    analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                    progress=False,
                    feedback_prompt=False,
                )

            self.assertEqual(result["stages"]["detect"]["language"], "cpp")
            self.assertEqual(result["stages"]["detect"]["languages_used"], ["build", "cpp", "python", "shell"])
            self.assertIn("Languages detected across the repository", " ".join(result["stages"]["detect"]["notes"]))

    def test_local_detect_enables_mpi_without_goose_stage(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        repo_payloads = [
            BUILD_SETUP_PAYLOAD,
            DEFAULT_RUN_PAYLOAD,
            ANNOTATE_PAYLOAD,
            REBUILD_PAYLOAD,
            POSTPROCESS_PAYLOAD,
            DFANALYZER_PAYLOAD,
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            repo_dir = workspace_root / "source" / "demo"
            repo_dir.mkdir(parents=True, exist_ok=True)
            (repo_dir / "CMakeLists.txt").write_text("find_package(MPI REQUIRED)\nadd_executable(demo main.cpp)\n", encoding="utf-8")
            (repo_dir / "main.cpp").write_text("#include <mpi.h>\nint main(){return 0;}\n", encoding="utf-8")

            skipped_feedback = {
                "prompt": "review",
                "prompted": False,
                "what_worked": "",
                "what_did_not_work": "",
                "next_time": "",
                "rating": "neutral",
                "status": "prompt_skipped",
            }

            with patch.dict(os.environ, {"GOOSE_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.invalid/v1", "OPENAI_MODEL": "test-model", "DFTRACER_GOOSE_EXECUTE_BUILD_SETUP": "0"}, clear=False), patch.object(goose_pipeline, "_preflight_goose_runtime", return_value={"provider": "openai", "model": "test-model"}), patch.object(goose_pipeline, "_run_goose_stage", side_effect=[(payload, 0.1) for payload in repo_payloads]) as stage_runner, patch.object(
                goose_pipeline,
                "collect_stage_feedback",
                side_effect=[skipped_feedback] * len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER),
            ):
                result = goose_pipeline.run_terminal_goose_pipeline(
                    root=repo_root,
                    name="demo",
                    repo_url="https://example.invalid/demo.git",
                    repo_ref="main",
                    language="cpp",
                    workspace_root=str(workspace_root),
                    repo_dir=str(repo_dir),
                    venv_dir=str(workspace_root / "venv"),
                    trace_dir=str(workspace_root / "traces" / "run1"),
                    post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                    compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                    analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                    progress=False,
                    feedback_prompt=False,
                )

            self.assertEqual(stage_runner.call_count, len(goose_pipeline.GOOSE_PIPELINE_STAGE_ORDER) - 1)
            self.assertEqual(result["stages"]["detect"]["build_system"], "cmake")
            self.assertTrue(result["stages"]["detect"]["uses_mpi"])
            self.assertEqual(result["stages"]["detect"]["dftracer_flags"]["DFTRACER_ENABLE_MPI"], "ON")


    def test_workspace_guard_rejects_absolute_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            env = {
                "PATH": "/usr/bin:/bin",
                "DFTRACER_WORKSPACE_ROOT": str(workspace_root),
                "DFTRACER_ALLOW_ONLY_WORKSPACE": "1",
            }
            result = run_shell_command("cat /etc/passwd", cwd=str(workspace_root), env=env)
            self.assertFalse(result["ok"])
            self.assertIn("outside workspace_root", result["stderr"])


    def test_pipeline_fails_fast_when_openai_model_missing(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["GOOSE_PROVIDER"] = "openai"
            env["OPENAI_API_KEY"] = "test-key"
            env["OPENAI_BASE_URL"] = "https://example.invalid/v1"
            env.pop("GOOSE_MODEL", None)
            env.pop("OPENAI_MODEL", None)
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RuntimeError, "no model is configured"):
                    goose_pipeline.run_terminal_goose_pipeline(
                        root=repo_root,
                        name="demo",
                        repo_url="https://example.invalid/demo.git",
                        repo_ref="main",
                        language="cpp",
                        workspace_root=str(workspace_root),
                        repo_dir=str(workspace_root / "source" / "demo"),
                        venv_dir=str(workspace_root / "venv"),
                        trace_dir=str(workspace_root / "traces" / "run1"),
                        post_dir=str(workspace_root / "artifacts" / "run1" / "post"),
                        compacted_trace_dir=str(workspace_root / "artifacts" / "run1" / "post" / "compacted"),
                        analysis_dir=str(workspace_root / "artifacts" / "run1" / "analysis"),
                        progress=False,
                        feedback_prompt=False,
                    )



if __name__ == "__main__":
    unittest.main()
