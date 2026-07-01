"""scndui - compact MPQ-backed SCNDgram UI/UX init JSON helpers."""

from __future__ import annotations

import base64
import json
import keyword
import os
import secrets
import struct
import sys

import eudplib as ep

UIUX_INIT_MPQ_PATH = "uiux_init.json"
MAKER_IMAGE_ID_OFFSET = 10000
DEFAULT_BASE_WIDTH = 1600
DEFAULT_BASE_HEIGHT = 1200
VARIABLE_TYPES = ("int32", "int64", "double", "string")
UIUX_MEMORY_VARIABLE_MAGIC = b"SCUXVAR1"
UIUX_MEMORY_REFERENCE_MAGIC = b"SCUXREF1"
UIUX_MEMORY_BIND_MAGIC = b"SCUXBND1"
UIUX_MEMORY_DIRECTORY_MAGIC = b"SCUXDIR1"
UIUX_MEMORY_LOCAL_PLAYER_MAGIC = b"SCUXLCP1"
UIUX_MEMORY_VARIABLE_VERSION = 1
UIUX_MEMORY_BIND_VERSION = 2
UIUX_MEMORY_DIRECTORY_VERSION = 1
UIUX_MEMORY_LOCAL_PLAYER_VERSION = 1
UIUX_MEMORY_VARIABLE_HEADER_DWORDS = 9
UIUX_MEMORY_DIRECTORY_HEADER_DWORDS = 6
UIUX_MEMORY_DIRECTORY_ENTRY_DWORDS = 3
UIUX_MEMORY_DIRECTORY_KIND_VARIABLE = 1
UIUX_MEMORY_DIRECTORY_KIND_REFERENCE = 2
UIUX_MEMORY_DIRECTORY_KIND_BIND = 3
UIUX_MEMORY_DIRECTORY_KIND_COMMAND = 4
UIUX_MEMORY_DIRECTORY_KIND_LOCAL_PLAYER = 5
UIUX_MEMORY_BIND_SCOPE_PLAYER = 0
UIUX_MEMORY_BIND_SCOPE_GLOBAL = 1
_UIUX_MEMORY_VARIABLE_TYPE_CODES = {
    "int32": 1,
    "int64": 2,
    "double": 3,
    "string": 4,
}

ANCHOR_TOP_LEFT = "tl"
ANCHOR_TOP = "t"
ANCHOR_TOP_RIGHT = "tr"
ANCHOR_LEFT = "l"
ANCHOR_RIGHT = "r"
ANCHOR_BOTTOM_LEFT = "bl"
ANCHOR_BOTTOM = "b"
ANCHOR_BOTTOM_RIGHT = "br"
ANCHOR_CENTER = "c"
TL = ANCHOR_TOP_LEFT
T = ANCHOR_TOP
TR = ANCHOR_TOP_RIGHT
L = ANCHOR_LEFT
R = ANCHOR_RIGHT
BL = ANCHOR_BOTTOM_LEFT
B = ANCHOR_BOTTOM
BR = ANCHOR_BOTTOM_RIGHT
CENTER = ANCHOR_CENTER

ACTION_HIDE_SELF = "hide_self"
ACTION_SHOW_ALL = "show_all"
_ACTION_DELIMITER_CHARS = set(":,|;\r\n")


def _action_id(value, name):
    text = str(value).strip()
    if not text:
        raise ValueError(f"scndui {name} must not be empty")
    if any(ch in text for ch in _ACTION_DELIMITER_CHARS):
        raise ValueError(f"scndui {name} must not contain action delimiters (: , | ; or newline)")
    return text

THEME_WINDOW = "window"
THEME_PANEL = "panel"
THEME_PASS = "pass"
THEME_PRIMARY = "primary"
THEME_DANGER = "danger"
THEME_SUCCESS = "success"
THEME_ACCENT = "accent"
THEME_GHOST = "ghost"

_CURRENT_DOC = None
_IMAGE_BASE_PATH = ""
_MEMORY_VARIABLES = {}
_MEMORY_BIND_BLOCKS = {}
_MEMORY_DIRECTORY_ENTRIES = []
_MEMORY_DIRECTORY_BLOCK = None
_UIUX_LOCAL_PLAYER_BLOCK = None

_SPEC_KEY_ALIASES = {
    "kind": "type",
    "input": "type",
    "i": "id",
    "name": "id",
    "to": "parent",
    "in": "parent",
    "a": "anchor",
    "rt": "reference_target",
    "ref": "reference_target",
    "ref_to": "reference_target",
    "relative_to": "reference_target",
    "reference": "reference_target",
    "reference_target": "reference_target",
    "re": "reference_edge",
    "edge": "reference_edge",
    "ref_edge": "reference_edge",
    "relative_edge": "reference_edge",
    "reference_edge": "reference_edge",
    "width": "w",
    "height": "h",
    "t": "text",
    "k": "block_clicks",
    "block": "block_clicks",
    "blocks": "block_clicks",
    "block_click": "block_clicks",
    "star_block": "block_clicks",
    "ac": "action",
    "it": "input_type",
    "input_type": "input_type",
    "mode": "input_type",
    "pl": "placeholder",
    "placeholder": "placeholder",
    "f": "fill_color",
    "fill": "fill_color",
    "bg": "fill_color",
    "background": "fill_color",
    "o": "outline_color",
    "outline": "outline_color",
    "border": "outline_color",
    "c": "text_color",
    "color": "text_color",
    "fg": "text_color",
    "foreground": "text_color",
    "q": "opacity",
    "r": "radius",
    "z": "font_size",
    "font": "font_size",
    "font_size": "font_size",
    "an": "animation",
    "anim": "animation",
    "ad": "animation_ms",
    "duration": "animation_ms",
    "anim_ms": "animation_ms",
    "ar": "animation_direction",
    "anim_dir": "animation_direction",
    "direction": "animation_direction",
    "dir": "animation_direction",
    "ta": "align",
    "text_align": "align",
    "la": "line_alignments",
    "line_align": "line_alignments",
    "line_alignments": "line_alignments",
    "va": "valign",
    "vertical_align": "valign",
    "s": "auto_scale",
    "scale": "auto_scale",
    "autoscale": "auto_scale",
    "v": "visible",
    "show": "visible",
    "start_visible": "visible",
    "im": "image_id",
    "img": "image_id",
    "image": "image_id",
    "builtin": "builtin_image",
    "builtin_img": "builtin_image",
    "il": "image_layout",
    "image_layout": "image_layout",
    "image_mode": "image_layout",
    "img_layout": "image_layout",
    "img_mode": "image_layout",
    "ip": "image_position",
    "image_pos": "image_position",
    "img_pos": "image_position",
    "iw": "image_width",
    "image_w": "image_width",
    "img_w": "image_width",
    "ih": "image_height",
    "image_h": "image_height",
    "img_h": "image_height",
    "ix": "image_x",
    "image_x": "image_x",
    "img_x": "image_x",
    "iy": "image_y",
    "image_y": "image_y",
    "img_y": "image_y",
    "hk": "hotkey",
    "key": "hotkey",
    "ha": "hotkey_action",
    "key_action": "hotkey_action",
    "ph": "press_hotkey",
    "press_key": "press_hotkey",
    "click_key": "press_hotkey",
    "src": "path",
    "p": "path",
    "mpq": "mpq_path",
}

_THEMES = {
    THEME_WINDOW: {
        "fill_color": "#111827",
        "outline_color": "#94A3B8",
        "text_color": "#FFFFFF",
        "opacity": 86,
        "radius": 8,
        "font_size": 16,
        "animation": "slide",
        "animation_ms": 220,
        "align": "left",
        "valign": "top",
    },
    THEME_PANEL: {
        "fill_color": "#1F2937",
        "outline_color": "#334155",
        "text_color": "#D1D5DB",
        "opacity": 82,
        "radius": 6,
        "font_size": 13,
        "animation": "fade",
        "animation_ms": 160,
        "align": "center",
        "valign": "center",
    },
    THEME_PASS: {
        "fill_color": "#0F766E",
        "outline_color": "",
        "text_color": "#ECFEFF",
        "opacity": 72,
        "radius": 6,
        "font_size": 12,
        "animation": "fade",
        "animation_ms": 160,
        "align": "center",
        "valign": "center",
    },
    THEME_PRIMARY: {
        "fill_color": "#2563EB",
        "outline_color": "#BFDBFE",
        "text_color": "#FFFFFF",
        "radius": 6,
        "font_size": 13,
        "animation": "scale",
        "animation_ms": 160,
        "align": "center",
        "valign": "center",
    },
    THEME_DANGER: {
        "fill_color": "#B91C1C",
        "outline_color": "",
        "text_color": "#FFFFFF",
        "radius": 6,
        "font_size": 13,
        "animation": "scale",
        "animation_ms": 160,
        "align": "center",
        "valign": "center",
    },
    THEME_SUCCESS: {
        "fill_color": "#15803D",
        "outline_color": "",
        "text_color": "#FFFFFF",
        "radius": 6,
        "font_size": 13,
        "animation": "scale",
        "animation_ms": 160,
        "align": "center",
        "valign": "center",
    },
    THEME_ACCENT: {
        "fill_color": "#312E81",
        "outline_color": "",
        "text_color": "#EEF2FF",
        "opacity": 70,
        "radius": 8,
        "font_size": 15,
        "animation": "scale",
        "animation_ms": 180,
        "align": "center",
        "valign": "center",
    },
    THEME_GHOST: {
        "fill_color": "#0F172A",
        "outline_color": "#A7F3D0",
        "text_color": "#FFFFFF",
        "opacity": 82,
        "radius": 6,
        "font_size": 13,
        "animation": "slide",
        "animation_ms": 180,
        "align": "center",
        "valign": "center",
    },
}


def _put(doc, key, value, default=None):
    if value is None or value == default:
        return
    doc[key] = value


def _blank_to_none(value):
    if value is None:
        return None
    if isinstance(value, str) and value == "":
        return None
    return value


def _number(value):
    value = _blank_to_none(value)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    return int(number) if number.is_integer() else round(number, 4)


def _opacity(value):
    number = _number(value)
    if number is None:
        return None
    if isinstance(number, str):
        return number
    number = float(number)
    if number > 1.0:
        number = number / 100.0
    number = max(0.0, min(1.0, number))
    return int(number) if number.is_integer() else round(number, 4)


def _int(value):
    value = _blank_to_none(value)
    return None if value is None else int(value)


def _color(value):
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            if text.startswith("#"):
                hex_text = text[1:]
                if len(hex_text) == 3:
                    hex_text = "".join(ch * 2 for ch in hex_text)
                return int(hex_text, 16)
            return int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError:
            return text
    return int(value)


def _image_id(value, *, builtin=False):
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("_id", value.get("i"))
    image_id = int(value)
    if builtin or image_id >= MAKER_IMAGE_ID_OFFSET:
        return image_id
    return image_id + MAKER_IMAGE_ID_OFFSET


def _maker_image_id(value):
    image_id = _image_id(value)
    if image_id is None:
        return None
    return image_id - MAKER_IMAGE_ID_OFFSET if image_id >= MAKER_IMAGE_ID_OFFSET else image_id


def next_image_id():
    """Return the next available maker image id before the 10000 runtime offset."""
    used = set()
    doc = _doc()
    for asset in doc.get("m", []):
        image_id = _maker_image_id(asset.get("i"))
        if image_id is not None:
            used.add(image_id)
    for element in _walk_elements(doc):
        image_id = _maker_image_id(element.get("im"))
        if image_id is not None:
            used.add(image_id)
        for image_doc in element.get("ci", []) or []:
            image_id = _maker_image_id(image_doc.get("i"))
            if image_id is not None:
                used.add(image_id)

    image_id = 1
    while image_id in used:
        image_id += 1
    return image_id


def _bool(value, default=False):
    value = _blank_to_none(value)
    if value is None:
        return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("0", "false", "no", "off", "hide", "hidden"):
            return False
        if text in ("1", "true", "yes", "on", "show", "visible"):
            return True
    return bool(value)


def _spec_key(key):
    normalized = str(key).strip().lower().replace("-", "_")
    return _SPEC_KEY_ALIASES.get(normalized, normalized)


def _parse_spec(spec):
    if isinstance(spec, dict):
        return {_spec_key(key): value for key, value in spec.items()}

    parsed = {}
    for raw_part in str(spec).split(";"):
        part = raw_part.strip()
        if not part:
            continue

        if "=" not in part:
            if "type" not in parsed:
                parsed["type"] = part.strip().lower()
            continue

        key, value = part.split("=", 1)
        parsed[_spec_key(key)] = value.strip()
    return parsed


