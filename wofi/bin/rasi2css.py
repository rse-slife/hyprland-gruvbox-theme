#!/usr/bin/env python3
"""
rasi2css.py — convert a rofi .rasi theme into wofi-compatible CSS.

Usage: rasi2css.py style.rasi > style.css

The converter:
  * recursively inlines @import files (colors/fonts), remapping
    ~/.config/wofi -> ~/.config/wofi, so the GruvBox palette of this
    dotfile set survives the translation;
  * strips /* ... */ comments;
  * maps rofi widget names to wofi CSS selectors:
        window   -> #window
        mainbox  -> #box
        listview -> #list
        element* -> row / row:selected
        entry    -> #entry
  * translates the properties wofi understands (colors, font, padding,
    margin) and silently drops the rest;
  * converts #RRGGBBAA colors to rgba() where needed.
"""

import os
import re
import sys

WIDGET_MAP = {
    "window": "#window",
    "mainbox": "#box",
    "listview": "#list",
    "element": "row",
    "element-text": "row",
    "element-icon": "row img",
    "entry": "#entry",
    "inputbar": "#entry",
    "prompt": "#entry",
    "textbox": "#input",
    "message": "#msg",
}

# Extra wofi selectors that may be targeted from rasi (e.g. by -theme-str)
EXTRA_SELECTORS = {
    "#window": {},
    "#box": {},
    "#list": {},
    "row": {},
    "row:selected": {},
    "#entry": {},
    "#img": {},
}


