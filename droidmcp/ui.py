"""Screen reading / UI dump parsing helpers.

``uiautomator dump`` produces a deeply nested XML tree.  Flattening it into a
list of nodes with absolute bounds makes it far easier for an LLM to reason
about ("tap the node whose text is 设置"), which is the whole point of this
server.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

REMOTE_DUMP = "/data/local/tmp/droidmcp_ui.xml"

# `uiautomator dump /dev/tty` is the usual one-liner, but it fails whenever the
# command does not own an interactive tty — over `adb shell` with a pipe, and
# over root/Shizuku shells in particular. Dumping to a file and reading it back
# works everywhere, so that is the path we take.
DUMP_COMMAND = (
    f"uiautomator dump {REMOTE_DUMP} >/dev/null 2>&1; "
    f"cat {REMOTE_DUMP} 2>/dev/null; "
    f"rm -f {REMOTE_DUMP} 2>/dev/null; true"
)


@dataclass
class UiNode:
    index: int
    text: str
    resource_id: str
    class_name: str
    content_desc: str
    package: str
    clickable: bool
    enabled: bool
    checkable: bool
    checked: bool
    scrollable: bool
    bounds: tuple[int, int, int, int]

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    def to_dict(self) -> dict:
        data = asdict(self)
        data["center"] = {"x": self.center[0], "y": self.center[1]}
        data["bounds"] = list(self.bounds)
        return data


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.search(raw or "")
    if not match:
        return None
    return tuple(int(g) for g in match.groups())  # type: ignore[return-value]


def _bool_attr(node: ET.Element, name: str) -> bool:
    return (node.get(name) or "").strip().lower() == "true"


def parse_dump(xml_text: str) -> list[UiNode]:
    """Flatten a uiautomator XML dump into a list of :class:`UiNode`.

    Raises ``ValueError`` when the payload is not parseable XML so callers can
    retry instead of silently returning an empty screen.
    """

    start = xml_text.find("<?xml")
    if start == -1:
        start = xml_text.find("<hierarchy")
    if start == -1:
        preview = xml_text.strip()[:200].replace("\n", " ")
        raise ValueError(
            "uiautomator produced no XML — the device may be showing a secure "
            "surface, mid-animation, or the screen is off"
            + (f" (got: {preview!r})" if preview else " (got empty output)")
        )
    payload = xml_text[start:]

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"could not parse uiautomator XML: {exc}") from exc

    nodes: list[UiNode] = []
    for elem in root.iter("node"):
        bounds = _parse_bounds(elem.get("bounds", ""))
        if bounds is None:
            continue
        if bounds[0] == bounds[2] or bounds[1] == bounds[3]:
            continue  # zero-area nodes are never tappable
        nodes.append(
            UiNode(
                index=len(nodes),
                text=(elem.get("text") or "").strip(),
                resource_id=(elem.get("resource-id") or "").strip(),
                class_name=(elem.get("class") or "").strip(),
                content_desc=(elem.get("content-desc") or "").strip(),
                package=(elem.get("package") or "").strip(),
                clickable=_bool_attr(elem, "clickable"),
                enabled=_bool_attr(elem, "enabled"),
                checkable=_bool_attr(elem, "checkable"),
                checked=_bool_attr(elem, "checked"),
                scrollable=_bool_attr(elem, "scrollable"),
                bounds=bounds,
            )
        )
    return nodes


def find_matching(nodes: list[UiNode], query: str, *, exact: bool = False) -> list[UiNode]:
    """Find nodes whose text / content-desc / resource-id matches ``query``.

    Results are ordered by how specific the match is, so the first element is
    usually the one the caller wants.
    """

    needle = query.strip().lower()
    if not needle:
        return []

    def matches(haystack: str) -> bool:
        hay = (haystack or "").lower()
        return hay == needle if exact else needle in hay

    exact_hits = [
        n for n in nodes if needle in (n.text.lower(), n.content_desc.lower())
        and (n.text.lower() == needle or n.content_desc.lower() == needle)
    ]
    text_hits = [n for n in nodes if matches(n.text)]
    desc_hits = [n for n in nodes if matches(n.content_desc)]
    id_hits = [n for n in nodes if matches(n.resource_id)]

    ordered: list[UiNode] = []
    seen: set[int] = set()
    for group in (exact_hits, text_hits, desc_hits, id_hits):
        for node in group:
            if node.index in seen:
                continue
            seen.add(node.index)
            ordered.append(node)
    return ordered


def _is_meaningful(node: UiNode) -> bool:
    """Whether a node carries anything an agent can act on.

    A node earns its place only if it has a label, is a real input target, or
    can be scrolled.  Bare layout containers — which dominate a typical
    ``uiautomator`` dump — are filtered out: on a busy home screen they account
    for roughly two thirds of the nodes and every one of them costs tokens.
    """

    if node.text or node.content_desc:
        return True
    if node.clickable and node.enabled:
        return True
    if node.scrollable:
        return True
    # An id alone is only worth reporting for things that take input.
    return bool(node.resource_id and node.class_name.endswith(("EditText", "Button", "ImageView")))


def summarize(nodes: list[UiNode], *, limit: int = 60) -> list[dict]:
    """Compact representation worth sending back to a model.

    Filters aggressively; if that leaves the screen looking empty (some apps
    render everything into unlabelled views) it relaxes the filter rather than
    reporting a blank screen the caller cannot act on.
    """

    def build(predicate) -> list[dict]:
        out: list[dict] = []
        for node in nodes:
            if not predicate(node):
                continue
            out.append(
                {
                    "i": node.index,
                    "text": node.text,
                    "desc": node.content_desc,
                    "id": node.resource_id,
                    "cls": node.class_name.rsplit(".", 1)[-1],
                    "at": {"x": node.center[0], "y": node.center[1]},
                    "clickable": node.clickable,
                    "scrollable": node.scrollable,
                }
            )
            if len(out) >= limit:
                break
        return out

    summary = build(_is_meaningful)
    if not summary:
        # Some apps render into entirely unlabelled views. Reporting an empty
        # screen would leave the caller with nothing to act on, so relax the
        # filter — but only when it produced *nothing*, never to pad a sparse
        # screen with inert containers.
        summary = build(lambda n: bool(n.resource_id or n.clickable or n.scrollable))
    return summary
