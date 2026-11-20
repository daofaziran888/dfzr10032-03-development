"""按采集规则的正文选择器，从缓存页面里抽出纯文本正文。

只依赖标准库：用 html.parser 建一棵很轻的节点树，再支持一个够用的 CSS 选择器子集
（标签、#id、.class、[属性]、后代/子元素组合、逗号分组）。正文选择器没命中时明确失败，
由调用方把页面标记成抽取失败，而不是悄悄交付空正文。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "head"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["Node | str"] = []
        self.parent = parent

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        """节点内全部可见文本，脚本/样式不计入。迭代遍历，避免恶意深嵌套撑爆递归。"""
        chunks: list[str] = []
        stack: list["Node | str"] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                chunks.append(item)
                continue
            if item.tag in SKIP_TEXT_TAGS:
                continue
            stack.extend(reversed(item.children))
        return "".join(chunks)

    def iter_elements(self) -> Iterable["Node"]:
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            for child in reversed(node.children):
                if isinstance(child, Node):
                    stack.append(child)
            if node is not self:
                yield node


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self._stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(tag, {key.lower(): value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(tag, {key.lower(): value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


_ATTR_PATTERN = re.compile(
    r"""\[\s*(?P<name>[A-Za-z_:][-\w:.]*)\s*(?:(?P<op>[~^$*|]?=)\s*(?P<value>"[^"]*"|'[^']*'|[^\]]*?)\s*)?\]"""
)
_PART = re.compile(r"""[#.][-\w]+|\[[^\]]*\]|:[-\w()]+""")


def _split_groups(selector: str) -> list[str]:
    """按顶层逗号拆分选择器分组，忽略属性值里的逗号。"""
    groups: list[str] = []
    depth = 0
    quote = ""
    current: list[str] = []
    for char in selector:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ('"', "'"):
            quote = char
            current.append(char)
        elif char == "[":
            depth += 1
            current.append(char)
        elif char == "]":
            depth = max(depth - 1, 0)
            current.append(char)
        elif char == "," and depth == 0:
            groups.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        groups.append(tail)
    return [group for group in groups if group]


def _parse_simple(part: str) -> dict[str, object]:
    simple: dict[str, object] = {"tag": None, "id": None, "classes": [], "attrs": []}
    part = part.strip()
    tag_match = re.match(r"^(\*|[A-Za-z][-\w]*)", part)
    if tag_match:
        tag = tag_match.group(1)
        simple["tag"] = None if tag == "*" else tag.lower()
        part = part[tag_match.end():]
    for token in _PART.findall(part):
        if token.startswith("#"):
            simple["id"] = token[1:]
        elif token.startswith("."):
            classes = simple["classes"]
            assert isinstance(classes, list)
            classes.append(token[1:])
        elif token.startswith("["):
            match = _ATTR_PATTERN.match(token)
            if match:
                value = match.group("value") or ""
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                attrs = simple["attrs"]
                assert isinstance(attrs, list)
                attrs.append((match.group("name").lower(), match.group("op"), value))
        # 伪类（:first-child 等）不支持，直接忽略，避免把整条规则判死
    return simple


def _parse_group(group: str) -> list[tuple[str, dict[str, object]]]:
    """把一组复杂选择器拆成 [(combinator, simple), ...]，最左项 combinator 为 ' '。"""
    tokens = re.split(r"\s*(>)\s*|\s+", group.strip())
    parts: list[tuple[str, dict[str, object]]] = []
    pending_child = False
    for token in tokens:
        if token is None or token == "":
            continue
        if token == ">":
            pending_child = True
            continue
        combinator = ">" if pending_child else " "
        parts.append((combinator, _parse_simple(token)))
        pending_child = False
    return parts


def _attr_matches(node: Node, name: str, op: str | None, expected: str) -> bool:
    actual = node.attrs.get(name)
    if actual is None:
        return False
    if not op:
        return True
    if op == "=":
        return actual == expected
    if op == "~=":
        return expected in actual.split()
    if op == "^=":
        return actual.startswith(expected)
    if op == "$=":
        return actual.endswith(expected)
    if op == "*=":
        return expected in actual
    if op == "|=":
        return actual == expected or actual.startswith(expected + "-")
    return False


def _simple_matches(node: Node, simple: dict[str, object]) -> bool:
    tag = simple.get("tag")
    if tag and node.tag != tag:
        return False
    expected_id = simple.get("id")
    if expected_id and node.attrs.get("id") != expected_id:
        return False
    classes = simple.get("classes") or []
    if classes and not set(classes).issubset(node.classes):
        return False
    for name, op, value in simple.get("attrs") or []:
        if not _attr_matches(node, name, op, value):
            return False
    return True


def _matches_group(node: Node, parts: list[tuple[str, dict[str, object]]], index: int) -> bool:
    if not _simple_matches(node, parts[index][1]):
        return False
    if index == 0:
        return True
    combinator = parts[index][0]
    if combinator == ">":
        parent = node.parent
        return parent is not None and parent.tag != "#document" and _matches_group(parent, parts, index - 1)
    ancestor = node.parent
    while ancestor is not None and ancestor.tag != "#document":
        if _matches_group(ancestor, parts, index - 1):
            return True
        ancestor = ancestor.parent
    return False


def select_nodes(root: Node, selector: str) -> list[Node]:
    groups = [_parse_group(group) for group in _split_groups(selector)]
    groups = [group for group in groups if group]
    if not groups:
        return []
    matched: list[Node] = []
    for node in root.iter_elements():
        if any(_matches_group(node, group, len(group) - 1) for group in groups):
            matched.append(node)
    return matched


def normalize_text(text: str) -> str:
    """统一空白，保证同一段正文在不同缓存里比较时不受排版影响。"""
    return re.sub(r"\s+", " ", text).strip()


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def extract_body(content: str, selector: str | None) -> tuple[str | None, str]:
    """返回 (正文, 失败原因)。正文选择器未命中或结果为空时正文为 None。"""
    content = content or ""
    selector = (selector or "").strip()
    root = parse_html(content)
    elements = list(root.iter_elements())
    if not elements:
        # 缓存样本本身就是纯文本时，选择器无从匹配，直接把整段作为正文
        text = normalize_text(content)
        return (text, "") if text else (None, "缓存样本没有正文")

    if selector:
        matched = select_nodes(root, selector)
        if not matched:
            return None, f"正文选择器「{selector}」未命中"
        text = normalize_text("\n".join(node.text() for node in matched))
    else:
        text = normalize_text(root.text())
    if not text:
        return None, "正文选择器命中但正文为空"
    return text, ""
