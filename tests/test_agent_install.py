"""Windows installer contract checks; only isolated config, Skill and library paths."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from xhs_context.common import atomic_json
from xhs_context.access import restrict_access
from xhs_context.storage import Store

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
CLI = Path(sys.executable).with_name("xhs-context.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL or not CLI.exists(),
                                reason="Windows PowerShell and installed CLI required")


@pytest.fixture
def installation(tmp_path):
    # No setup/native host registration: these acceptance checks only read local data.
    library = tmp_path / "已有 收藏"
    Store(library).initialize()
    skill = tmp_path / "agent skills" / "xhs-context"
    shutil.copytree(ROOT / "skills/xhs-context", skill)
    config = tmp_path / "localappdata/xhs-context/config.json"
    atomic_json(config, {"library": str(library), "cli_path": str(CLI),
                         "extension_dir": str(ROOT / "browser-extension")})
    env = {**os.environ, "LOCALAPPDATA": str(config.parent.parent),
           "CODEX_HOME": str(tmp_path / "unused codex home")}
    env.pop("XHS_CONTEXT_HOME", None)
    return {"library": library, "skill": skill, "config": config, "env": env, "root": tmp_path}


def run_installer(installation, *args, script=None):
    result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             str(script or ROOT / "scripts/install-agent.ps1"),
                             "-SkillDestination", str(installation["skill"]), *args],
                            env=installation["env"], cwd=installation["root"],
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    response = json.loads(result.stdout)
    return result, response


@pytest.mark.parametrize("restricted", [False, True])
def test_check_only_reads_existing_library_through_installed_skill(installation, restricted):
    if restricted:
        with Store(installation["library"]).write() as db:
            restrict_access(db)
    before_config = installation["config"].read_bytes()
    before_db = (installation["library"] / "library.sqlite").read_bytes()
    result, response = run_installer(installation, "-CheckOnly")
    assert result.returncode == 0, result.stdout + result.stderr
    assert response["ok"] and response["check_only"]
    assert response["library"] == str(installation["library"])
    assert response["checks"]["skill_launcher"] == "passed"
    assert response["notes"] == 0
    assert response["browser_connection"] == "not_verified"
    assert response["agent_skill_loading"] == "not_verified"
    assert bool(response["access_restriction"]) is restricted
    assert installation["config"].read_bytes() == before_config
    assert (installation["library"] / "library.sqlite").read_bytes() == before_db
    assert not (installation["config"].parent / "native-host.json").exists()


def test_check_only_missing_config_does_not_initialize_a_new_library(installation):
    installation["config"].unlink()
    result, response = run_installer(installation, "-CheckOnly")
    assert result.returncode == 1 and not response["ok"]
    assert "configuration is missing" in response["error"]["message"]
    assert not installation["config"].exists()
    assert not (installation["config"].parent / "library").exists()


def test_conflicting_environment_override_is_rejected_without_changes(installation):
    before = installation["config"].read_bytes()
    override = installation["root"] / "wrong library"
    installation["env"]["XHS_CONTEXT_HOME"] = str(override)
    result, response = run_installer(installation, "-CheckOnly")
    assert result.returncode == 1 and not response["ok"]
    assert "XHS_CONTEXT_HOME" in response["error"]["message"]
    assert installation["config"].read_bytes() == before
    assert not override.exists()


def test_missing_skill_launcher_is_a_failed_acceptance_check(installation):
    (installation["skill"] / "scripts/xhs.ps1").unlink()
    result, response = run_installer(installation, "-CheckOnly")
    assert result.returncode == 1 and not response["ok"]
    assert "launcher is missing" in response["error"]["message"]


@pytest.mark.parametrize("fail", [False, True])
def test_install_orchestration_preserves_library_and_separates_logs(installation, fail):
    # Replace only the mutating installation boundary; acceptance still runs the real CLI.
    scripts = installation["root"] / "checkout/scripts"
    scripts.mkdir(parents=True)
    script = scripts / "install-agent.ps1"
    shutil.copyfile(ROOT / "scripts/install-agent.ps1", script)
    (scripts / "install.ps1").write_text(
        "param([string]$Library, [string]$SkillDestination)\n"
        "$config = Get-Content (Join-Path $env:LOCALAPPDATA 'xhs-context/config.json') -Raw -Encoding UTF8 | ConvertFrom-Json\n"
        "if ($Library -ne $config.library) { throw 'existing library was not retained' }\n"
        "if (-not (Test-Path -LiteralPath $SkillDestination)) { throw 'Skill destination was not forwarded' }\n"
        "Write-Host 'installation log'\n"
        "& $env:ComSpec /d /c 'echo native progress 1>&2'\n" + ("throw 'simulated setup failure'\n" if fail else ""),
        encoding="utf-8")
    result, response = run_installer(installation, script=script)
    assert "installation log" in result.stderr
    assert "native progress" in result.stderr
    assert response["ok"] is (not fail)
    assert result.returncode == (1 if fail else 0)
    if fail:
        assert "Installation failed" in response["error"]["message"]
        assert "simulated setup failure" in result.stderr
    else:
        assert response["library"] == str(installation["library"])
        assert not response["check_only"]
