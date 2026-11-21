"""No editable text field may exist while the popup is closed.

A Qt editable text input watches the clipboard (to know whether it could
paste), so one that lives on while the popup is closed has the shell read
the clipboard each time another Wayland app copies, and a stuck transfer
can stall the shell. Every TextField, TextInput, TextEdit and TextArea, and
every plugin component that holds one, must sit inside a Loader whose
`active` depends on `opened`.

The QML is read as text: comments and strings are blanked, then each
`Type {` opens a block and each `}` closes one.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from helpers import ROOT

EDITABLE = {"TextField", "TextInput", "TextEdit", "TextArea"}


def _blank(source: str) -> str:
    """Comments and string literals as spaces, so their braces do not count."""
    pattern = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`', re.S)
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)


class Block:
    def __init__(self, kind: str | None, start: int, parent: "Block | None"):
        self.kind, self.start, self.end, self.parent = kind, start, None, parent


def _blocks(text: str) -> list[Block]:
    """Every brace block; `kind` is the QML type before it, or None."""
    blocks, stack = [], []
    for match in re.finditer(r"[{}]", text):
        if match.group() == "{":
            before = re.search(r"([A-Za-z_][\w.]*)\s*$", text[: match.start()])
            name = before.group(1).split(".")[-1] if before else ""
            kind = name if name[:1].isupper() else None
            block = Block(kind, match.start(), stack[-1] if stack else None)
            blocks.append(block)
            stack.append(block)
        elif stack:
            stack.pop().end = match.end()
    return blocks


def _own_property(text: str, block: Block, name: str) -> str | None:
    """A property set directly on the block (not on a child), to line end."""
    depth = 0
    i = block.start + 1
    while i < block.end - 1:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            found = re.match(rf"\b{name}\s*:\s*([^\n]*)", text[i:])
            if found and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "._")):
                return found.group(1)
        i += 1
    return None


def _gated(text: str, block: Block) -> bool:
    node = block.parent
    while node is not None:
        if node.kind == "Loader":
            active = _own_property(text, node, "active")
            if active is not None and re.search(r"\bopened\b", active):
                return True
        node = node.parent
    return False


def ungated(files: dict[str, str]) -> list[str]:
    """Each `file: Type` holding an editable field with no gate above it."""
    texts = {name: _blank(source) for name, source in files.items()}
    parsed = {name: _blocks(text) for name, text in texts.items()}

    def open_blocks(kinds):
        # A file's root block of its own name is an imported type (the
        # host's Panel in Panel.qml), never the file itself.
        return [
            (name, block)
            for name, blocks in parsed.items()
            for block in blocks
            if block.kind in kinds and block.kind != Path(name).stem
            and not _gated(texts[name], block)
        ]

    risky = set(EDITABLE)
    # A plugin component with an ungated field inside is itself risky
    # wherever it is used; repeat until nothing new turns up.
    while True:
        more = {Path(name).stem for name, _ in open_blocks(risky)} - risky
        if not more:
            break
        risky |= more
    uses = {}
    for name, blocks in parsed.items():
        for block in blocks:
            if block.kind in risky - EDITABLE and block.kind != Path(name).stem:
                uses.setdefault(block.kind, []).append(_gated(texts[name], block))
    # A component whose every use is gated may hold fields of its own.
    safe = {kind for kind, gates in uses.items() if all(gates)}
    return [f"{name}: {block.kind}" for name, block in open_blocks(risky)
            if Path(name).stem not in safe]


class TestNoFieldWhileClosed(unittest.TestCase):
    def test_every_text_field_is_loaded_only_while_open(self):
        files = {
            str(path.relative_to(ROOT)): path.read_text()
            for path in sorted(ROOT.rglob("*.qml"))
            if ".git" not in path.parts
        }
        self.assertTrue(files)
        self.assertEqual(ungated(files), [])

    def test_the_guard_finds_fields(self):
        bare = "Item {\n  TextField { text: \"{\" }\n}\n"
        self.assertEqual(ungated({"A.qml": bare}), ["A.qml: TextField"])
        always = "Item {\n  Loader {\n    active: true\n    sourceComponent: TextInput {}\n  }\n}\n"
        self.assertEqual(ungated({"A.qml": always}), ["A.qml: TextInput"])
        gated = ("Item {\n  Loader {\n    active: root.opened && root.editing\n"
                 "    sourceComponent: Component { TextEdit {} }\n  }\n}\n")
        self.assertEqual(ungated({"A.qml": gated}), [])
        # The gate must be the Loader's own, not a child's.
        child = "Item {\n  Loader {\n    sourceComponent: Item { property bool active: opened\n TextArea {} }\n  }\n}\n"
        self.assertEqual(ungated({"A.qml": child}), ["A.qml: TextArea"])
        # A component holding a field is checked where it is used.
        card = "ColumnLayout {\n  TextField {}\n}\n"
        self.assertEqual(ungated({"Card.qml": card, "B.qml": "Item {\n  Card {}\n}\n"}),
                         ["Card.qml: TextField", "B.qml: Card"])
        used = "Item {\n  Loader {\n    active: opened\n    sourceComponent: Card {}\n  }\n}\n"
        self.assertEqual(ungated({"Card.qml": card, "B.qml": used}), [])


if __name__ == "__main__":
    unittest.main()
