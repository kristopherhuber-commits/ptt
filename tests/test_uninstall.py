"""
The uninstaller: `_internal/uninstall.ps1`, the Installed apps entry
`install.ps1` writes for it, and the Advanced tab's guard on which copy may
offer it.

The script itself was exercised against a scratch tree (its path parameters
exist for that); what is checked here is the wiring a later edit could break
without anything else noticing.
"""

import os

from ptt import paths

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(REPO, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_the_installer_copies_the_uninstaller_and_registers_it():
    """
    The Installed apps entry runs whatever `UninstallString` names, so the
    script must be in the installation and the entry must point at it.
    """
    text = read("_internal", "install.ps1")
    assert r'"$SourceDir\_internal\uninstall.ps1"' in text
    assert r"CurrentVersion\Uninstall\PTTDictation" in text
    assert '"UninstallString"' in text
    assert r"\_internal\uninstall.ps1" in text


def test_the_uninstaller_removes_the_same_entry_and_keeps_the_vocabulary():
    text = read("_internal", "uninstall.ps1")
    assert r"CurrentVersion\Uninstall\PTTDictation" in text
    assert "models--*--faster-whisper-*" in text
    # huggingface_hub 1.x's shared store: the weights themselves, which 3.2.0
    # left behind after deleting only the folders that point at them.
    assert '"*.refs"' in text
    assert "agent-configs" not in text.split("param(")[1].split(")")[0]


def test_uninstall_is_offered_only_from_the_installed_copy(monkeypatch, tmp_path):
    install = tmp_path / "Programs" / "ptt_dictate"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    monkeypatch.setattr(paths, "APP_DIR", str(install / "app"))
    assert paths.is_installed_copy()

    monkeypatch.setattr(paths, "APP_DIR", str(tmp_path / "git" / "ptt" / "app"))
    assert not paths.is_installed_copy()


def test_the_uninstaller_path_is_inside_the_installation(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.uninstaller_path() == os.path.join(
        str(tmp_path), "Programs", "ptt_dictate", "_internal", "uninstall.ps1")
