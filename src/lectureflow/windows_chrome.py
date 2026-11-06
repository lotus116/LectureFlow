"""Compact Tk controls drawn against their actual parent surface."""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


def surface(widget):
    try:
        return widget.cget("background")
    except tk.TclError:
        return ttk.Style(widget).lookup(widget.winfo_class(), "background")


def image_asset(widget, key, make):
    root = widget._root()
    if not hasattr(root, "_chrome_images"):
        root._chrome_images = {}
    if key not in root._chrome_images:
        root._chrome_images[key] = ImageTk.PhotoImage(make(), master=root)
    return root._chrome_images[key]


def tile(widget, base, fill, border, radius=6):
    def draw():
        im = Image.new("RGB", (80, 80), base)
        ImageDraw.Draw(im).rounded_rectangle(
            (1, 1, 78, 78), radius=radius * 4, fill=fill, outline=border, width=3
        )
        return im.resize((20, 20), Image.Resampling.LANCZOS)

    return image_asset(widget, (base, fill, border, radius), draw)


def icon(widget, name, color, size=15):
    def draw():
        im = Image.new("RGBA", (64, 64))
        d = ImageDraw.Draw(im)

        def line(*points):
            d.line(points, fill=color, width=5, joint="curve")

        if name == "sidebar":
            d.rounded_rectangle((5, 9, 58, 54), radius=7, outline=color, width=5)
            line(23, 12, 23, 51)
        elif name == "folder":
            line(5, 46, 5, 17, 23, 17, 29, 24, 56, 24, 56, 49, 6, 49)
        elif name == "float":
            d.rounded_rectangle((5, 9, 59, 53), radius=7, outline=color, width=5)
            d.rounded_rectangle((31, 31, 51, 45), radius=3, fill=color)
        elif name == "bookmark":
            line(17, 54, 17, 9, 47, 9, 47, 54, 32, 44, 17, 54)
        elif name == "book":
            line(
                6, 13, 21, 12, 32, 17, 43, 12, 57, 13, 57, 49, 43, 48, 32, 53, 21, 48, 6, 49, 6, 13
            )
            line(32, 19, 32, 50)
        elif name == "record":
            d.ellipse((11, 11, 52, 52), fill=color)
        elif name == "plus":
            line(32, 12, 32, 51)
            line(12, 32, 52, 32)
        elif name == "more":
            for x in (10, 29, 48):
                d.ellipse((x, 27, x + 7, 34), fill=color)
        elif name == "settings":
            line(7, 20, 56, 20)
            line(7, 44, 56, 44)
            d.ellipse((16, 12, 31, 27), fill=color)
            d.ellipse((37, 36, 52, 51), fill=color)
        elif name == "close":
            line(15, 15, 48, 48)
            line(48, 15, 15, 48)
        return im.resize((size, size), Image.Resampling.LANCZOS)

    return image_asset(widget, ("icon", name, color, size), draw)


