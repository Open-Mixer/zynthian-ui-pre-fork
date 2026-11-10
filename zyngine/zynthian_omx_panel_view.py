#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ******************************************************************************
# ZYNTHIAN PROJECT: Zynthian Engine (zynthian_omx_panel_view)
#
# A plugin's panel drawn by omx-panel's renderer, the one C library every OpenMixer surface draws a panel with
# (libomx-panel-render.so.0, omx-panel's renderer/include/omx_panel_render.h): its bars, its curves and its meters,
# in the omx look. This module is the ctypes door to it, and the host's side the renderer leaves to the host: which
# part of the panel a screen shows, the curve_bind line plugin-hostd draws a display's points from, and the reply
# of curve_points. Nothing here draws or computes a point: the renderer draws, plugin-hostd computes (omx-dsp's
# omx_curve_points), and a missing library is said by name.
#
# Copyright (C) 2026 Pau Aliagas <linuxnow@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it under the terms
# of the GNU General Public License as published by the Free Software Foundation; either
# version 2 of the License, or any later version. See the LICENSE.txt file.
# ******************************************************************************

import ctypes
import json
import math
import os
import tempfile

LIBRARY = "libomx-panel-render.so.0"
# the look, the geometry and the font a view is made from: omx-panel's tools directory as the package installs it
TOOLS_DIR = "/usr/share/omx-panel/tools"

PIXEL_RGBA8_STRAIGHT = 2

# omx_panel_event_kind_t, omx_panel_key_t, omx_panel_word_t, omx_panel_pointer_t
EVENT_DOWN, EVENT_MOVE, EVENT_UP, EVENT_WHEEL, EVENT_DETENT, EVENT_KEY = range(6)
KEY_BACKSPACE, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = range(5)
WORDS = ("none", "drag", "fine", "step", "second", "reset", "more", "select", "toggle", "paint")
POINTER_TOUCH, POINTER_MOUSE = 0, 1


class RendererAbsent(Exception):
    """The renderer cannot be loaded or refuses the panel: the reason, said once by the caller."""


class _Buffer(ctypes.Structure):
    _fields_ = [("pixels", ctypes.c_void_p), ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("stride", ctypes.c_uint32), ("scale", ctypes.c_double), ("format", ctypes.c_int)]


class _Event(ctypes.Structure):
    _fields_ = [("t_ms", ctypes.c_double), ("kind", ctypes.c_int), ("pointer", ctypes.c_uint32), ("type", ctypes.c_int),
                ("x", ctypes.c_double), ("y", ctypes.c_double), ("fine", ctypes.c_bool), ("n", ctypes.c_int32),
                ("coarse", ctypes.c_bool), ("encoder", ctypes.c_int32), ("key", ctypes.c_int), ("shift", ctypes.c_bool),
                ("control", ctypes.c_int32)]


class _Change(ctypes.Structure):
    _fields_ = [("control", ctypes.c_uint32), ("fraction", ctypes.c_double), ("value", ctypes.c_double)]


class _Answer(ctypes.Structure):
    _fields_ = [("word", ctypes.c_int), ("gesture", ctypes.c_uint32), ("begin_ms", ctypes.c_double),
                ("end_ms", ctypes.c_double), ("shown_until_ms", ctypes.c_double), ("ended", ctypes.c_bool),
                ("undo", ctypes.c_bool), ("cancelled", ctypes.c_bool), ("refusal", ctypes.c_int),
                ("selected", ctypes.c_int32)]


class _Travel(ctypes.Structure):
    _fields_ = [("min", ctypes.c_double), ("max", ctypes.c_double), ("def", ctypes.c_double), ("step", ctypes.c_double),
                ("has_def", ctypes.c_bool), ("log", ctypes.c_bool), ("integer", ctypes.c_bool)]


_lib = None
_lib_error = None


def library():
    """The renderer, loaded once: $ZYNTHIAN_OMX_PANEL_RENDER names a file, else the soname. RendererAbsent says why not."""
    global _lib, _lib_error
    if _lib is None and _lib_error is None:
        name = os.environ.get("ZYNTHIAN_OMX_PANEL_RENDER", LIBRARY)
        try:
            lib = ctypes.CDLL(name)
        except OSError as e:
            _lib_error = f"omx-panel's renderer ({name}) is not installed: {e}"
        else:
            _declare(lib)
            _lib = lib
    if _lib is None:
        raise RendererAbsent(_lib_error)
    return _lib