def _spec_get(spec, key, default=""):
    value = spec.get(key, default)
    return default if _blank_to_none(value) is None else value


def _theme_dict(theme_name):
    name = str(theme_name).strip().lower()
    if not name:
        return {}
    return dict(_THEMES.get(name, {}))


def action_log(message):
    """UIUX action: write a launcher debug log entry."""
    return "log:" + str(message)


def action_toggle(element_id):
    """UIUX action: toggle visibility of a panel/button by id."""
    return "toggle:" + str(element_id)


def action_show(element_id):
    """UIUX action: show a panel/button by id."""
    return "show:" + str(element_id)


def action_hide(element_id):
    """UIUX action: hide a panel/button by id."""
    return "hide:" + str(element_id)


def action_hide_self():
    """UIUX action: hide the clicked button."""
    return ACTION_HIDE_SELF


def action_show_all():
    """UIUX action: show all UIUX elements hidden by UIUX actions."""
    return ACTION_SHOW_ALL


def action_show_only(element_id, scope_id=""):
    """UIUX action: show one element and hide its siblings in the same scope."""
    action = "show_only:" + _action_id(element_id, "element_id")
    if _blank_to_none(scope_id) is not None:
        action += ":" + _action_id(scope_id, "scope_id")
    return action


def action_page(scope_id, page_id):
    """UIUX action: show a page inside a page host and remember that page."""
    return "page:" + _action_id(scope_id, "scope_id") + ":" + _action_id(page_id, "page_id")


def _encode_action_text(value):
    return str(value).replace("\\", "\\\\").replace(";", "\\;")


def _content_image_doc(image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode=""):
    doc = {"i": _image_id(image_id, builtin=bool(builtin_image))}
    _put(doc, "p", _blank_to_none(position))
    _put(doc, "w", _int(width))
    _put(doc, "h", _int(height))
    _put(doc, "x", _int(x))
    _put(doc, "y", _int(y))
    _put(doc, "m", _blank_to_none(mode))
    return doc


def _content_images(value):
    if _blank_to_none(value) is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _line_alignments(value):
    if _blank_to_none(value) is None:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    normalized = []
    for item in values:
        text = _blank_to_none(item)
        normalized.append("" if text is None else str(text))
    while normalized and not normalized[-1]:
        normalized.pop()
    return normalized


def rich(text, color="", font_size="", bold=0):
    """Define one styled text run for rich text content."""
    doc = {"t": str(text)}
    _put(doc, "c", _color(color))
    _put(doc, "z", _number(font_size))
    _put(doc, "b", 1 if _bool(bold, False) else 0, 0)
    return doc


def _rich_runs(value):
    if _blank_to_none(value) is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _encode_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def content_image(image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode=""):
    """Define a content image anchored inside panel/button content.

    position accepts tl/t/tr/l/c/r/bl/b/br. Older left/right/top/bottom values
    are still accepted by the launcher and mapped to the closest anchor.
    mode="text" makes the image render at a {img:N} token inside text.
    """
    return _content_image_doc(image_id, builtin_image, position, width, height, x, y, mode)


def content_align(horizontal=""):
    """Define the default horizontal alignment for a content-change action."""
    return {"_scndui_content_align": _blank_to_none(horizontal)}


def content_valign(vertical=""):
    """Define the vertical alignment for a content-change action."""
    return {"_scndui_content_valign": _blank_to_none(vertical)}


def line_alignments(*values):
    """Define per-line text alignment for panel/button text or content-change actions."""
    if len(values) == 1 and isinstance(values[0], (list, tuple)):
        values = tuple(values[0])
    return {"_scndui_line_alignments": _line_alignments(values)}


def line_align(*values):
    """Alias for line_alignments()."""
    return line_alignments(*values)


def _split_content_items(items):
    if len(items) == 1 and isinstance(items[0], (list, tuple)):
        items = tuple(items[0])
    images = []
    rich_items = []
    align_value = None
    valign_value = None
    line_aligns = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if "_scndui_content_align" in item:
            align_value = item.get("_scndui_content_align")
        elif "_scndui_content_valign" in item:
            valign_value = item.get("_scndui_content_valign")
        elif "_scndui_line_alignments" in item:
            line_aligns = item.get("_scndui_line_alignments")
        elif "t" in item:
            rich_items.append(item)
        elif "i" in item:
            images.append(item)
    return images, rich_items, align_value, valign_value, line_aligns


def _encode_content_payload(text="", images=None, rich_text=None, align="", valign="", line_alignments_value=None):
    payload = {"t": str(text)}
    normalized_rich = _rich_runs(rich_text)
    if normalized_rich:
        payload["rx"] = normalized_rich
    normalized_images = _content_images(images)
    if normalized_images:
        payload["ci"] = normalized_images
    _put(payload, "ta", _blank_to_none(align))
    _put(payload, "va", _blank_to_none(valign))
    _put(payload, "la", _line_alignments(line_alignments_value), [])
    return _encode_payload(payload)


def action_content(element_id, text="", *items):
    """UIUX action: replace panel/button text and content images at runtime."""
    images, rich_items, align_value, valign_value, line_aligns = _split_content_items(items)
    return "content:" + str(element_id) + ":" + _encode_content_payload(text, images, rich_items, align_value, valign_value, line_aligns)


def action_content_rich(element_id, *items):
    """UIUX action: replace content with rich text runs and optional content images."""
    image_items, rich_items, align_value, valign_value, line_aligns = _split_content_items(items)
    plain_text = "".join(str(item.get("t", "")) for item in rich_items)
    return "content:" + str(element_id) + ":" + _encode_content_payload(plain_text, image_items, rich_items, align_value, valign_value, line_aligns)


def action_style(element_id, fill_color="", outline_color="", text_color="", opacity="", radius="", font_size="", rich_text=None):
    """UIUX action: change fill/outline/text style of a panel/button at runtime."""
    payload = {}
    _put(payload, "f", _color(fill_color))
    _put(payload, "o", _color(outline_color))
    _put(payload, "c", _color(text_color))
    _put(payload, "q", _opacity(opacity))
    _put(payload, "r", _number(radius))
    _put(payload, "z", _number(font_size))
    _put(payload, "rx", _rich_runs(rich_text), [])
    return "style:" + str(element_id) + ":" + _encode_payload(payload)


def action_rect(element_id, anchor="", x="", y="", width="", height=""):
    """UIUX action: change anchor, position, or size of a panel/button at runtime."""
    payload = {}
    _put(payload, "a", _blank_to_none(anchor))
    _put(payload, "x", _number(x))
    _put(payload, "y", _number(y))
    _put(payload, "w", _number(width))
    _put(payload, "h", _number(height))
    return "rect:" + str(element_id) + ":" + _encode_payload(payload)


def _normalize_condition_operator(op):
    value = str(op or "eq").strip().lower()
    return {
        "==": "eq",
        "=": "eq",
        "eq": "eq",
        "match": "eq",
        "!=": "ne",
        "<>": "ne",
        "not": "ne",
        "not_eq": "ne",
        "not_equal": "ne",
        "ne": "ne",
        ">": "gt",
        "gt": "gt",
        "greater": "gt",
        ">=": "ge",
        "ge": "ge",
        "gte": "ge",
        "<": "lt",
        "lt": "lt",
        "less": "lt",
        "<=": "le",
        "le": "le",
        "lte": "le",
        "visible": "vis",
        "shown": "vis",
        "show": "vis",
        "is_visible": "vis",
        "vis": "vis",
        "hidden": "hid",
        "hide": "hid",
        "is_hidden": "hid",
        "hid": "hid",
    }.get(value, "eq")


def _normalize_condition_source(source):
    value = str(source or "input").strip().lower()
    if value in ("var", "variable", "v"):
        return "var"
    if value in ("visible", "visibility", "vis", "show", "shown"):
        return "visible"
    return "input"


def _condition_payload(source_id, expected_value="", nested="", op="eq", source="input", expr=""):
    payload = {
        "i": str(source_id),
        "v": str(expected_value),
        "a": str(nested),
    }
    source_kind = _normalize_condition_source(source)
    operator = _normalize_condition_operator(op)
    if source_kind != "input":
        payload["s"] = source_kind
    if operator != "eq":
        payload["op"] = operator
    if _blank_to_none(expr) is not None:
        payload["x"] = str(expr)
    return payload


def action_condition(input_id, expected_value="", *then_actions, op="eq", source="input", expr=""):
    """UIUX action: run nested UIUX actions when an input or variable condition matches."""
    main_actions = []
    else_if_branches = []
    else_action = ""
    for item in then_actions:
        if isinstance(item, dict) and "_scndui_condition_elseif" in item:
            else_if_branches.append(item["_scndui_condition_elseif"])
        elif isinstance(item, dict) and "_scndui_condition_else" in item:
            else_action = item["_scndui_condition_else"]
        else:
            main_actions.append(item)
    nested = actions(*main_actions)
    payload = _condition_payload(input_id, expected_value, nested, op, source, expr)
    if else_if_branches:
        payload["ei"] = else_if_branches
    if _blank_to_none(else_action) is not None:
        payload["el"] = str(else_action)
    return "if:" + _encode_payload(payload)


def action_text(element_id, value, images=None):
    """UIUX action: change panel/button text at runtime."""
    if _content_images(images):
        return action_content(element_id, value, images)
    return "text:" + str(element_id) + ":" + _encode_action_text(value)


def action_set_text(element_id, value, images=None):
    """Alias for action_text()."""
    return action_text(element_id, value, images)


def actions(*items):
    """Combine multiple UIUX actions into one button/hotkey action."""
    return ";".join(str(item).strip() for item in items if _blank_to_none(item) is not None)


def action_many(*items):
    """Alias for actions()."""
    return actions(*items)


def multi(*items):
    """Alias for actions()."""
    return actions(*items)