def Button(parent, *, kind="quiet", glyph=None, **options):
    from .windows_ui import ACCENT, DARK, EDGE, FONT, INK, MUTED

    if options.pop("style", None) == "Action.TButton":
        kind = "primary"
    base = surface(parent)
    fg = MUTED if kind == "quiet" else INK
    fill = base
    hover = "#DEEBF9" if not DARK else "#293F59"
    pressed = "#CADFF4" if not DARK else "#324E6C"
    edge = base
    disabled = base
    if kind == "primary":
        fg, fill, hover, pressed, edge = "white", ACCENT, "#2358BB", "#16479C", ACCENT
    elif kind == "soft":
        fill = "#F4F7FB" if not DARK else "#203046"
        edge = EDGE
    elif kind == "overlay":
        fg, hover, pressed = "#C9D6EA", "#253042", "#33435A"
    style = ttk.Style(parent)
    name = f"LF{kind}{base.lstrip('#')}.TButton"
    element = name + ".face"
    if element not in style.element_names():
        style.element_create(
            element,
            "image",
            tile(parent, base, fill, edge),
            ("disabled", tile(parent, base, disabled, base)),
            ("pressed", tile(parent, base, pressed, pressed)),
            ("active", tile(parent, base, hover, hover)),
            ("focus", tile(parent, base, fill, ACCENT)),
            border=7,
            padding=0,
        )
        style.layout(
            name,
            [
                (
                    element,
                    {
                        "sticky": "nsew",
                        "children": [
                            (
                                "Button.padding",
                                {
                                    "sticky": "nsew",
                                    "children": [("Button.label", {"sticky": "nsew"})],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        style.configure(
            name,
            background=base,
            foreground=fg,
            padding=(7, 4),
            font=(FONT, 9),
            width=0,
            anchor="center",
        )
        style.map(name, foreground=[("disabled", "#8493A3" if DARK else "#8595A4")])
    if glyph:
        options.update(
            image=(
                icon(parent, glyph, fg, 14),
                "disabled",
                icon(parent, glyph, "#778898" if DARK else "#A6B3BF", 14),
            ),
            compound="left",
        )
    return ttk.Button(parent, style=name, **options)


def scroll(parent, target):
    """Overlay a six-pixel thumb only when the text/table can actually scroll."""
    from .windows_ui import DARK

    base = surface(parent)
    style = ttk.Style(parent)
    name = "Slim" + base.lstrip("#") + ".Vertical.TScrollbar"
    elem = name + ".thumb"
    if elem not in style.element_names():
        color = "#566981" if DARK else "#B5C3D0"

        def thumb():
            im = Image.new("RGB", (24, 40), base)
            ImageDraw.Draw(im).rounded_rectangle((2, 0, 21, 39), radius=9, fill=color)
            return im.resize((6, 10), Image.Resampling.LANCZOS)

        img = image_asset(parent, ("scroll", base), thumb)
        style.element_create(elem, "image", img, border=(2, 4), sticky="ns")
        trough = name + ".trough"
        flat = image_asset(parent, ("trough", base), lambda: Image.new("RGB", (6, 6), base))
        style.element_create(trough, "image", flat, border=0, sticky="ns")
        # Standard thumb/trough suffixes retain ttk's drag, page and mouse behavior.
        style.layout(
            name,
            [
                (
                    trough,
                    {"sticky": "ns", "children": [(elem, {"sticky": "nswe"})]},
                )
            ],
        )
        style.configure(name, background=base, troughcolor=base, borderwidth=0, arrowsize=6)
    bar = ttk.Scrollbar(parent, style=name, command=target.yview)

    def report(first, last):
        bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            bar.place_forget()
        else:
            bar.place(relx=1, x=-2, rely=0, y=3, relheight=1, height=-6, width=6, anchor="ne")
            bar.lift()

    target.configure(yscrollcommand=report)
    return bar


def playback_scale(parent, duration):
    from .windows_ui import ACCENT, DARK

    base = surface(parent)
    style = ttk.Style(parent)
    name = "LF.Horizontal.TScale"
    if "LF.Scale.slider" not in style.element_names():

        def draw(size, shape, color):
            im = Image.new("RGB", (size * 4, size * 4), base)
            d = ImageDraw.Draw(im)
            if shape == "circle":
                d.ellipse((4, 4, size * 4 - 5, size * 4 - 5), fill=color)
            else:
                d.rectangle((0, size * 2 - 4, size * 4, size * 2 + 3), fill=color)
            return im.resize((size, size), Image.Resampling.LANCZOS)

        track = image_asset(
            parent, ("track", base), lambda: draw(14, "line", "#7890A5" if DARK else "#C9D6E3")
        )
        knob = image_asset(parent, ("knob", base), lambda: draw(14, "circle", ACCENT))
        style.element_create("LF.Scale.trough", "image", track, border=2, sticky="ew")
        style.element_create("LF.Scale.slider", "image", knob)
        style.layout(
            name,
            [
                (
                    "LF.Scale.trough",
                    {
                        "sticky": "ew",
                        "children": [("LF.Scale.slider", {"side": "left", "sticky": ""})],
                    },
                )
            ],
        )
    return ttk.Scale(parent, from_=0, to=duration, style=name)
