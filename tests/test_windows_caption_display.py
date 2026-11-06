"""Windows UI event ordering observed in packaged lecture replay."""
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop")
def test_first_translation_after_next_caption_is_visible_without_moving_mark():
    from lectureflow.windows_app import App

    app = App.__new__(App)
    app.current = app.previous = app.first_en = app.first_zh = None
    app.show_previous = False
    app.display_times = {}
    app.caption, app.mark_label = Mock(), Mock()
    app.cap = SimpleNamespace(started=time.monotonic())
    app.marked = {"b"}
    first = dict(id="a", start=0, english="first", chinese="")
    second = dict(id="b", start=6, english="second", chinese="")
    app.display_caption(first)
    app.display_caption(second)
    app.display_caption(dict(first, chinese="第一句"))
    assert app.current["id"] == "b" and app.first_zh is not None
    app.mark_label.set.assert_called_with("已标记")
    app.caption.set.assert_called_with("当前句\nsecond\n\n上一句\nfirst\n第一句")
    app.display_caption(dict(second, chinese="第二句"))
    assert "第一句" in app.caption.set.call_args.args[0]
    assert 0 <= app.display_times["a"]["chinese"] <= app.display_times["b"]["chinese"]
    app.display_caption(dict(id="c", start=12, english="third", chinese=""))
    app.display_caption(dict(first, chinese="旧回改"))
    assert "旧回改" not in app.caption.set.call_args.args[0] and app.current["id"] == "c"