def if_match(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when an input value matches exactly."""
    return action_condition(input_id, expected_value, *then_actions)


def if_not_match(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when an input value does not match."""
    return action_condition(input_id, expected_value, *then_actions, op="ne")


def if_ge(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when a numeric input is >= expected."""
    return action_condition(input_id, expected_value, *then_actions, op="ge")


def if_le(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when a numeric input is <= expected."""
    return action_condition(input_id, expected_value, *then_actions, op="le")


def if_gt(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when a numeric input is > expected."""
    return action_condition(input_id, expected_value, *then_actions, op="gt")


def if_lt(input_id, expected_value="", *then_actions):
    """Action helper: run nested UIUX actions when a numeric input is < expected."""
    return action_condition(input_id, expected_value, *then_actions, op="lt")


def if_var(variable_id, expected_value="", *then_actions, op="eq"):
    """Action helper: run nested UIUX actions when a variable condition matches."""
    return action_condition(variable_id, expected_value, *then_actions, op=op, source="var")


def if_visible(element_id, *then_actions):
    """Action helper: run nested UIUX actions when an element is visible."""
    return action_condition(element_id, "", *then_actions, op="vis", source="visible")


def if_hidden(element_id, *then_actions):
    """Action helper: run nested UIUX actions when an element is hidden."""
    return action_condition(element_id, "", *then_actions, op="hid", source="visible")


def if_expr(expression, *then_actions):
    """Action helper: run nested UIUX actions when a boolean condition expression is true."""
    return action_condition("", "", *then_actions, expr=expression)


def if_var_match(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="eq")


def if_var_not_match(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="ne")


def if_var_ge(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="ge")


def if_var_le(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="le")


def if_var_gt(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="gt")


def if_var_lt(variable_id, expected_value="", *then_actions):
    return if_var(variable_id, expected_value, *then_actions, op="lt")


def elseif_match(input_id, expected_value="", *then_actions, op="eq", source="input", expr=""):
    """Condition helper: add an elseif branch to if_match()."""
    return {
        "_scndui_condition_elseif": _condition_payload(input_id, expected_value, actions(*then_actions), op, source, expr)
    }


def elseif_not_match(input_id, expected_value="", *then_actions):
    return elseif_match(input_id, expected_value, *then_actions, op="ne")


def elseif_ge(input_id, expected_value="", *then_actions):
    return elseif_match(input_id, expected_value, *then_actions, op="ge")


def elseif_le(input_id, expected_value="", *then_actions):
    return elseif_match(input_id, expected_value, *then_actions, op="le")


def elseif_gt(input_id, expected_value="", *then_actions):
    return elseif_match(input_id, expected_value, *then_actions, op="gt")


def elseif_lt(input_id, expected_value="", *then_actions):
    return elseif_match(input_id, expected_value, *then_actions, op="lt")


def elseif_var(variable_id, expected_value="", *then_actions, op="eq"):
    return elseif_match(variable_id, expected_value, *then_actions, op=op, source="var")


def elseif_visible(element_id, *then_actions):
    return elseif_match(element_id, "", *then_actions, op="vis", source="visible")


def elseif_hidden(element_id, *then_actions):
    return elseif_match(element_id, "", *then_actions, op="hid", source="visible")


def elseif_expr(expression, *then_actions):
    return elseif_match("", "", *then_actions, expr=expression)


def else_do(*then_actions):
    """Condition helper: add an else branch to if_match()."""
    return {"_scndui_condition_else": actions(*then_actions)}


def condition(input_id, expected_value="", *then_actions):
    """Alias for if_match()."""
    return action_condition(input_id, expected_value, *then_actions)


def _normalize_variable_type(type_name):
    value = str(type_name or "string").strip().lower()
    aliases = {
        "int": "int32",
        "long": "int64",
        "float": "double",
        "number": "double",
        "str": "string",
        "text": "string",
    }
    value = aliases.get(value, value)
    if value not in VARIABLE_TYPES:
        raise ValueError("scndui variable type must be one of: int32, int64, double, string")
    return value


def _variable_hash(variable_id):
    value = 0x811C9DC5
    for byte in str(variable_id).encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value or 1


def _memory_variable_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_VARIABLE_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_VARIABLE_MAGIC[4:8], "little"),
    ]


def _memory_reference_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_REFERENCE_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_REFERENCE_MAGIC[4:8], "little"),
    ]


def _memory_bind_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_BIND_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_BIND_MAGIC[4:8], "little"),
    ]


def _int32_dword(value):
    return int(value) & 0xFFFFFFFF


def _int64_dwords(value):
    raw = int(value) & 0xFFFFFFFFFFFFFFFF
    return [raw & 0xFFFFFFFF, (raw >> 32) & 0xFFFFFFFF]


def _double_dwords(value):
    raw = struct.pack("<d", float(value))
    return [
        int.from_bytes(raw[:4], "little"),
        int.from_bytes(raw[4:8], "little"),
    ]


def _bytes_to_dwords(data, dword_count):
    raw = bytearray(max(0, int(dword_count)) * 4)
    raw[: min(len(raw), len(data))] = data[: len(raw)]
    return [
        int.from_bytes(raw[index:index + 4], "little")
        for index in range(0, len(raw), 4)
    ]


def _memory_variable_layout(type_name, initial_value, count, max_length):
    type_name = _normalize_variable_type(type_name)
    count = max(1, int(count))
    max_length = max(1, int(max_length or 64))
    if type_name == "int64" or type_name == "double":
        stride_dwords = 2
    elif type_name == "string":
        stride_dwords = max(1, (max_length + 3) // 4)
    else:
        stride_dwords = 1

    capacity_dwords = stride_dwords * count
    dwords = [0] * capacity_dwords
    if type_name == "int32":
        dwords[0] = _int32_dword(initial_value or 0)
    elif type_name == "int64":
        dwords[:2] = _int64_dwords(initial_value or 0)
    elif type_name == "double":
        dwords[:2] = _double_dwords(initial_value or 0)
    else:
        encoded = str(initial_value or "").encode("utf-8")[:max(0, max_length - 1)]
        dwords[:stride_dwords] = _bytes_to_dwords(encoded, stride_dwords)

    return stride_dwords, max_length, capacity_dwords, dwords


def _upsert_variable_row(row):
    variables = _doc().setdefault("vr", [])
    variable_id = row.get("i")
    for index, existing in enumerate(variables):
        if existing.get("i") == variable_id:
            variables[index] = row
            return row
    variables.append(row)
    return row


class UiuxMemoryVariable:
    """EUDArray-backed display variable that SCNDgram can discover with ReadMemory."""

    def __init__(self, variable_id, type_name, row, block, value_offset, count):
        self.id = str(variable_id)
        self.type = _normalize_variable_type(type_name)
        self.row = row
        self._block = block
        self._value_offset = int(value_offset)
        self.length = max(1, int(count))

    @property
    def block(self):
        return self._block

    @property
    def value_epd(self):
        return self._block + self._value_offset

    def __getitem__(self, key):
        return self._block[self._value_offset + int(key)]

    def __setitem__(self, key, value):
        self._block[self._value_offset + int(key)] = value

    def get(self, key=0):
        return self[key]

    def set(self, value, key=0):
        self[key] = value
        return value

    def iadditem(self, key, value):
        return self._block.iadditem(self._value_offset + int(key), value)

    def isubtractitem(self, key, value):
        return self._block.isubtractitem(self._value_offset + int(key), value)


def _touch_memory_variable(block, header_dwords=None):
    header_dwords = list(header_dwords or [])

    def _touch(block=block, header_dwords=header_dwords):
        actions = [
            ep.SetMemoryEPD(block._epd + offset, ep.SetTo, int(value) & 0xFFFFFFFF)
            for offset, value in enumerate(header_dwords)
        ]
        ep.RawTrigger(actions=actions)

    ep.EUDOnStart(_touch)


def _ptr_from_epd(epd):
    return epd * 4 + 0x58A364


def _memory_directory_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_DIRECTORY_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_DIRECTORY_MAGIC[4:8], "little"),
    ]


def _memory_local_player_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_LOCAL_PLAYER_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_LOCAL_PLAYER_MAGIC[4:8], "little"),
    ]


def _register_memory_directory_entry(kind, variable_hash, block):
    if block is None or not hasattr(block, "_epd"):
        return

    raw_address = _ptr_from_epd(block._epd)
    entry = (int(kind), int(variable_hash) & 0xFFFFFFFF, raw_address)
    if entry not in _MEMORY_DIRECTORY_ENTRIES:
        _MEMORY_DIRECTORY_ENTRIES.append(entry)


def _touch_memory_directory(block, entry_count):
    def _touch(block=block, entry_count=int(entry_count)):
        ep.RawTrigger(
            actions=[
                ep.SetMemoryEPD(block._epd + 0, ep.SetTo, _memory_directory_magic_dwords()[0]),
                ep.SetMemoryEPD(block._epd + 1, ep.SetTo, _memory_directory_magic_dwords()[1]),
                ep.SetMemoryEPD(block._epd + 2, ep.SetTo, UIUX_MEMORY_DIRECTORY_VERSION),
                ep.SetMemoryEPD(block._epd + 3, ep.SetTo, _ptr_from_epd(block._epd)),
                ep.SetMemoryEPD(block._epd + 4, ep.SetTo, entry_count),
                ep.SetMemoryEPD(block._epd + 5, ep.SetTo, UIUX_MEMORY_DIRECTORY_ENTRY_DWORDS),
            ]
        )

    ep.EUDOnStart(_touch)


def _touch_local_player_block(block):
    def _touch(block=block):
        ep.RawTrigger(
            actions=[
                ep.SetMemoryEPD(block._epd + 0, ep.SetTo, _memory_local_player_magic_dwords()[0]),
                ep.SetMemoryEPD(block._epd + 1, ep.SetTo, _memory_local_player_magic_dwords()[1]),
                ep.SetMemoryEPD(block._epd + 2, ep.SetTo, UIUX_MEMORY_LOCAL_PLAYER_VERSION),
                ep.SetMemoryEPD(block._epd + 3, ep.SetTo, _ptr_from_epd(block._epd)),
            ]
        )
        ep.f_dwwrite_epd(block._epd + 4, ep.f_getuserplayerid())

    ep.EUDOnStart(_touch)


def _ensure_local_player_block():
    global _UIUX_LOCAL_PLAYER_BLOCK
    if _UIUX_LOCAL_PLAYER_BLOCK is None:
        _UIUX_LOCAL_PLAYER_BLOCK = ep.EUDArray(
            _memory_local_player_magic_dwords()
            + [UIUX_MEMORY_LOCAL_PLAYER_VERSION, 0, 0xFFFFFFFF]
        )
        _touch_local_player_block(_UIUX_LOCAL_PLAYER_BLOCK)
        _register_memory_directory_entry(
            UIUX_MEMORY_DIRECTORY_KIND_LOCAL_PLAYER,
            0,
            _UIUX_LOCAL_PLAYER_BLOCK,
        )

    return _UIUX_LOCAL_PLAYER_BLOCK


def _ensure_memory_directory():
    global _MEMORY_DIRECTORY_BLOCK
    if _MEMORY_DIRECTORY_BLOCK is not None:
        return _MEMORY_DIRECTORY_BLOCK

    _ensure_local_player_block()
    if not _MEMORY_DIRECTORY_ENTRIES:
        return _MEMORY_DIRECTORY_BLOCK

    dwords = _memory_directory_magic_dwords() + [
        UIUX_MEMORY_DIRECTORY_VERSION,
        0,
        len(_MEMORY_DIRECTORY_ENTRIES),
        UIUX_MEMORY_DIRECTORY_ENTRY_DWORDS,
    ]
    for kind, variable_hash, raw_address in _MEMORY_DIRECTORY_ENTRIES:
        dwords += [kind, variable_hash, raw_address]

    _MEMORY_DIRECTORY_BLOCK = ep.EUDArray(dwords)
    _touch_memory_directory(_MEMORY_DIRECTORY_BLOCK, len(_MEMORY_DIRECTORY_ENTRIES))
    return _MEMORY_DIRECTORY_BLOCK


def _rotl32(value, shift):
    value = int(value) & 0xFFFFFFFF
    shift = int(shift) & 31
    return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF


def _memory_bind_mask(variable_hash, schema_hash, type_code, scope):
    value = (0x9E3779B9 ^ int(variable_hash) ^ ((int(schema_hash) << 7) & 0xFFFFFFFF)) & 0xFFFFFFFF
    value ^= (int(type_code) * 0x045D9F3B) & 0xFFFFFFFF
    value ^= (int(scope) * 0x27D4EB2D) & 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    value ^= value >> 16
    return value or 0xA5A5A5A5


def _memory_bind_signature(variable_hash, schema_hash, type_code, scope, stride, item_index, array_count, slots, max_length):
    value = (0xC2B2AE35 ^ int(variable_hash) ^ _rotl32(schema_hash, 11)) & 0xFFFFFFFF
    for word in (type_code, scope, stride, item_index, array_count, slots, max_length):
        value ^= int(word) & 0xFFFFFFFF
        value = (value * 0x85EBCA6B) & 0xFFFFFFFF
        value ^= value >> 13
    return value or 0x1D872B41


def _source_memory_layout(values):
    if not hasattr(values, "_epd"):
        raise RuntimeError("scndui.variable can bind only EUDArray or PVariable-like SCND values")

    if hasattr(values, "_size"):
        return _ptr_from_epd(values._epd + 87), 18

    return _ptr_from_epd(values._epd), 1


def _source_epd_layout(source):
    if source is None:
        return None, 0
    if hasattr(source, "value_epd"):
        return source.value_epd, 1
    if hasattr(source, "_epd"):
        if hasattr(source, "_size"):
            return source._epd + 87, 18
        return source._epd, 1
    return None, 0


def _source_memory_address(source, index=0):
    if source is None:
        return None
    if hasattr(source, "getValueAddr"):
        return source.getValueAddr()
    base_epd, stride = _source_epd_layout(source)
    if base_epd is not None:
        return _ptr_from_epd(base_epd + int(index) * int(stride))
    if hasattr(source, "Evaluate"):
        return source
    return None


def _reference_variable(variable_id, type_name="int32", source=None, index=0, stride=1, max_length=64, initial_value=0):
    variable_id = str(variable_id)
    type_name = _normalize_variable_type(type_name)
    source_address = _source_memory_address(source, index)
    if source_address is None:
        raise ValueError("scndui.variable source must be an EUDVariable, EUDArray, EPD/ConstExpr, or UiuxMemoryVariable")

    stride = max(1, int(stride))
    max_length = max(1, int(max_length or 64))
    variable_hash = _variable_hash(variable_id)
    header = (
        _memory_reference_magic_dwords()
        + [
            UIUX_MEMORY_VARIABLE_VERSION,
            variable_hash,
            _UIUX_MEMORY_VARIABLE_TYPE_CODES[type_name],
            source_address,
            stride,
            max_length,
            1,
        ]
    )
    block = ep.EUDArray(header)
    _touch_memory_variable(block, header)
    _register_memory_directory_entry(UIUX_MEMORY_DIRECTORY_KIND_REFERENCE, variable_hash, block)

    row = {"i": variable_id, "t": type_name, "v": initial_value, "mh": variable_hash}
    if stride != 1:
        row["ms"] = stride
    if type_name == "string" or max_length != 64:
        row["ml"] = max_length
    _upsert_variable_row(row)
    return source


def _scnd_module():
    module = sys.modules.get("scnd")
    if module is not None:
        return module

    for candidate in tuple(sys.modules.values()):
        if getattr(candidate, "__name__", "").rsplit(".", 1)[-1].casefold() == "scnd":
            return candidate

    return None


def _parse_bind_variable_id(variable_id):
    text = str(variable_id).strip()
    if not text.endswith("]"):
        return text, None

    open_at = text.rfind("[")
    if open_at <= 0:
        return text, None

    index_text = text[open_at + 1 : -1].strip()
    if not index_text.isdigit():
        return text, None

    return text[:open_at], int(index_text)


def _find_scnd_bind_group(module, variable_id, index):
    groups = getattr(module, "_PACK_GROUPS", None)
    if not groups:
        return None, 0

    text = str(variable_id).strip()
    base_key, parsed_index = _parse_bind_variable_id(text)
    requested_index = int(index or 0)

    for group in groups:
        kind = getattr(group, "kind", "")
        key = getattr(group, "key", "")
        if kind in ("number", "global_number"):
            if key == text and parsed_index is None and requested_index == 0:
                return group, 0
            continue

        if kind not in ("array", "global_array"):
            continue

        group_count = int(getattr(group, "group_count", 0))
        if key == text:
            if 0 <= requested_index < group_count:
                return group, requested_index
            continue

        if parsed_index is not None and key == base_key:
            if 0 <= parsed_index < group_count:
                return group, parsed_index
            continue

        indexed_key = "%s[%d]" % (text, requested_index)
        if parsed_index is None and key == indexed_key and group_count == 1:
            return group, 0

    return None, 0


def _touch_bind_block(block, header_dwords=None):
    header_dwords = list(header_dwords or [])

    def _touch(block=block, header_dwords=header_dwords):
        actions = [
            ep.SetMemoryEPD(block._epd + offset, ep.SetTo, int(value) & 0xFFFFFFFF)
            for offset, value in enumerate(header_dwords)
        ]
        ep.RawTrigger(actions=actions)

    ep.EUDOnStart(_touch)


def _scnd_bind_variable(variable_id, type_name="int32", initial_value=0, index=0, max_length=64):
    module = _scnd_module()
    if module is None:
        return None

    variable_id = str(variable_id).strip()
    if not variable_id:
        return None

    type_name = _normalize_variable_type(type_name)
    variable_hash = _variable_hash(variable_id)
    max_length = max(1, int(max_length or 64))
    index = int(index or 0)
    cache_key = (variable_id, type_name, index, max_length)
    if cache_key in _MEMORY_BIND_BLOCKS:
        block = _MEMORY_BIND_BLOCKS[cache_key]
    else:
        group, group_index = _find_scnd_bind_group(module, variable_id, index)
        if group is None:
            return None

        values = getattr(group, "values", None)
        base_address, element_stride_dwords = _source_memory_layout(values)
        kind = getattr(group, "kind", "")
        scope = UIUX_MEMORY_BIND_SCOPE_GLOBAL if str(kind).startswith("global") else UIUX_MEMORY_BIND_SCOPE_PLAYER
        type_code = _UIUX_MEMORY_VARIABLE_TYPE_CODES[type_name]
        schema_hash = int(getattr(module, "_SCHEMA_HASH", 1) or 1) & 0xFFFFFFFF
        mask_a = _memory_bind_mask(variable_hash, schema_hash, type_code, scope)
        mask_b = _memory_bind_mask(variable_hash ^ 0xA511E9B3, schema_hash ^ 0x63D83595, type_code + 17, scope + 3)
        stride = int(element_stride_dwords)
        item_index = int(getattr(group, "item_index_start", 0)) + int(group_index)
        array_count = int(getattr(group, "array_count", 1))
        slots = int(getattr(group, "slots", 1))
        masked_stride = stride ^ _rotl32(mask_a, 3)
        masked_item_index = item_index ^ _rotl32(mask_a, 7)
        masked_array_count = array_count ^ _rotl32(mask_a, 11)
        masked_slots = slots ^ _rotl32(mask_a, 13)
        masked_max_length = max_length ^ _rotl32(mask_a, 17)
        signature = _memory_bind_signature(
            variable_hash,
            schema_hash,
            type_code,
            scope,
            masked_stride,
            masked_item_index,
            masked_array_count,
            masked_slots,
            masked_max_length,
        )
        header = (
            _memory_bind_magic_dwords()
            + [
                UIUX_MEMORY_BIND_VERSION,
                variable_hash,
                type_code,
                scope,
                base_address + mask_a,
                base_address + mask_b,
                masked_stride,
                masked_item_index,
                masked_array_count,
                masked_slots,
                masked_max_length,
                schema_hash,
                signature,
            ]
        )
        block = ep.EUDArray(header)
        _touch_bind_block(block, header)
        _register_memory_directory_entry(UIUX_MEMORY_DIRECTORY_KIND_BIND, variable_hash, block)
        _MEMORY_BIND_BLOCKS[cache_key] = block

    row = {"i": variable_id, "t": type_name, "v": initial_value, "mh": variable_hash}
    if index:
        row["mi"] = index
    if type_name == "string" or max_length != 64:
        row["ml"] = max_length
    _upsert_variable_row(row)
    return block


def _memory_variable(variable_id, type_name="int32", initial_value=0, count=1, max_length=64, index=0):
    """Declare an auto-discoverable display variable backed by EUD memory."""
    variable_id = str(variable_id)
    type_name = _normalize_variable_type(type_name)
    stride_dwords, max_length, capacity_dwords, dwords = _memory_variable_layout(
        type_name,
        initial_value,
        count,
        max_length,
    )
    variable_hash = _variable_hash(variable_id)
    value_offset = UIUX_MEMORY_VARIABLE_HEADER_DWORDS
    header = (
        _memory_variable_magic_dwords()
        + [
            UIUX_MEMORY_VARIABLE_VERSION,
            variable_hash,
            _UIUX_MEMORY_VARIABLE_TYPE_CODES[type_name],
            value_offset,
            stride_dwords,
            max_length,
            capacity_dwords,
        ]
    )
    block = ep.EUDArray(header + dwords)
    _touch_memory_variable(block, header)
    _register_memory_directory_entry(UIUX_MEMORY_DIRECTORY_KIND_VARIABLE, variable_hash, block)

    row = {"i": variable_id, "t": type_name, "v": initial_value, "mh": variable_hash}
    index = int(index)
    if index:
        row["mi"] = index
    if stride_dwords != 1:
        row["ms"] = stride_dwords
    if type_name == "string" or max_length != 64:
        row["ml"] = max_length
    _upsert_variable_row(row)

    variable_ref = UiuxMemoryVariable(variable_id, type_name, row, block, value_offset, count)
    _MEMORY_VARIABLES[variable_id] = variable_ref
    return variable_ref


# ---------------------------------------------------------------------------
# Secure overlay write bridge.
#
# Direct WriteProcessMemory into live EUD variables cannot be validated by
# the map and can tear mid-frame, so overlay writes go through a command
# queue instead. SCNDgram writes one sequenced, checksummed record; the map
# validates it (known variable, index bound, min/max clamp, session nonce)
# in process_commands() and only then applies it, acknowledging the
# sequence number. Stale memory copies of the queue never acknowledge, so
# the overlay pings each discovered queue and trusts only the live one.
#
# Queue layout (dwords):
#   0-1 magic "SCUXCMQ1"   2 version
#   3 self pointer (stamped at map start)
#   4 session nonce (per-build salt, stamped at map start)
#   5 seq (overlay -> map)  6 ack seq (map -> overlay)  7 status
#   8 variable hash         9 element index  10 value  11 checksum

UIUX_MEMORY_COMMAND_MAGIC = b"SCUXCMQ1"
UIUX_MEMORY_COMMAND_VERSION = 1
UIUX_CMD_STATUS_OK = 1
UIUX_CMD_STATUS_BAD_CHECKSUM = 2
UIUX_CMD_STATUS_UNKNOWN_VARIABLE = 3
UIUX_CMD_STATUS_BAD_INDEX = 4

_UIUX_COMMAND_BLOCK = None
_UIUX_COMMAND_SALT = None
_UIUX_SECURE_BINDS = []
_UIUX_MIRRORS = []


def _memory_command_magic_dwords():
    return [
        int.from_bytes(UIUX_MEMORY_COMMAND_MAGIC[:4], "little"),
        int.from_bytes(UIUX_MEMORY_COMMAND_MAGIC[4:8], "little"),
    ]


def _touch_command_block(block, salt):
    def _touch(block=block, salt=salt):
        ep.RawTrigger(
            actions=[
                ep.SetMemoryEPD(block._epd + 0, ep.SetTo, _memory_command_magic_dwords()[0]),
                ep.SetMemoryEPD(block._epd + 1, ep.SetTo, _memory_command_magic_dwords()[1]),
                ep.SetMemoryEPD(block._epd + 2, ep.SetTo, UIUX_MEMORY_COMMAND_VERSION),
                ep.SetMemoryEPD(block._epd + 3, ep.SetTo, _ptr_from_epd(block._epd)),
                ep.SetMemoryEPD(block._epd + 4, ep.SetTo, salt),
            ]
        )

    ep.EUDOnStart(_touch)


def _ensure_command_block():
    global _UIUX_COMMAND_BLOCK, _UIUX_COMMAND_SALT
    if _UIUX_COMMAND_BLOCK is None:
        _UIUX_COMMAND_SALT = (secrets.randbits(32) & 0xFFFFFFFF) or 1
        _UIUX_COMMAND_BLOCK = ep.EUDArray(
            _memory_command_magic_dwords()
            + [UIUX_MEMORY_COMMAND_VERSION] + [0] * 9
        )
        _touch_command_block(_UIUX_COMMAND_BLOCK, _UIUX_COMMAND_SALT)
        _register_memory_directory_entry(UIUX_MEMORY_DIRECTORY_KIND_COMMAND, 0, _UIUX_COMMAND_BLOCK)

    return _UIUX_COMMAND_BLOCK


def _secure_target_layout(target):
    return _source_epd_layout(target)


def _secure_target_epd(target):
    epd, _stride = _secure_target_layout(target)
    return epd


def _publish_exposed_source_alias(variable_id, source):
    name = str(variable_id).strip()
    if not name or not name.isidentifier() or keyword.iskeyword(name):
        return

    try:
        frame = sys._getframe(2)
    except (AttributeError, ValueError):
        return

    module_name = frame.f_globals.get("__name__")
    if not module_name or module_name == __name__:
        return
    if name not in frame.f_globals:
        frame.f_globals[name] = source


def _secure_row(variable_id, variable_hash, min_value, max_value):
    variables = _doc().setdefault("vr", [])
    row = next((item for item in variables if item.get("i") == variable_id), None)
    if row is None:
        row = {"i": variable_id, "t": "int32", "v": 0, "mh": variable_hash}
        variables.append(row)
    row["sw"] = 1
    if min_value:
        row["mn"] = min_value
    if max_value != 0xFFFFFFFF:
        row["mx"] = max_value
    return row


def secure_bind(variable_id, target=None, count=1, min_value=0, max_value=0xFFFFFFFF, player_scope=False, slots=8):
    """Route overlay writes for variable_id through the validated command queue.

    After secure_bind, SCNDgram never writes the value directly: it submits a
    sequenced, checksummed command that the map validates (registered
    variable, index bound, min/max clamp) before applying. The map must call
    scndui.process_commands() once per frame to apply pending commands.

    player_scope=True declares a per-player array laid out [player][count];
    the overlay then targets only the local player segment automatically.
    Note that in multiplayer the command exists only in the issuing client
    memory: route state that affects shared game logic through a
    synchronized handler (secure_action + MSQC) instead.
    """
    variable_id = str(variable_id).strip()
    if not variable_id:
        raise ValueError("scndui.secure_bind needs a variable id")

    # epScript callers pass positional args, so 0 also means default target.
    if target is None or (isinstance(target, int) and target == 0):
        target = _MEMORY_VARIABLES.get(variable_id)
    epd, stride = _secure_target_layout(target)
    if epd is None:
        raise ValueError(
            "scndui.secure_bind target must be a scndui.variable or an EPD-backed object"
        )

    count = max(1, int(count))
    slots = max(1, min(8, int(slots)))
    total_count = count * slots if player_scope else count
    min_value = int(min_value) & 0xFFFFFFFF
    max_value = int(max_value) & 0xFFFFFFFF
    if min_value > max_value:
        raise ValueError("scndui.secure_bind min_value must be <= max_value")

    variable_hash = _variable_hash(variable_id)
    _ensure_command_block()
    _UIUX_SECURE_BINDS.append((variable_hash, epd, int(stride), total_count, min_value, max_value, None))

    row = _secure_row(variable_id, variable_hash, min_value, max_value)
    if player_scope:
        row["ps"] = count
    return target


def secure_action(action_id, handler, min_value=0, max_value=0xFFFFFFFF, count=1):
    """Register a map-authoritative transaction the overlay can request.

    handler(index, value) receives EUDVariables and runs as trigger code in
    process_commands() after checksum and range validation. Use it for
    multi-step state changes (shop buy/sell, inventory move) so the map -
    not the overlay - verifies funds, moves items, and stays the single
    authority over game state.
    """
    action_id = str(action_id).strip()
    if not action_id:
        raise ValueError("scndui.secure_action needs an action id")
    if not callable(handler):
        raise ValueError("scndui.secure_action handler must be callable")

    count = max(1, int(count))
    min_value = int(min_value) & 0xFFFFFFFF
    max_value = int(max_value) & 0xFFFFFFFF
    if min_value > max_value:
        raise ValueError("scndui.secure_action min_value must be <= max_value")

    variable_hash = _variable_hash(action_id)
    _ensure_command_block()
    _UIUX_SECURE_BINDS.append((variable_hash, None, 1, count, min_value, max_value, handler))
    _secure_row(action_id, variable_hash, min_value, max_value)
    return variable_hash


def _lookup_entry(value):
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    return str(value)


def lookup(table_id, mapping=None, **entries):
    """Register/extend a value -> text lookup table for overlay formatting.

    Tables are shipped in the manifest at compile time, so the overlay can
    render any numeric (or string) variable as human-readable text without
    extra memory reads. Entry values are either a plain string or a dict of
    named fields.

        scndui.lookup("grade", {0: "일반", 1: "희귀", 2: "영웅"})
        scndui.lookup("item", {1: {"n": "장검", "p": 120}})

    Overlay tokens:
        {lk:grade:{myGradeVar}}        -> "희귀"
        {lk:item.p:{slotVar}}          -> "120"
        {fmt:N0:{goldVar}}             -> "1,234" (generic number formatting)
    Unknown values render as the raw value, so missing entries degrade
    gracefully.
    """
    table = _doc().setdefault("lk", {}).setdefault(str(table_id), {})
    if mapping:
        for key, value in mapping.items():
            table[str(key)] = _lookup_entry(value)
    for key, value in entries.items():
        table[str(key)] = _lookup_entry(value)
    return table


def lookup_entries(table_id, key, text="", **fields):
    """Register one lookup key with either plain text or named fields."""
    if fields:
        entry = {str(name): str(value) for name, value in fields.items()}
        if text:
            entry.setdefault("n", str(text))
        return lookup(table_id, {key: entry})
    return lookup(table_id, {key: text})


def lookup_field(table_id, key, field, text):
    """Register one named lookup field for a key."""
    table = _doc().setdefault("lk", {}).setdefault(str(table_id), {})
    key_text = str(key)
    entry = table.get(key_text)
    if not isinstance(entry, dict):
        entry = {"n": str(entry)} if entry is not None else {}
        table[key_text] = entry
    entry[str(field)] = str(text)
    return table


def expose(variable_id, source, mode="r", count=1, player_scope=True, slots=8, min_value=0, max_value=0xFFFFFFFF):
    """Connect an existing EUD value (e.g. const arr = EUDArray(8)) to the
    overlay with an explicit access mode, without ever publishing its
    memory address.

    mode: "r" read-only, "w" write-only, "rw" both.

    - Read path: the real address never appears in any discoverable block.
      process_commands() copies the value into an anonymous mirror block
      every call, and the overlay reads only the mirror. Direct writes to
      the mirror are overwritten on the next copy, so "r" is enforced.
    - Write path: commands carry only (hash, index, value) through the
      validated queue; without "w" the variable is not whitelisted and the
      map rejects the command.
    - player_scope defaults to True (the common [player][count] layout, e.g.
      EUDArray(8)); the overlay reads and writes only the local player's
      segment. Pass player_scope=False (epScript: 0) for one shared global
      value/array.

    The source must be dword-stride (EUDArray / Db dwords). Call
    process_commands() once per frame for mirrors and writes to work.
    """
    variable_id = str(variable_id).strip()
    if not variable_id:
        raise ValueError("scndui.expose needs a variable id")

    mode_key = str(mode).strip().lower()
    aliases = {"read": "r", "write": "w", "readwrite": "rw", "wr": "rw"}
    mode_key = aliases.get(mode_key, mode_key)
    if mode_key not in ("r", "w", "rw"):
        raise ValueError('scndui.expose mode must be "r", "w", or "rw"')

    epd, stride = _secure_target_layout(source)
    if epd is None:
        raise ValueError("scndui.expose source must be an EUDArray/EPD-backed object")

    count = max(1, int(count))
    slots = max(1, min(8, int(slots)))
    total = count * slots if player_scope else count

    mirror = None
    if "r" in mode_key:
        mirror = _memory_variable(variable_id, "int32", 0, count=total)
        _UIUX_MIRRORS.append((mirror, epd, int(stride), total))
    if "w" in mode_key:
        secure_bind(
            variable_id,
            source,
            count=count,
            min_value=min_value,
            max_value=max_value,
            player_scope=player_scope,
            slots=slots,
        )

    if player_scope:
        variables = _doc().setdefault("vr", [])
        row = next((entry for entry in variables if entry.get("i") == variable_id), None)
        if row is not None:
            row["ps"] = count

    _publish_exposed_source_alias(variable_id, source)
    return mirror if mirror is not None else source


def item(item_id, name="", description="", price=0, **extra):
    """Shorthand for lookup("item", ...): one item row with n/d/p fields.

    The overlay renders ids with tokens such as {item:name:{inv0}} or
    {item:price:5} - aliases for {lk:item.n:...} / {lk:item.p:...}.
    """
    fields = {}
    if name:
        fields["n"] = str(name)
    if description:
        fields["d"] = str(description)
    if price:
        fields["p"] = str(int(price))
    for key, value in extra.items():
        fields[str(key)] = str(value)
    return lookup("item", {int(item_id): fields})


def process_commands():
    """Pump exposed read mirrors and apply pending overlay write commands.

    Call once per frame (e.g. in beforeTriggerExec).
    """
    if not _UIUX_MIRRORS and not _UIUX_SECURE_BINDS and _UIUX_COMMAND_BLOCK is None:
        return

    if _UIUX_SECURE_BINDS or _UIUX_COMMAND_BLOCK is not None:
        block = _ensure_command_block()
        q = block._epd
        seq = ep.EUDVariable()
        seq << ep.f_dwread_epd(q + 5)
        if ep.EUDIfNot()(seq.Exactly(ep.f_dwread_epd(q + 6))):
            nonce = ep.f_dwread_epd(q + 4)
            var_hash = ep.EUDVariable()
            index = ep.EUDVariable()
            value = ep.EUDVariable()
            var_hash << ep.f_dwread_epd(q + 8)
            index << ep.f_dwread_epd(q + 9)
            value << ep.f_dwread_epd(q + 10)
            expected = ep.EUDVariable()
            expected << ep.f_bitxor(nonce, ep.f_mul(seq, 0x9E3779B1))
            expected += ep.f_bitxor(var_hash, ep.f_mul(value, 0x85EBCA6B))
            expected += ep.f_mul(index, 0xC2B2AE35)
            status = ep.EUDVariable()
            if ep.EUDIfNot()(expected.Exactly(ep.f_dwread_epd(q + 11))):
                status << UIUX_CMD_STATUS_BAD_CHECKSUM
            if ep.EUDElse()():
                if ep.EUDIf()(var_hash.Exactly(0)):
                    # Liveness ping from the overlay: acknowledge, touch nothing.
                    status << UIUX_CMD_STATUS_OK
                if ep.EUDElse()():
                    status << UIUX_CMD_STATUS_UNKNOWN_VARIABLE
                    for bind_hash, target_epd, target_stride, count, min_value, max_value, handler in _UIUX_SECURE_BINDS:
                        if ep.EUDIf()(var_hash.Exactly(bind_hash)):
                            if ep.EUDIf()(index.AtMost(count - 1)):
                                if min_value > 0:
                                    if ep.EUDIf()(value.AtMost(min_value - 1)):
                                        value << min_value
                                    ep.EUDEndIf()
                                if max_value < 0xFFFFFFFF:
                                    if ep.EUDIf()(value.AtLeast(max_value + 1)):
                                        value << max_value
                                    ep.EUDEndIf()
                                if handler is not None:
                                    handler(index, value)
                                else:
                                    target_offset = index if target_stride == 1 else ep.f_mul(index, target_stride)
                                    ep.f_dwwrite_epd(target_epd + target_offset, value)
                                status << UIUX_CMD_STATUS_OK
                            if ep.EUDElse()():
                                status << UIUX_CMD_STATUS_BAD_INDEX
                            ep.EUDEndIf()
                        ep.EUDEndIf()
                ep.EUDEndIf()
            ep.EUDEndIf()
            ep.f_dwwrite_epd(q + 7, status)
            ep.f_dwwrite_epd(q + 6, seq)
        ep.EUDEndIf()

    for mirror, source_epd, source_stride, total in _UIUX_MIRRORS:
        for offset in range(total):
            ep.f_dwwrite_epd(
                mirror.value_epd + offset,
                ep.f_dwread_epd(source_epd + offset * source_stride),
            )


def runtime_variable(variable_id, type_name="string", initial_value="", address=None, index=0, stride=1, max_length=0):
    """Declare an overlay-local runtime variable."""
    variable_id = str(variable_id)
    type_name = _normalize_variable_type(type_name)
    address_text = "" if address is None else str(address).strip()
    row = {"i": variable_id, "t": type_name, "v": initial_value}
    if address_text:
        row["ma"] = int(address) if isinstance(address, int) else str(address).strip()
        index = int(index)
        stride = int(stride)
        max_length = int(max_length)
        if index:
            row["mi"] = index
        if stride != 1:
            row["ms"] = stride
        if max_length:
            row["ml"] = max_length
    return _upsert_variable_row(row)


def variable(variable_id, type_name="int32", initial_value=0, source=None, address=None, index=0, stride=1, max_length=0, count=1, runtime=False):
    """Declare a maker-facing display variable backed by auto-discovered EUD memory."""
    address_text = "" if address is None else str(address).strip()
    if runtime:
        return runtime_variable(variable_id, type_name, initial_value, address=None, index=index, stride=stride, max_length=max_length)
    if source is not None:
        return _reference_variable(variable_id, type_name, source=source, index=index, stride=stride, max_length=max_length or 64, initial_value=initial_value)
    if address_text and address_text.lower() != "auto":
        return runtime_variable(variable_id, type_name, initial_value, address=address, index=index, stride=stride, max_length=max_length)
    bound = _scnd_bind_variable(variable_id, type_name, initial_value=initial_value, index=index, max_length=max_length or 64)
    if bound is not None:
        return bound
    return _memory_variable(variable_id, type_name, initial_value, count=count, max_length=max_length or 64, index=index)


def var_text(variable_id):
    """Return a text placeholder that renders the current variable value."""
    return "{var:" + str(variable_id) + "}"


var_ref = var_text


def action_variable(variable_id, value="", op="set", type_name=""):
    """UIUX action: set or change a runtime variable."""
    payload = {"i": str(variable_id), "o": str(op or "set"), "v": value}
    if _blank_to_none(type_name) is not None:
        payload["t"] = _normalize_variable_type(type_name)
    return "var:" + _encode_payload(payload)


def _normalize_variable_watcher_operator(op):
    value = str(op or "changed").strip().lower()
    if value in ("", "change", "changed", "any"):
        return "changed"
    return _normalize_condition_operator(value)


def on_variable_change(variable_id, *then_actions, op="changed", value=""):
    """Run UIUX actions when a variable changes.

    Default op="changed" uses the last observed value as the baseline and
    runs on every later value change. Use op="eq", "le", "ge", "gt", "lt",
    or "ne" with value=... to run whenever the watched value or comparison
    expression changes and the new state matches the condition.
    """
    variable_id = str(variable_id).strip()
    if not variable_id:
        raise ValueError("scndui.on_variable_change needs a variable id")

    nested = actions(*then_actions)
    if not nested:
        raise ValueError("scndui.on_variable_change needs at least one action")

    operator = _normalize_variable_watcher_operator(op)
    row = {"i": variable_id, "a": nested}
    if operator != "changed":
        row["op"] = operator
        row["v"] = str(value)
    _doc().setdefault("vw", []).append(row)
    return row


watch_variable = on_variable_change
on_var_change = on_variable_change


def set_var(variable_id, value="", type_name=""):
    """Action helper: assign a variable value."""
    return action_variable(variable_id, value, "set", type_name)


def add_var(variable_id, value=1):
    """Action helper: add to a numeric variable."""
    return action_variable(variable_id, value, "add")


def sub_var(variable_id, value=1):
    """Action helper: subtract from a numeric variable."""
    return action_variable(variable_id, value, "sub")


def inc_var(variable_id):
    """Action helper: increment a numeric variable."""
    return action_variable(variable_id, 1, "inc")


def dec_var(variable_id):
    """Action helper: decrement a numeric variable."""
    return action_variable(variable_id, 1, "dec")


def log(message):
    """Action helper: launcher debug log only."""
    return action_log(message)


def toggle(element_id):
    """Action helper: toggle a UIUX element."""
    return action_toggle(element_id)


def show(element_id):
    """Action helper: show a UIUX element."""
    return action_show(element_id)


def hide(element_id):
    """Action helper: hide a UIUX element."""
    return action_hide(element_id)


def hide_self():
    """Action helper: hide the clicked button."""
    return action_hide_self()


def show_all():
    """Action helper: show all elements hidden by UIUX actions."""
    return action_show_all()


def show_only(element_id, scope_id=""):
    """Action helper: show one child and hide the other children in the same scope."""
    return action_show_only(element_id, scope_id)


def page(scope_id, page_id):
    """Action helper: switch a page host to a page and remember the last page."""
    return action_page(scope_id, page_id)


def show_page(scope_id, page_id):
    """Alias for page()."""
    return action_page(scope_id, page_id)


def text(element_id, value, images=None):
    """Action helper: change panel/button text at runtime."""
    return action_text(element_id, value, images)


def content(element_id, value="", *images):
    """Action helper: change panel/button text and content images at runtime."""
    return action_content(element_id, value, *images)


def content_rich(element_id, *items):
    """Action helper: change panel/button content with rich text runs."""
    return action_content_rich(element_id, *items)


def change_text(element_id, value, images=None):
    """Alias for text()."""
    return action_text(element_id, value, images)


def set_text_action(element_id, value, images=None):
    """Alias for text()."""
    return action_text(element_id, value, images)


def style_action(element_id, fill_color="", outline_color="", text_color="", opacity="", radius="", font_size="", rich_text=None):
    """Action helper: change panel/button visual style at runtime."""
    return action_style(element_id, fill_color, outline_color, text_color, opacity, radius, font_size, rich_text)


def rect_action(element_id, anchor="", x="", y="", width="", height=""):
    """Action helper: change panel/button position/size at runtime."""
    return action_rect(element_id, anchor, x, y, width, height)


def show_one(element_id, *other_ids):
    """Action helper: show one element and explicitly hide N other elements."""
    return actions(show(element_id), *(hide(other_id) for other_id in other_ids))


def show_many(*element_ids):
    """Action helper: show N elements."""
    return actions(*(show(element_id) for element_id in element_ids))


def hide_many(*element_ids):
    """Action helper: hide N elements."""
    return actions(*(hide(element_id) for element_id in element_ids))


def _size(width, height, w, h, default_width, default_height):
    resolved_width = default_width if width is None and w is None else (width if width is not None else w)
    resolved_height = default_height if height is None and h is None else (height if height is not None else h)
    return int(resolved_width), int(resolved_height)


def _children(panels=None, buttons=None, inputs=None, children=None):
    nested_panels = []
    nested_buttons = []
    nested_inputs = []
    for child in list(children or []):
        if not isinstance(child, dict):
            raise TypeError("scndui children must be panel/button/input dictionaries")
        child_type = child.get("_type")
        if child_type == "button":
            nested_buttons.append(child)
        elif child_type == "input":
            nested_inputs.append(child)
        else:
            nested_panels.append(child)
    nested_panels.extend(list(panels or []))
    nested_buttons.extend(list(buttons or []))
    nested_inputs.extend(list(inputs or []))
    return nested_panels, nested_buttons, nested_inputs


def _element(
    element_id,
    element_type,
    *,
    anchor=ANCHOR_TOP_LEFT,
    reference_target=None,
    reference_edge=None,
    x=0,
    y=0,
    width=None,
    height=None,
    w=None,
    h=None,
    default_width=160,
    default_height=48,
    text="",
    rich_text=None,
    image_id=None,
    builtin_image=False,
    image_layout=None,
    image_position=None,
    image_width=None,
    image_height=None,
    image_x=None,
    image_y=None,
    content_images=None,
    align=None,
    line_alignments=None,
    valign=None,
    auto_scale=True,
    visible=True,
    fill_color=None,
    outline_color=None,
    text_color=None,
    opacity=None,
    radius=None,
    font_size=None,
    animation=None,
    animation_ms=None,
    animation_direction=None,
    hotkey=None,
    hotkey_action=None,
    input_type=None,
    placeholder=None,
    panels=None,
    buttons=None,
    inputs=None,
    children=None,
):
    resolved_width, resolved_height = _size(width, height, w, h, default_width, default_height)
    doc = {
        "_type": element_type,
        "i": str(element_id),
        "a": str(anchor),
        "x": int(x),
        "y": int(y),
        "w": resolved_width,
        "h": resolved_height,
    }
    _put(doc, "rt", _blank_to_none(reference_target))
    _put(doc, "re", _blank_to_none(reference_edge))
    _put(doc, "t", str(text), "")
    _put(doc, "rx", _rich_runs(rich_text), [])
    _put(doc, "im", _image_id(image_id, builtin=builtin_image))
    _put(doc, "il", _blank_to_none(image_layout))
    _put(doc, "ip", _blank_to_none(image_position))
    _put(doc, "iw", _int(image_width))
    _put(doc, "ih", _int(image_height))
    _put(doc, "ix", _int(image_x))
    _put(doc, "iy", _int(image_y))
    _put(doc, "ci", _content_images(content_images), [])
    _put(doc, "ta", _blank_to_none(align))
    _put(doc, "la", _line_alignments(line_alignments), [])
    _put(doc, "va", _blank_to_none(valign))
    _put(doc, "s", 1 if auto_scale else 0, 1)
    _put(doc, "v", 1 if visible else 0, 1)
    _put(doc, "f", _color(fill_color))
    _put(doc, "o", _color(outline_color))
    _put(doc, "c", _color(text_color))
    _put(doc, "q", _opacity(opacity))
    _put(doc, "r", _number(radius))
    _put(doc, "z", _number(font_size))
    _put(doc, "an", _blank_to_none(animation))
    _put(doc, "ad", _int(animation_ms))
    _put(doc, "ar", _blank_to_none(animation_direction))
    _put(doc, "hk", _blank_to_none(hotkey))
    _put(doc, "ha", _blank_to_none(hotkey_action))
    _put(doc, "it", _blank_to_none(input_type))
    _put(doc, "pl", _blank_to_none(placeholder))

    nested_panels, nested_buttons, nested_inputs = _children(panels=panels, buttons=buttons, inputs=inputs, children=children)
    if nested_panels:
        doc["p"] = nested_panels
    if nested_buttons:
        doc["b"] = nested_buttons
    if nested_inputs:
        doc["n"] = nested_inputs
    return doc


def panel_def(
    panel_id=None,
    anchor=ANCHOR_TOP_LEFT,
    x=0,
    y=0,
    w=None,
    h=None,
    text="",
    block_clicks=False,
    id=None,
    **kwargs,
):
    """Create a panel dictionary without adding it to the current document."""
    panel_id = panel_id if panel_id is not None else id
    if panel_id is None:
        raise TypeError("panel_def() requires panel_id or id")

    doc = _element(
        panel_id,
        "panel",
        anchor=anchor,
        x=x,
        y=y,
        w=w,
        h=h,
        text=text,
        default_width=160,
        default_height=48,
        **kwargs,
    )
    _put(doc, "k", 1 if block_clicks else 0, 0)
    return doc


def button_def(
    button_id=None,
    anchor=ANCHOR_TOP_LEFT,
    x=0,
    y=0,
    w=None,
    h=None,
    text="",
    action="",
    id=None,
    **kwargs,
):
    """Create a button dictionary without adding it to the current document."""
    button_id = button_id if button_id is not None else id
    if button_id is None:
        raise TypeError("button_def() requires button_id or id")

    doc = _element(
        button_id,
        "button",
        anchor=anchor,
        x=x,
        y=y,
        w=w,
        h=h,
        text=text,
        default_width=120,
        default_height=36,
        **kwargs,
    )
    _put(doc, "ac", str(action), "")
    return doc


def input_def(
    input_id=None,
    anchor=ANCHOR_TOP_LEFT,
    x=0,
    y=0,
    w=None,
    h=None,
    text="",
    input_type="all",
    placeholder="",
    id=None,
    **kwargs,
):
    """Create an input dictionary without adding it to the current document."""
    input_id = input_id if input_id is not None else id
    if input_id is None:
        raise TypeError("input_def() requires input_id or id")

    return _element(
        input_id,
        "input",
        anchor=anchor,
        x=x,
        y=y,
        w=w,
        h=h,
        text=text,
        input_type=input_type,
        placeholder=placeholder,
        default_width=160,
        default_height=34,
        **kwargs,
    )


def image(image_id, path=None, mpq_path=None):
    """Create a maker image asset. Omit image_id to allocate one automatically."""
    if path is None:
        path = image_id
        image_id = next_image_id()
    resolved_id = _image_id(image_id)
    raw_id = _maker_image_id(image_id)
    if isinstance(path, str) and path.strip().lower().startswith("data:"):
        return {"i": resolved_id, "d": path.strip(), "_id": raw_id}
    source_path = _resolve_image_path(path)
    ext = os.path.splitext(source_path)[1].lower() or ".png"
    inner_path = mpq_path or f"uiux/img/{resolved_id}{ext}"
    return {"i": resolved_id, "p": inner_path.replace("/", "\\"), "_src": source_path, "_id": raw_id}


def _resolve_image_path(path):
    source_path = os.fspath(path)
    if _IMAGE_BASE_PATH and source_path and not os.path.isabs(source_path):
        return os.path.normpath(os.path.join(_IMAGE_BASE_PATH, source_path))
    return source_path


def set_image_folder(path=""):
    """Set a base folder for maker images registered with relative paths."""
    global _IMAGE_BASE_PATH
    source_path = os.fspath(path).strip() if path is not None else ""
    _IMAGE_BASE_PATH = os.path.normpath(source_path) if source_path else ""
    return _IMAGE_BASE_PATH


def image_folder(path=""):
    """Alias for set_image_folder()."""
    return set_image_folder(path)


def get_image_folder():
    """Return the current maker image base folder."""
    return _IMAGE_BASE_PATH


def build(panels=None, buttons=None, inputs=None, images=None, variables=None, base_width=DEFAULT_BASE_WIDTH, base_height=DEFAULT_BASE_HEIGHT):
    """Build the compact uiux_init document."""
    doc = {"v": 1}
    _put(doc, "bw", int(base_width), DEFAULT_BASE_WIDTH)
    _put(doc, "bh", int(base_height), DEFAULT_BASE_HEIGHT)
    panels = list(panels or [])
    buttons = list(buttons or [])
    inputs = list(inputs or [])
    images = list(images or [])
    variables = list(variables or [])
    if panels:
        doc["p"] = panels
    if buttons:
        doc["b"] = buttons
    if inputs:
        doc["n"] = inputs
    if images:
        doc["m"] = images
    if variables:
        doc["vr"] = variables
    return doc


def _strip_private(value):
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_private(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    return value


def write_uiux_init(doc=None, panels=None, buttons=None, inputs=None, images=None, **kwargs):
    """Write compact UI/UX init JSON and image assets into the output MPQ."""
    _ensure_memory_directory()
    payload_doc = doc if doc is not None else build(panels=panels, buttons=buttons, inputs=inputs, images=images, **kwargs)
    for asset in payload_doc.get("m", []):
        source_path = asset.get("_src")
        inner_path = asset.get("p")
        if source_path and inner_path:
            ep.MPQAddFile(str(inner_path), str(source_path))

    compact_doc = _strip_private(payload_doc)
    payload = json.dumps(compact_doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ep.MPQAddFile(UIUX_INIT_MPQ_PATH, payload)
    return compact_doc


def _doc():
    global _CURRENT_DOC
    if _CURRENT_DOC is None:
        _CURRENT_DOC = build()
    return _CURRENT_DOC


def set_base_size(base_width=DEFAULT_BASE_WIDTH, base_height=DEFAULT_BASE_HEIGHT):
    """Set the coordinate base used for StarCraft-ratio auto scaling."""
    doc = _doc()
    if int(base_width) == DEFAULT_BASE_WIDTH:
        doc.pop("bw", None)
    else:
        doc["bw"] = int(base_width)
    if int(base_height) == DEFAULT_BASE_HEIGHT:
        doc.pop("bh", None)
    else:
        doc["bh"] = int(base_height)
    return doc


def _add_to_container(container, element):
    key = "b" if element.get("_type") == "button" else ("n" if element.get("_type") == "input" else "p")
    container.setdefault(key, []).append(element)
    return element


def _walk_elements(container):
    for key in ("p", "n", "b"):
        for element in container.get(key, []):
            yield element
            yield from _walk_elements(element)


def _find(element_id):
    target = str(element_id)
    for element in _walk_elements(_doc()):
        if element.get("i") == target:
            return element
    raise KeyError(f"scndui element not found: {target}")


def _set_element_rect(element, anchor=None, x=None, y=None, width=None, height=None):
    if _blank_to_none(anchor) is not None:
        element["a"] = str(anchor)
    if _blank_to_none(x) is not None:
        element["x"] = int(x)
    if _blank_to_none(y) is not None:
        element["y"] = int(y)
    if _blank_to_none(width) is not None:
        element["w"] = int(width)
    if _blank_to_none(height) is not None:
        element["h"] = int(height)
    return element


def _apply_visual_options(element_id, options):
    element = _find(element_id)
    _put(element, "f", _color(_spec_get(options, "fill_color")))
    _put(element, "o", _color(_spec_get(options, "outline_color")))
    _put(element, "c", _color(_spec_get(options, "text_color")))
    _put(element, "q", _opacity(_spec_get(options, "opacity")))
    _put(element, "r", _number(_spec_get(options, "radius")))
    _put(element, "z", _number(_spec_get(options, "font_size")))
    _put(element, "an", _blank_to_none(_spec_get(options, "animation")))
    _put(element, "ad", _int(_spec_get(options, "animation_ms")))
    _put(element, "ar", _blank_to_none(_spec_get(options, "animation_direction")))
    _put(element, "rt", _blank_to_none(_spec_get(options, "reference_target")))
    _put(element, "re", _blank_to_none(_spec_get(options, "reference_edge")))
    _put(element, "ta", _blank_to_none(_spec_get(options, "align")))
    _put(element, "va", _blank_to_none(_spec_get(options, "valign")))
    if "line_alignments" in options:
        _put(element, "la", _line_alignments(options["line_alignments"]), [])

    if "auto_scale" in options:
        _put(element, "s", 1 if _bool(options["auto_scale"], True) else 0, 1)
    if "visible" in options:
        _put(element, "v", 1 if _bool(options["visible"], True) else 0, 1)
    if "hotkey" in options:
        _put(element, "hk", _blank_to_none(options["hotkey"]))
    if "hotkey_action" in options:
        _put(element, "ha", _blank_to_none(options["hotkey_action"]))
    if "press_hotkey" in options:
        _put(element, "ph", _blank_to_none(options["press_hotkey"]))
    if "input_type" in options:
        _put(element, "it", _blank_to_none(options["input_type"]))
    if "placeholder" in options:
        _put(element, "pl", _blank_to_none(options["placeholder"]))
    if "image_id" in options:
        _put(element, "im", _image_id(options["image_id"], builtin=_bool(options.get("builtin_image"), False)))
    if "image_layout" in options:
        _put(element, "il", _blank_to_none(options["image_layout"]))
    if "image_position" in options:
        _put(element, "ip", _blank_to_none(options["image_position"]))
    if "image_width" in options:
        _put(element, "iw", _int(options["image_width"]))
    if "image_height" in options:
        _put(element, "ih", _int(options["image_height"]))
    if "image_x" in options:
        _put(element, "ix", _int(options["image_x"]))
    if "image_y" in options:
        _put(element, "iy", _int(options["image_y"]))
    if "content_images" in options:
        _put(element, "ci", _content_images(options["content_images"]), [])
    if element.get("_type") == "button" and "action" in options:
        _put(element, "ac", str(options["action"]), "")
    if element.get("_type") == "panel" and "block_clicks" in options:
        _put(element, "k", 1 if _bool(options["block_clicks"], False) else 0, 0)
    return element


def panel(panel_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=48, text=""):
    """Create a root-level panel on the current UIUX document."""
    return _add_to_container(_doc(), panel_def(panel_id, anchor, x, y, width, height, text, False))


def panel_to(parent_id, panel_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=48, text=""):
    """Create a panel inside another panel/button."""
    return _add_to_container(_find(parent_id), panel_def(panel_id, anchor, x, y, width, height, text, False))


def button(button_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=120, height=36, text=""):
    """Create a root-level overlay button on the current UIUX document."""
    return _add_to_container(_doc(), button_def(button_id, anchor, x, y, width, height, text, ""))


def button_to(parent_id, button_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=120, height=36, text=""):
    """Create an overlay button inside another panel/button."""
    return _add_to_container(_find(parent_id), button_def(button_id, anchor, x, y, width, height, text, ""))


def input_box(input_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=34, text="", input_type="all", placeholder=""):
    """Create a root-level text input on the current UIUX document."""
    return _add_to_container(_doc(), input_def(input_id, anchor, x, y, width, height, text, input_type, placeholder))


def input_to(parent_id, input_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=34, text="", input_type="all", placeholder=""):
    """Create a text input inside another panel/button/input."""
    return _add_to_container(_find(parent_id), input_def(input_id, anchor, x, y, width, height, text, input_type, placeholder))


def page_panel(parent_id, page_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=48, text="", active=0):
    """Create a page panel. Inactive pages start hidden but keep their last page state later."""
    element = panel_to(parent_id, page_id, anchor, x, y, width, height, text)
    page_host(element["i"], parent_id)
    if not active:
        hidden(element["i"])
    return element


def page_host(element_id, parent_id):
    """Mark a panel as a page that belongs to a page host."""
    element = _find(element_id)
    _put(element, "pg", _blank_to_none(parent_id))
    return element


def page_switch(button_id, scope_id, page_id):
    """Set a button to switch a page host to a page."""
    return action(button_id, page(scope_id, page_id))


def add_panel(panel_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=48, text="", block_clicks=0):
    """Backward-compatible root panel creator."""
    element = panel(panel_id, anchor, x, y, width, height, text)
    return block_click(element["i"], block_clicks)


def add_panel_to(parent_id, panel_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=160, height=48, text="", block_clicks=0):
    """Backward-compatible child panel creator."""
    element = panel_to(parent_id, panel_id, anchor, x, y, width, height, text)
    return block_click(element["i"], block_clicks)


def add_button(button_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=120, height=36, text="", action=""):
    """Backward-compatible root button creator."""
    element = button(button_id, anchor, x, y, width, height, text)
    return set_action(element["i"], action)


def add_button_to(parent_id, button_id, anchor=ANCHOR_TOP_LEFT, x=0, y=0, width=120, height=36, text="", action=""):
    """Backward-compatible child button creator."""
    element = button_to(parent_id, button_id, anchor, x, y, width, height, text)
    return set_action(element["i"], action)


def create(spec):
    """Create a panel/button/image from an EPScript-friendly key=value string.

    Example:
      create("panel; id=root; a=tl; x=24; y=24; w=360; h=210; text=Menu; theme=window; hotkey=F8")
    """
    options = _parse_spec(spec)
    element_type = str(options.get("type", "panel")).strip().lower()
    if element_type in ("image", "img", "asset"):
        image_id = _spec_get(options, "id")
        path = _spec_get(options, "path")
        if _blank_to_none(path) is None:
            raise TypeError("scndui.create image requires path")
        if _blank_to_none(image_id) is None:
            return add_image(path, mpq_path=_spec_get(options, "mpq_path", None))
        return add_image(image_id, path, _spec_get(options, "mpq_path", None))

    element_id = _spec_get(options, "id")
    if _blank_to_none(element_id) is None:
        raise TypeError("scndui.create requires id")

    anchor = _spec_get(options, "anchor", ANCHOR_TOP_LEFT)
    x = _int(_spec_get(options, "x", 0)) or 0
    y = _int(_spec_get(options, "y", 0)) or 0
    text = str(_spec_get(options, "text", ""))
    parent_id = _blank_to_none(options.get("parent"))

    if element_type in ("button", "btn", "b"):
        width = _int(_spec_get(options, "w", 120)) or 120
        height = _int(_spec_get(options, "h", 36)) or 36
        action = str(_spec_get(options, "action", ""))
        if parent_id is None:
            element = add_button(element_id, anchor, x, y, width, height, text, action)
        else:
            element = add_button_to(parent_id, element_id, anchor, x, y, width, height, text, action)
        theme_options = _theme_dict(options.get("theme", THEME_PRIMARY))
    elif element_type in ("input", "text_input", "textbox", "field", "n"):
        width = _int(_spec_get(options, "w", 160)) or 160
        height = _int(_spec_get(options, "h", 34)) or 34
        input_type = str(_spec_get(options, "input_type", "all"))
        placeholder = str(_spec_get(options, "placeholder", ""))
        if parent_id is None:
            element = input_box(element_id, anchor, x, y, width, height, text, input_type, placeholder)
        else:
            element = input_to(parent_id, element_id, anchor, x, y, width, height, text, input_type, placeholder)
        theme_options = _theme_dict(options.get("theme", THEME_PANEL))
    elif element_type in ("panel", "box", "card", "p"):
        width = _int(_spec_get(options, "w", 160)) or 160
        height = _int(_spec_get(options, "h", 48)) or 48
        block_clicks = _bool(options.get("block_clicks"), False)
        if parent_id is None:
            element = add_panel(element_id, anchor, x, y, width, height, text, block_clicks)
        else:
            element = add_panel_to(parent_id, element_id, anchor, x, y, width, height, text, block_clicks)
        theme_options = _theme_dict(options.get("theme", THEME_PANEL))
    else:
        raise TypeError(f"unsupported scndui.create type: {element_type}")

    _apply_visual_options(element_id, theme_options)
    _apply_visual_options(element_id, options)
    return element


def create_to(parent_id, spec):
    """Create a child panel/button from a key=value string."""
    options = _parse_spec(spec)
    options["parent"] = parent_id
    return create(options)


def rect(element_id, anchor="", x="", y="", width="", height=""):
    """Set anchor, position, and size for an element."""
    return _set_element_rect(_find(element_id), anchor, x, y, width, height)


def reference(element_id, target_id="", edge="", anchor="", x="", y=""):
    """Set coordinates relative to another panel/button.

    When edge is omitted, the element anchor is also used as the target basis point.
    """
    element = _find(element_id)
    if _blank_to_none(target_id) is None:
        element.pop("rt", None)
        element.pop("re", None)
    else:
        element["rt"] = str(target_id)
        if _blank_to_none(edge) is None:
            element.pop("re", None)
        else:
            element["re"] = str(edge)
    return _set_element_rect(element, anchor, x, y, None, None)


def relative_to(element_id, target_id="", edge="", anchor="", x="", y=""):
    """Alias for reference()."""
    return reference(element_id, target_id, edge, anchor, x, y)


def clear_reference(element_id):
    """Return an element to its parent/screen coordinate basis."""
    return reference(element_id, "")


def move(element_id, x, y, anchor=""):
    """Set position, optionally changing anchor."""
    return _set_element_rect(_find(element_id), anchor, x, y, None, None)


def size(element_id, width, height):
    """Set element size."""
    return _set_element_rect(_find(element_id), None, None, None, width, height)


def set_text(element_id, value):
    """Set panel/button text."""
    element = _find(element_id)
    _put(element, "t", str(value), "")
    return element


def rich_text(element_id, *runs):
    """Set styled text runs for a panel/button."""
    element = _find(element_id)
    if len(runs) == 1 and isinstance(runs[0], (list, tuple)):
        runs = tuple(runs[0])
    _put(element, "rx", _rich_runs(runs), [])
    if runs:
        _put(element, "t", "".join(str(run.get("t", "")) for run in _rich_runs(runs)), "")
    return element


def set_action(element_id, value):
    """Set a button action. UIUX actions only affect UIUX visibility/log state."""
    element = _find(element_id)
    if element.get("_type") != "button":
        raise TypeError("scndui action can only be assigned to a button")
    _put(element, "ac", str(value), "")
    return element


def action(element_id, value):
    """Alias for set_action()."""
    return set_action(element_id, value)


def block_click(element_id, enabled=1):
    """Set whether this panel blocks StarCraft mouse clicks."""
    element = _find(element_id)
    if element.get("_type") != "panel":
        raise TypeError("scndui block_click can only be assigned to a panel")
    _put(element, "k", 1 if _bool(enabled, True) else 0, 0)
    return element


def pass_click(element_id):
    """Let mouse clicks pass through this panel to StarCraft."""
    return block_click(element_id, 0)


def visible(element_id, enabled=1):
    """Set initial visibility."""
    element = _find(element_id)
    _put(element, "v", 1 if _bool(enabled, True) else 0, 1)
    return element


def hidden(element_id):
    """Start hidden."""
    return visible(element_id, 0)


def auto_scale(element_id, enabled=1):
    """Set whether this element scales with StarCraft client size."""
    element = _find(element_id)
    _put(element, "s", 1 if _bool(enabled, True) else 0, 1)
    return element


def align(element_id, horizontal="center", vertical="center"):
    """Set text/content alignment."""
    element = _find(element_id)
    _put(element, "ta", _blank_to_none(horizontal))
    _put(element, "va", _blank_to_none(vertical))
    return element


def line_text_align(element_id, *alignments):
    """Set per-line text alignment. Empty values use the element's default alignment."""
    if len(alignments) == 1 and isinstance(alignments[0], (list, tuple)):
        alignments = tuple(alignments[0])
    element = _find(element_id)
    _put(element, "la", _line_alignments(alignments), [])
    return element


def text_line_align(element_id, *alignments):
    """Alias for line_text_align()."""
    return line_text_align(element_id, *alignments)


def colors(element_id, fill_color="", outline_color="", text_color="", opacity=""):
    """Set fill, outline, text color, and opacity."""
    element = _find(element_id)
    _put(element, "f", _color(fill_color))
    _put(element, "o", _color(outline_color))
    _put(element, "c", _color(text_color))
    _put(element, "q", _opacity(opacity))
    return element


def font(element_id, font_size):
    """Set maximum font size. Text still auto-shrinks to fit."""
    element = _find(element_id)
    _put(element, "z", _number(font_size))
    return element


def radius(element_id, value):
    """Set corner radius."""
    element = _find(element_id)
    _put(element, "r", _number(value))
    return element


def appear(element_id, animation="fade", animation_ms=160, direction=""):
    """Set appear animation and optional direction."""
    element = _find(element_id)
    if _blank_to_none(animation) is None or str(animation).strip().lower() == "none":
        element.pop("an", None)
        element.pop("ad", None)
        element.pop("ar", None)
        return element

    _put(element, "an", _blank_to_none(animation))
    _put(element, "ad", _int(animation_ms))
    _put(element, "ar", _blank_to_none(direction))
    return element


def key_action(element_id, key, value):
    """Bind a key to a UIUX action without capturing the key from StarCraft."""
    return hotkey(element_id, key, value)


def key_toggle(element_id, key, start_visible=1):
    """Bind a key that toggles this element and set initial visibility."""
    return hotkey_toggle(element_id, key, start_visible)


def icon(element_id, image_id, builtin_image=0, position="left", width="", height="", x="", y="", layout=""):
    """Attach a built-in or maker image to a panel/button."""
    return image_on(element_id, image_id, builtin_image, position, width, height, x, y, layout)


def register_image(image_id, path=None, mpq_path=None):
    """Register a maker image asset. Omit image_id to allocate one automatically."""
    return add_image(image_id, path, mpq_path)


def style(element_id, fill_color="", outline_color="", text_color="", opacity="", radius="", font_size="", animation="", animation_ms="", animation_direction=""):
    """Set visual style. Size values are maximums; SCNDgram auto-shrinks content."""
    element = _find(element_id)
    _put(element, "f", _color(fill_color))
    _put(element, "o", _color(outline_color))
    _put(element, "c", _color(text_color))
    _put(element, "q", _opacity(opacity))
    _put(element, "r", _number(radius))
    _put(element, "z", _number(font_size))
    _put(element, "an", _blank_to_none(animation))
    _put(element, "ad", _int(animation_ms))
    _put(element, "ar", _blank_to_none(animation_direction))
    return element


def theme(element_id, theme_name):
    """Apply one of the built-in visual themes."""
    return _apply_visual_options(element_id, _theme_dict(theme_name))


def layout(element_id, align="", valign="", auto_scale=1, visible=1):
    """Set alignment and visibility."""
    element = _find(element_id)
    _put(element, "ta", _blank_to_none(align))
    _put(element, "va", _blank_to_none(valign))
    _put(element, "s", 1 if auto_scale else 0, 1)
    _put(element, "v", 1 if visible else 0, 1)
    return element


def hotkey(element_id, key, action=""):
    """Bind a UIUX-only action to a key. The key is not captured, so StarCraft can still receive it."""
    element = _find(element_id)
    _put(element, "hk", _blank_to_none(key))
    resolved_action = action if _blank_to_none(action) is not None else action_toggle(element_id)
    _put(element, "ha", _blank_to_none(resolved_action))
    return element


def hotkey_toggle(element_id, key, start_visible=1):
    """Bind a key that toggles this element and set whether it starts visible."""
    element = _find(element_id)
    _put(element, "v", 1 if start_visible else 0, 1)
    _put(element, "hk", _blank_to_none(key))
    _put(element, "ha", action_toggle(element_id))
    return element


def press_hotkey(element_id, key):
    """Bind a key that presses a visible button and runs its UIUX action."""
    element = _find(element_id)
    if element.get("_type") != "button":
        raise TypeError("scndui press_hotkey can only be assigned to a button")
    _put(element, "ph", _blank_to_none(key))
    return element


def press_key(element_id, key):
    """Alias for press_hotkey()."""
    return press_hotkey(element_id, key)


def image_on(element_id, image_id, builtin_image=0, image_position="left", image_width="", image_height="", image_x="", image_y="", image_layout=""):
    """Attach an image to a panel/button."""
    element = _find(element_id)
    _put(element, "im", _image_id(image_id, builtin=bool(builtin_image)))
    _put(element, "il", _blank_to_none(image_layout))
    _put(element, "ip", _blank_to_none(image_position))
    _put(element, "iw", _int(image_width))
    _put(element, "ih", _int(image_height))
    _put(element, "ix", _int(image_x))
    _put(element, "iy", _int(image_y))
    return element


def background_image(element_id, image_id, builtin_image=0, width="", height="", x="", y="", position="c"):
    """Attach a background image to a panel/button."""
    return image_on(element_id, image_id, builtin_image, position, width, height, x, y, "background")


def content_image_on(element_id, image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode=""):
    """Append an anchored image inside panel/button content."""
    element = _find(element_id)
    element.setdefault("ci", []).append(content_image(image_id, builtin_image, position, width, height, x, y, mode))
    return element


def content_icon(element_id, image_id, builtin_image=0, position="tl", width="", height="", x="", y="", mode=""):
    """Alias for content_image_on()."""
    return content_image_on(element_id, image_id, builtin_image, position, width, height, x, y, mode)


def add_image(image_id, path=None, mpq_path=None):
    """Register a maker image asset on the current document."""
    asset = image(image_id, path, mpq_path)
    _doc().setdefault("m", []).append(asset)
    return asset


def write():
    """Write the current EPScript-friendly UIUX document into uiux_init.json."""
    return write_uiux_init(doc=_doc())


create_panel = panel
create_button = button
create_input = input_box
define_panel = panel_def
define_button = button_def
define_input = input_def
f_panel = panel
f_button = button
f_input_box = input_box
f_input = input_box
f_panel_def = panel_def
f_button_def = button_def
f_input_def = input_def
f_image = image
f_set_image_folder = set_image_folder
f_image_folder = image_folder
f_get_image_folder = get_image_folder
f_log = log
f_toggle = toggle
f_show = show
f_hide = hide
f_hide_self = hide_self
f_show_all = show_all
f_show_only = show_only
f_page = page
f_show_page = show_page
f_text = text
f_content = content
f_content_rich = content_rich
f_change_text = change_text
f_set_text_action = set_text_action
f_style_action = style_action
f_rect_action = rect_action
f_action_condition = action_condition
f_if_match = if_match
f_if_not_match = if_not_match
f_if_ge = if_ge
f_if_le = if_le
f_if_gt = if_gt
f_if_lt = if_lt
f_if_var = if_var
f_if_var_match = if_var_match
f_if_var_not_match = if_var_not_match
f_if_var_ge = if_var_ge
f_if_var_le = if_var_le
f_if_var_gt = if_var_gt
f_if_var_lt = if_var_lt
f_if_visible = if_visible
f_if_hidden = if_hidden
f_if_expr = if_expr
f_elseif_match = elseif_match
f_elseif_not_match = elseif_not_match
f_elseif_ge = elseif_ge
f_elseif_le = elseif_le
f_elseif_gt = elseif_gt
f_elseif_lt = elseif_lt
f_elseif_var = elseif_var
f_elseif_visible = elseif_visible
f_elseif_hidden = elseif_hidden
f_elseif_expr = elseif_expr
f_else_do = else_do
f_condition = condition
f_variable = variable
f_var = variable
f_runtime_variable = runtime_variable
f_ui_variable = runtime_variable
f_var_text = var_text
f_var_ref = var_text
f_action_variable = action_variable
f_on_variable_change = on_variable_change
f_watch_variable = on_variable_change
f_on_var_change = on_variable_change
f_set_var = set_var
f_add_var = add_var
f_sub_var = sub_var
f_inc_var = inc_var
f_dec_var = dec_var
f_content_align = content_align
f_content_valign = content_valign
f_line_alignments = line_alignments
f_line_align = line_align
f_show_one = show_one
f_show_many = show_many
f_hide_many = hide_many
f_actions = actions
f_action_many = action_many
f_multi = multi
f_action_log = action_log
f_action_toggle = action_toggle
f_action_show = action_show
f_action_hide = action_hide
f_action_hide_self = action_hide_self
f_action_show_all = action_show_all
f_action_show_only = action_show_only
f_action_page = action_page
f_action_content = action_content
f_action_content_rich = action_content_rich
f_action_style = action_style
f_action_rect = action_rect
f_action_text = action_text
f_action_set_text = action_set_text
f_action_var = action_variable
f_build = build
f_write_uiux_init = write_uiux_init
f_set_base_size = set_base_size
f_add_panel = add_panel
f_add_panel_to = add_panel_to
f_add_button = add_button
f_add_button_to = add_button_to
f_panel_to = panel_to
f_button_to = button_to
f_input_to = input_to
f_page_panel = page_panel
f_page_host = page_host
f_page_switch = page_switch
f_create = create
f_create_to = create_to
f_rect = rect
f_reference = reference
f_relative_to = relative_to
f_clear_reference = clear_reference
f_move = move
f_size = size
f_set_text = set_text
f_rich = rich
f_rich_text = rich_text
f_action = action
f_set_action = set_action
f_block_click = block_click
f_pass_click = pass_click
f_visible = visible
f_hidden = hidden
f_auto_scale = auto_scale
f_align = align
f_line_text_align = line_text_align
f_text_line_align = line_text_align
f_colors = colors
f_font = font
f_radius = radius
f_appear = appear
f_style = style
f_theme = theme
f_layout = layout
f_hotkey = hotkey
f_hotkey_toggle = hotkey_toggle
f_key_action = key_action
f_key_toggle = key_toggle
f_press_hotkey = press_hotkey
f_press_key = press_key
f_content_image = content_image
f_image_on = image_on
f_background_image = background_image
f_content_image_on = content_image_on
f_content_icon = content_icon
f_icon = icon
f_next_image_id = next_image_id
f_add_image = add_image
f_register_image = register_image
f_write = write
f_expose = expose
f_process_commands = process_commands
f_secure_bind = secure_bind
f_secure_action = secure_action
f_lookup = lookup
f_lookup_entries = lookup_entries
f_lookup_field = lookup_field
f_item = item