def expand_imports(path, depth=0, seen=None):
    """Return file content with @import lines replaced recursively."""
    if seen is None:
        seen = set()
    path = os.path.realpath(os.path.expanduser(path))
    if depth > 8 or path in seen or not os.path.isfile(path):
        return ""
    seen.add(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""

    def repl(m):
        target = m.group(1)
        target = target.replace(".config/wofi", ".config/wofi")
        if target.startswith("~/"):
            target = os.path.expanduser(target)
        elif not os.path.isabs(target):
            target = os.path.join(os.path.dirname(path), target)
        return "\n" + expand_imports(target, depth + 1, seen) + "\n"

    return re.sub(r'@import\s+"([^"]+)"\s*;', repl, text)


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


def convert_color(value):
    value = value.strip().rstrip(";").strip()
    if value == "transparent":
        return "transparent"
    m = re.fullmatch(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})", value)
    if m:
        r, g, b = (int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        a = int(m.group(2), 16) / 255.0
        if a >= 0.999:
            return "#" + m.group(1)
        return "rgba({}, {}, {}, {:.3f})".format(r, g, b, a)
    if value.startswith("@"):
        return "var(--{})".format(value[1:])
    return value


def parse_blocks(text):
    """Yield (selector, [(prop, value), ...]) from flattened rasi."""
    blocks = []
    stack = []
    buf = ""
    for ch in text:
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
        elif ch == "}":
            sel = stack.pop() if stack else ""
            if sel:
                decls = [d.strip() for d in buf.split(";") if d.strip()]
                pairs = []
                for d in decls:
                    if ":" in d:
                        p, v = d.split(":", 1)
                        pairs.append((p.strip(), v.strip()))
                blocks.append((sel, pairs))
            buf = ""
        else:
            buf += ch
    return blocks


def parse_theme_str(text):
    """Parse a rofi `-theme-str` snippet into (selector, pairs) blocks.

    Handles both rasi widget syntax (`window { width: 500px; }`) and
    plain CSS (`#window { width: 500px; }`)."""
    text = strip_comments(text)
    return parse_blocks(text)


def translate(selector, pairs):
    """Map one rasi block onto a dict of CSS declarations."""
    props = {}
    for p, v in pairs:
        if p in ("enabled", "expand", "cycle", "scrollbar", "layout",
                 "cursor", "transparency", "location",
                 "anchor", "fullscreen", "x-offset", "y-offset",
                 "horizontal-align", "vertical-align", "children",
                 "icon-theme",
                 "show-icons", "markup-columns", "sort", "fake-transparency"):
            continue
        # rasi geometry properties are shorthand lists, CSS needs the long form
        if p in ("padding", "margin"):
            parts = v.split()
            if len(parts) == 4:
                t, r, b, l = parts
                props["margin-top" if p == "margin" else "padding-top"] = t
                props["margin-right" if p == "margin" else "padding-right"] = r
                props["margin-bottom" if p == "margin" else "padding-bottom"] = b
                props["margin-left" if p == "margin" else "padding-left"] = l
                continue
        if p == "spacing":
            props["gap"] = v
        elif p == "font":
            props["font"] = v.strip('"')
        elif p == "background-color":
            c = convert_color(v)
            if c != "transparent":
                props["background"] = c
        elif p == "text-color":
            if v == "inherit":
                continue
            props["color"] = convert_color(v)
        elif p in ("border", "border-radius", "border-color"):
            # keep borders: wofi's GTK theme engine understands them
            if p == "border-color":
                props["border-color"] = convert_color(v)
            elif p == "border-radius":
                props["border-radius"] = v
            else:
                m = re.match(r"\s*(\d+(?:\.\d+)?(?:px|em|pt)?)[ \t]+(\w+)", v)
                if m and m.group(2) == "solid" and m.group(1) not in ("0px",):
                    props["border"] = "{} solid".format(m.group(1))
        elif p in ("columns", "lines", "str"):
            continue
        elif p == "width":
            props["width"] = v
        elif p == "height":
            props["height"] = v
        elif p == "orientation":
            props["flex-direction"] = ("column" if v == "vertical"
                                       else "row")
        elif p == "background-image":
            # rofi: url("~/.config/wofi/images/x.png", none)
            # wofi CSS: background-image: url("file:///home/user/x.png");
            m = re.search(r'url\(\s*"([^"]+)"', v)
            if m:
                path = os.path.expanduser(m.group(1))
                props["background-image"] = 'url("file://{}")'.format(path)
    return props


def map_selector(sel):
    """Map a rasi widget selector onto a wofi CSS selector (or None)."""
    sel = sel.strip()
    if sel.startswith("#") or sel in ("row",):
        return sel  # already a wofi/CSS selector
    base = sel.split(".")[0].strip()
    extra = "".join(s for s in sel.split(base)[1:] if s)  # ".class" parts
    selector = WIDGET_MAP.get(base)
    if selector is None:
        return None
    if base == "element" and "selected" in sel:
        selector = "row:selected"
    elif base == "element" and extra:
        selector = "row" + extra.replace(".", " ")
    return selector


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: rasi2css.py <theme.rasi> [theme-str ...]")
    src = sys.argv[1]
    theme_strs = sys.argv[2:]
    flat = strip_comments(expand_imports(src))
    blocks = parse_blocks(flat)

    # -theme-str snippets override the theme file (like rofi does)
    for ts in theme_strs:
        blocks.extend(parse_theme_str(ts))

    out = ["/* Generated by wofi/bin/rasi2css.py from {} */".format(
        os.path.basename(src)), ""]

    # 1. global palette ("*" block) -> :root variables
    palette = {}
    for sel, pairs in blocks:
        if sel == "*":
            for p, v in pairs:
                if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", p):
                    palette[p] = convert_color(v)
    if palette:
        out.append(":root {")
        for k, v in palette.items():
            out.append("    --{}: {};".format(k, v))
        out.append("}")
        out.append("")

    # 2. widget blocks -> wofi selectors
    emitted = {}
    order = []
    for sel, pairs in blocks:
        if sel == "*" or not sel:
            continue
        selector = map_selector(sel)
        if selector is None:
            continue
        if selector not in emitted:
            emitted[selector] = {}
            order.append(selector)
        emitted[selector].update(translate(selector, pairs))

    for selector in order:
        props = emitted[selector]
        if not props:
            continue
        out.append(selector + " {")
        for k, v in props.items():
            out.append("    {}: {};".format(k, v))
        out.append("}")
        out.append("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
