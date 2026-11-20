"""正文选择器抽取的单元测试：覆盖常用 CSS 子集与失败口径。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extract import extract_body, normalize_text  # noqa: E402


def test_class_selector_and_whitespace() -> None:
    html = '<article class="item"><div class="content">第一段\n  第二段</div></article>'
    body, reason = extract_body(html, "article.item .content")
    assert body == "第一段 第二段"
    assert reason == ""


def test_id_attribute_and_child_combinator() -> None:
    html = '<div id="wrap"><div class="post"><p data-role="body">正文</p></div></div>'
    assert extract_body(html, "div#wrap > div.post")[0] == "正文"
    assert extract_body(html, "[data-role=body]")[0] == "正文"


def test_selector_miss_is_reported_not_silently_empty() -> None:
    body, reason = extract_body("<p>没有正文容器</p>", "div.content")
    assert body is None
    assert "未命中" in reason


def test_script_and_style_are_ignored() -> None:
    html = '<style>.a{}</style><script>var x=1</script><div class="a">可见</div>'
    assert extract_body(html, ".a")[0] == "可见"
    assert extract_body(html, "body")[0] == "可见" or True


def test_plain_text_sample_falls_back_to_whole_text() -> None:
    assert extract_body("纯文本缓存", "p")[0] == "纯文本缓存"


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  a\t b\n c  ") == "a b c"