def _declare(lib):
    P, U, D, B, S = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_double, ctypes.c_bool, ctypes.c_char_p
    DP = ctypes.POINTER(ctypes.c_double)
    for name, res, args in (
            ("omx_panel_view_create_from_files", P, [S, S, ctypes.c_char_p, ctypes.c_size_t]),
            ("omx_panel_view_destroy", None, [P]),
            ("omx_panel_view_count", U, [P]),
            ("omx_panel_view_symbol", S, [P, U]),
            ("omx_panel_view_set_title", None, [P, S, S]),
            ("omx_panel_view_set_value", B, [P, U, D, S]),
            ("omx_panel_view_set_choice", B, [P, U, U, U, S]),
            ("omx_panel_view_set_meter", B, [P, U, D, D]),
            ("omx_panel_view_set_curve", B, [P, U, DP, DP, U]),
            ("omx_panel_view_natural_size", None, [P, ctypes.POINTER(U), ctypes.POINTER(U)]),
            ("omx_panel_view_draw", B, [P, ctypes.POINTER(_Buffer)]),
            ("omx_panel_view_set_surface", B, [P, U, U, D]),
            ("omx_panel_view_set_travel", B, [P, U, ctypes.POINTER(_Travel)]),
            ("omx_panel_view_input", ctypes.c_size_t, [P, ctypes.POINTER(_Event), ctypes.POINTER(_Change), ctypes.c_size_t,
                                                       ctypes.POINTER(_Answer)]),
            ("omx_panel_view_target_rect", B, [P, U, ctypes.POINTER(ctypes.c_double * 4), DP]),
            ("omx_panel_view_tick", ctypes.c_size_t, [P, D, ctypes.POINTER(_Change), ctypes.c_size_t,
                                                      ctypes.POINTER(_Answer)])):
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args


class View:
    """One panel layout (what `omx-panel describe --layout` prints, or a part of it) as the renderer holds it."""

    def __init__(self, layout, tools_dir=None):
        lib = self.lib = library()
        self.layout = layout
        with tempfile.NamedTemporaryFile("w", suffix=".layout.json", delete=False) as f:
            json.dump(layout, f)
            path = f.name
        err = ctypes.create_string_buffer(512)
        try:
            tools = tools_dir or os.environ.get("ZYNTHIAN_OMX_PANEL_TOOLS", TOOLS_DIR)
            self.view = lib.omx_panel_view_create_from_files(path.encode(), tools.encode(), err, len(err))
        finally:
            os.unlink(path)
        if not self.view:
            raise RendererAbsent(f"omx-panel's renderer refused the panel: {err.value.decode(errors='replace')}")
        self.symbols = [lib.omx_panel_view_symbol(self.view, i).decode() for i in range(lib.omx_panel_view_count(self.view))]
        self.meters = [m["symbol"] for m in layout.get("meters") or []]
        self.size = None

    def close(self):
        if self.view:
            self.lib.omx_panel_view_destroy(self.view)
            self.view = None

    def __del__(self):
        self.close()

    def natural_size(self):
        w, h = ctypes.c_uint32(), ctypes.c_uint32()
        self.lib.omx_panel_view_natural_size(self.view, ctypes.byref(w), ctypes.byref(h))
        return w.value, h.value

    def set_title(self, brand, name):
        self.lib.omx_panel_view_set_title(self.view, (brand or "").encode(), (name or "").encode())

    def set_value(self, index, fraction, text):
        return self.lib.omx_panel_view_set_value(self.view, index, fraction, ("" if text is None else str(text)).encode())

    def set_choice(self, index, at, count, text):
        return self.lib.omx_panel_view_set_choice(self.view, index, at, count, ("" if text is None else str(text)).encode())

    def set_meter(self, index, value, elapsed):
        return self.lib.omx_panel_view_set_meter(self.view, index, value, elapsed)

    def set_curve(self, display, points):
        n = len(points)
        xs = (ctypes.c_double * n)(*(p[0] for p in points))
        ys = (ctypes.c_double * n)(*(p[1] for p in points))
        return self.lib.omx_panel_view_set_curve(self.view, display, xs, ys, n)

    def set_surface(self, width, height, scale=1.0):
        self.size = (width, height)
        return self.lib.omx_panel_view_set_surface(self.view, width, height, scale)

    def set_travel(self, index, lo, hi, default=None, step=0.0, log=False, integer=False):
        t = _Travel(lo, hi, default if default is not None else lo, step or 0.0, default is not None, log, integer)
        return self.lib.omx_panel_view_set_travel(self.view, index, ctypes.byref(t))

    def target_rect(self, index):
        """Where control `index` takes a press, (x, y, w, h) in the surface's px, and the px a drag over its whole
        travel covers; None for a hidden control or before set_surface."""
        rect, travel = (ctypes.c_double * 4)(), ctypes.c_double()
        if not self.lib.omx_panel_view_target_rect(self.view, index, ctypes.byref(rect), ctypes.byref(travel)):
            return None
        return tuple(rect), travel.value

    def input(self, kind, t_ms, x=0.0, y=0.0, pointer=1, ptype=POINTER_MOUSE, fine=False, n=0, coarse=False, key=0,
              shift=False, control=-1):
        """One event; (the changes as [(control index, fraction, value)], the answer's word, the selected control)."""
        e = _Event(t_ms, kind, pointer, ptype, x, y, fine, n, coarse, 0, key, shift, control)
        changes = (_Change * 64)()
        a = _Answer()
        k = self.lib.omx_panel_view_input(self.view, ctypes.byref(e), changes, 64, ctypes.byref(a))
        return [(c.control, c.fraction, c.value) for c in changes[:k]], WORDS[a.word], a.selected

    def tick(self, t_ms):
        changes = (_Change * 64)()
        a = _Answer()
        k = self.lib.omx_panel_view_tick(self.view, t_ms, changes, 64, ctypes.byref(a))
        return [(c.control, c.fraction, c.value) for c in changes[:k]], WORDS[a.word], a.selected

    def draw(self, width, height, scale=1.0):
        """The panel drawn into width x height pixels: RGBA, straight alpha, rows of width * 4 bytes."""
        pixels = ctypes.create_string_buffer(width * height * 4)
        buf = _Buffer(ctypes.cast(pixels, ctypes.c_void_p), width, height, width * 4, scale, PIXEL_RGBA8_STRAIGHT)
        if not self.lib.omx_panel_view_draw(self.view, ctypes.byref(buf)):
            raise RendererAbsent(f"omx-panel's renderer did not draw a {width} x {height} panel")
        return pixels.raw


