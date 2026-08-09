"""Unit tests for launch_app whitelist and dialog helper matching."""

from __future__ import annotations

import pytest

from desktop_agent.adapters.apps import AppLauncher
from desktop_agent.common.dialogs import FileDialogHelper
from desktop_agent.errors import ActionRejected


def test_launch_app_rejects_unknown():
    launcher = AppLauncher(allowed_aliases={"notepad", "excel"})
    with pytest.raises(ActionRejected):
        launcher.launch("cmd")


def test_dialog_helper_escape():
    assert FileDialogHelper._escape("a{b}") == "a{{}b{}}"
    assert FileDialogHelper._escape("x+y") == "x{+}y"


def test_download_bar_action_names_cover_save_open():
    from desktop_agent.common.dialogs import (
        DOWNLOAD_BAR_OPEN_NAMES,
        DOWNLOAD_BAR_SAVE_NAMES,
    )

    assert "保存" in DOWNLOAD_BAR_SAVE_NAMES
    assert "Save As" in DOWNLOAD_BAR_SAVE_NAMES
    assert "打开" in DOWNLOAD_BAR_OPEN_NAMES
