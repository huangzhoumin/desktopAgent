"""Unit tests for launch_app whitelist and dialog helper matching."""

from __future__ import annotations

import pytest

from desktop_agent.adapters.apps import (
    AppLauncher,
    infer_launch_app_from_goal,
    infer_launch_app_from_question,
    normalize_app_alias,
)
from desktop_agent.common.dialogs import FileDialogHelper
from desktop_agent.errors import ActionRejected


def test_launch_app_rejects_unknown():
    launcher = AppLauncher(allowed_aliases={"notepad", "excel"})
    with pytest.raises(ActionRejected):
        launcher.launch("cmd")


def test_normalize_app_alias_chinese_notepad():
    assert normalize_app_alias("记事本") == "notepad"
    assert normalize_app_alias("Notepad.exe") == "notepad"
    assert normalize_app_alias("excel") == "excel"
    assert normalize_app_alias("google") == "chrome"
    assert normalize_app_alias("谷歌浏览器") == "chrome"


@pytest.mark.parametrize(
    "question,alias",
    [
        ("未找到记事本窗口。是否需要我尝试启动记事本？", "notepad"),
        ("记事本没有打开，是否要重新尝试打开记事本？", "notepad"),
        ("记事本没有找到，是否需要我尝试其他方法来打开它？", "notepad"),
        ("未找到记事本窗口。您是否已经手动打开了记事本？", "notepad"),
        ("是否需要我为您打开记事本应用程序？", "notepad"),
        ("无法找到记事本窗口，请确认是否已手动打开记事本应用程序？", "notepad"),
        ("Should I launch notepad?", "notepad"),
        ("现在几点了？", None),
        ("姓名?", None),
    ],
)
def test_infer_launch_app_from_question(question, alias):
    assert infer_launch_app_from_question(question) == alias


def test_infer_launch_app_from_question_uses_goal():
    assert (
        infer_launch_app_from_question(
            "窗口找不到，要不要我再试一次？",
            goal="打开记事本，输入 hello",
        )
        == "notepad"
    )


def test_infer_launch_app_from_goal():
    assert infer_launch_app_from_goal("打开记事本，输入 hello") == "notepad"
    assert infer_launch_app_from_goal("随便聊聊") is None
    assert (
        infer_launch_app_from_goal(
            "google 打开 https://www.bilibili.com，在 B站顶部搜索框 填入凡人修仙传"
        )
        == "chrome"
    )


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