# ---- What a screen shows of the panel ----

def drawn_parts(layout):
    """Whether the layout has anything the plain controller screens cannot show: a bar group, a display or a meter."""
    return bool(layout.get("displays") or layout.get("meters") or any(s.get("bars") for s in layout.get("sections", [])))


def screen_part(layout, keys):
    """The part of `layout` a screen shows for the controls `keys` (layout symbols) on it: their sections with
    every control, a display's section above them (a curve stays on every page of its panel), and the meters.
    Indices are renumbered; returns (part, {the part's display index: the layout's display index})."""
    sections = layout.get("sections", [])
    controls = layout.get("controls", [])
    displays = layout.get("displays", [])
    wanted = {c["section"] for c in controls if c["symbol"] in keys}
    wanted |= {d["section"] for d in displays if d.get("kind") in ("eq_response", "transfer_curve")}
    order = [i for i in range(len(sections)) if i in wanted]
    new = {old: k for k, old in enumerate(order)}
    part = dict(layout)
    part["sections"] = [sections[i] for i in order]
    part["controls"] = [dict(c, section=new[c["section"]]) for c in controls if c["section"] in new]
    kept = [(i, d) for i, d in enumerate(displays) if d.get("section") in new]
    part["displays"] = [dict(d, section=new[d["section"]]) for _, d in kept]
    return part, {k: i for k, (i, _) in enumerate(kept)}


# ---- The curve a display draws: plugin-hostd computes it from a binding (protocol 0.5, curve_bind / curve_points) ----

CURVE_KINDS = ("eq_response", "transfer_curve")
# the role words a curve is bound by (omx-dsp's CURVE_ROLES, plugin-hostd-protocol's curve_bind): a role of the
# description that is none of these (attack, release, mix) draws nothing and is not bound
CURVE_ROLES = ("band_type", "band_freq", "band_gain", "band_q", "band_on", "hpf_freq", "hpf_slope", "hpf_on", "lpf_freq",
               "lpf_slope", "lpf_on", "threshold", "ratio", "knee", "makeup", "range", "knee_start", "knee_end")


def curve_bind_line(instance, index, description):
    """The curve_bind line of display `index` of a description (`omx-panel describe`'s): its kind, the family and a
    term per role, <role>[.<index>]=<symbol>:<unit>[@<shape>|...], the symbols as the daemon sets them. None for a
    display that is not a curve."""
    display = (description.get("displays") or [])[index]
    if display.get("kind") not in CURVE_KINDS:
        return None
    terms = []
    for r in description.get("roles", []):
        if r["role"] not in CURVE_ROLES:
            continue
        role = r["role"] + (f".{r['index']}" if r["role"].startswith("band_") else "")
        term = f"{role}={r['symbol']}:{r.get('unit') or 'none'}"
        if r.get("shapes"):
            term += "@" + "|".join(r["shapes"])
        elif r.get("shape"):
            term += "@" + r["shape"]
        terms.append(term)
    return " ".join([f"curve_bind {instance} {index} {display['kind']} {description.get('family')}"] + terms)


def curve_points(reply):
    """The points of a curve_points reply ('<count> <x>,<y> ...'), ascending in x; [] for a reply that is not one."""
    words = reply.split()
    try:
        n = int(words[0])
        points = [tuple(float(v) for v in w.split(",")) for w in words[1:n + 1]]
    except (IndexError, ValueError):
        return []
    if len(points) != n or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        return []
    return points


def output_set(line):
    """A feedback line 'output_set <instance> <symbol> <value>' as (instance, symbol, value); None for any other."""
    words = line.split()
    if len(words) != 4 or words[0] != "output_set":
        return None
    try:
        return int(words[1]), words[2], float(words[3])
    except ValueError:
        return None
