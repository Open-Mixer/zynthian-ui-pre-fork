#!/usr/bin/python3
# -*- coding: utf-8 -*-
# ******************************************************************************
# ZYNTHIAN PROJECT: Zynthian GUI
#
# Zynthian Widget: a page of an OpenMixer panel's controls on a Tk canvas
#
# A plugin whose panel has a bar group, a curve or a meter is drawn by omx-panel's renderer, the one library every
# OpenMixer surface draws a panel with (zynthian_omx_panel_view): the sections of the controls on the screen, a curve's
# section above them on every page, and the meters, the same for a CLAP and an LV2 plugin. The renderer answers the
# pointer, the wheel and the keys with the same gesture vocabulary; plugin-hostd computes each curve's points and its
# feedback port gives the meters. Without the renderer the strips below are drawn, and the reason is logged once.
#
# Each control is a strip that moves with omx-panel's one gesture vocabulary (zynthian_panel_gestures): press and drag,
# Shift for fine, the wheel and the arrow keys for a step (Shift for five), double-click or Backspace for the declared
# default, a tap to select, a long press for `more`. The unit and the taper are the control's own; every number is
# read from omx-panel's schema/gestures.json at run time. Without it the strips are drawn and move with nothing.
#
# Copyright (C) 2026 Pau Aliagas <linuxnow@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it under the terms
# of the GNU General Public License as published by the Free Software Foundation; either
# version 2 of the License, or any later version. See the LICENSE.txt file.
# ******************************************************************************

import logging
import time
import tkinter

from zyngine import zynthian_omx_panel_view as panel_view
from zyngine import zynthian_panel_gestures as gestures
from zyngine import zynthian_position_law as position_law
from zyngine import zynthian_touch_layer as touch
from zyngui import zynthian_gui_config
from zyngui.zynthian_widget_base import zynthian_widget_base

MARGIN = 10


class zynthian_widget_omx_panel(zynthian_widget_base):

    def __init__(self, parent):
        super().__init__(parent)
        self.widget_canvas = tkinter.Canvas(self, bd=0, highlightthickness=0, relief=tkinter.FLAT,
                                            bg=zynthian_gui_config.color_bg)
        self.widget_canvas.grid(sticky="news")
        self.panel = None
        self.table_error = None
        self.selected = None
        self.shown_until = {}
        c = self.widget_canvas
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", self.on_wheel)
        c.bind("<Button-5>", self.on_wheel)
        c.bind("<Key>", self.on_key)
        self.touch = self.slots = None
        self.polling = False
        self.drawn = None  # the renderer's view of the screen's part of the panel, see build_drawn
        self.drawn_page = None
        self.drawn_state = None
        self.image = None
        self.meter_at = {}
        try:  # Tk sees one pointer; a multi-touch screen's fingers come from the evdev slots (a pinch needs two)
            self.touch = touch.EvdevSource()
        except touch.NoTouchDevice as e:
            logging.info("no multi-touch for the panel's pinch: %s", e)

    def zctrls(self):
        return [z for z in self.zyngui_control.zcontrollers if not getattr(z, "not_on_gui", False)]

    def build(self):
        if self.build_drawn():
            return
        try:
            self.panel = gestures.Panel(self.zctrls(), self.width, self.height, MARGIN, on_word=self.on_word)
            self.table_error = None
        except gestures.GestureTableAbsent as e:
            self.panel, self.table_error = None, str(e)
            logging.error(e)
        if self.touch and self.panel:
            self.slots = touch.Slots(touch.route(self.panel), self.touch.ranges, (self.width, self.height))
            if not self.polling:
                self.polling = True
                self.after(15, self.poll_touch)

    def poll_touch(self):
        if self.slots and self.touch.poll(self.slots):
            self.refresh_gui()
        if self.slots:
            self.after(15, self.poll_touch)

    def on_size(self, event):
        if super().on_size(event):
            self.build()
            return True
        return False

    def set_processor(self, processor):
        super().set_processor(processor)
        self.build()

    def on_word(self, zctrl, word):
        if word == "select":
            self.selected = zctrl.symbol
        elif word == "more":
            self.zyngui_control.show_menu()  # the surface's menu: never a value change

    # ---- The renderer's view ----

    def page_keys(self):
        """The layout symbols of the controls on the screen, by their controller."""
        engine = self.processor.engine if self.processor else None
        keys = {}
        for z in self.zctrls():
            sym = str(z.symbol)
            keys[engine_key(sym)] = z
        return keys

    def build_drawn(self):
        """The renderer's view of the part of the panel this screen shows; False when there is none to draw."""
        engine = self.processor.engine if self.processor else None
        drawn = getattr(engine, "drawn", None)
        if self.drawn:
            self.drawn.close()
        self.drawn = self.drawn_state = None
        if not drawn or self.width < 2 or self.height < 2:
            return False
        keys = self.page_keys()
        part, self.display_map = panel_view.screen_part(drawn[0], set(keys))
        try:
            view = panel_view.View(part)
        except panel_view.RendererAbsent as e:
            if not getattr(zynthian_widget_omx_panel, "said", False):
                zynthian_widget_omx_panel.said = True
                logging.warning("%s: the panel is drawn as strips", e)
            return False
        view.set_title("", engine.name)
        view.set_surface(self.width, self.height)
        self.controls = []  # (view index, controller)
        all_zctrls = {engine_key(str(sym)): z for sym, z in engine.zctrls.items()}
        for i, key in enumerate(view.symbols):
            z = all_zctrls.get(key)
            if z is None:
                continue
            self.controls.append((i, z))
            if not z.labels:
                view.set_travel(i, z.value_min, z.value_max, z.value_default, (z.position_law or {}).get("step", 0.0),
                                bool(z.is_logarithmic), bool(z.is_integer))
        self.drawn = view
        self.drawn_page = tuple(sorted(keys))
        self.meter_at = {}
        return True

    def fraction(self, z):
        if z.position_law:
            return position_law.scale_position(z.position_law, z.value)
        span = z.value_max - z.value_min
        return (z.value - z.value_min) / span if span else 0.0

    def refresh_drawn(self):
        """Give the view the values, the curves and the meters as they are now, and draw it when anything moved."""
        view, engine = self.drawn, self.processor.engine
        if tuple(sorted(self.page_keys())) != self.drawn_page:
            self.build()
            view = self.drawn
            if not view:
                return False
        values = tuple(z.value for _, z in self.controls)
        changed = values != self.drawn_state
        if changed:
            self.drawn_state = values
            for i, z in self.controls:
                if z.labels and len(z.labels) > 2 and z.ticks:
                    at = min(range(len(z.ticks)), key=lambda k: abs(z.ticks[k] - z.value))
                    view.set_choice(i, at, len(z.ticks), z.labels[at])
                else:
                    view.set_value(i, self.fraction(z), z.get_value2label())
            for k, display in self.display_map.items():
                points = engine.curve(display)
                if len(points) >= 2:
                    view.set_curve(k, points)
        now = time.monotonic()
        for i, sym in enumerate(view.meters):
            value = engine.meter(sym)
            if value is not None:
                elapsed = now - self.meter_at.get(sym, now)
                self.meter_at[sym] = now
                view.set_meter(i, value, elapsed)
                changed = True
        if changed or self.image is None:
            self.paint(view)
        return True

    def paint(self, view):
        from PIL import Image, ImageTk
        w, h = self.width, self.height
        rgba = Image.frombuffer("RGBA", (w, h), view.draw(w, h), "raw", "RGBA", 0, 1)
        back = Image.new("RGBA", (w, h), zynthian_gui_config.color_bg)
        self.image = ImageTk.PhotoImage(Image.alpha_composite(back, rgba))
        c = self.widget_canvas
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self.image)

    def drawn_input(self, kind, e=None, **kw):
        """An event to the renderer; the controls it moved are set, and `more` opens the surface's menu."""
        x, y = (e.x, e.y) if e is not None else (0, 0)
        changes, word, _ = self.drawn.input(kind, time.monotonic() * 1000.0, x, y, **kw)
        by_index = dict(self.controls)
        for index, fraction, value in changes:
            z = by_index.get(index)
            if z is None:
                continue
            if z.position_law:
                value = position_law.scale_value(z.position_law, fraction)
            z.set_value(value)
        if word == "more":
            self.zyngui_control.show_menu()
        self.refresh_gui()

    # ---- Pointer, wheel and keys ----

    def on_press(self, e):
        self.widget_canvas.focus_set()
        if self.drawn:
            self.drawn_input(panel_view.EVENT_DOWN, e)
            return
        if self.panel:
            gestures.tk_press(_Routed(self.panel), e)
            self.refresh_gui()

    def on_motion(self, e):
        if self.drawn:
            self.drawn_input(panel_view.EVENT_MOVE, e, fine=bool(e.state & 0x1))
            return
        if self.panel:
            gestures.tk_motion(_Routed(self.panel), e)
            self.refresh_gui()

    def on_release(self, e):
        if self.drawn:
            self.drawn_input(panel_view.EVENT_UP, e)
            return
        if self.panel:
            gestures.tk_release(_Routed(self.panel), e)
            self.refresh_gui()

    def on_wheel(self, e):
        if self.drawn:
            n = 1 if getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0 else -1
            self.drawn_input(panel_view.EVENT_WHEEL, e, n=n, coarse=bool(e.state & 0x1))
            return
        row = self.panel and self.panel.row_at(e.y)
        if row:
            gestures.tk_wheel(row, e)
            self.refresh_gui()

    def on_key(self, e):
        keys = {"BackSpace": panel_view.KEY_BACKSPACE, "Up": panel_view.KEY_UP, "Down": panel_view.KEY_DOWN,
                "Left": panel_view.KEY_LEFT, "Right": panel_view.KEY_RIGHT}
        if self.drawn:
            if e.keysym in keys:
                self.drawn_input(panel_view.EVENT_KEY, key=keys[e.keysym], shift=bool(e.state & 0x1))
            return
        row = self.panel and self.selected and next((g for z, g in self.panel.rows if z.symbol == self.selected), None)
        if row:
            gestures.tk_key(row, e)
            self.refresh_gui()

    def refresh_gui(self):
        if self.drawn and self.refresh_drawn():
            return
        c = self.widget_canvas
        c.delete("all")
        if not self.panel:
            c.create_text(self.width / 2, self.height / 2, text=self.table_error or "", fill=zynthian_gui_config.color_tx_off)
            return
        for zctrl, g in self.panel.rows:
            x, y, w, h = g.target
            pos = g.travel.position(zctrl.value)
            top, bottom = y + h * 0.3, y + h * 0.7
            c.create_rectangle(MARGIN, top, w - MARGIN, bottom, outline=zynthian_gui_config.color_tx_off)
            c.create_rectangle(MARGIN, top, MARGIN + pos * (w - 2 * MARGIN), bottom, fill=zynthian_gui_config.color_on,
                               outline="")
            label = zctrl.short_name if zctrl.symbol != self.selected else f"> {zctrl.short_name}"
            c.create_text(MARGIN, y + h * 0.15, anchor="w", text=label, fill=zynthian_gui_config.color_tx)
            c.create_text(w - MARGIN, y + h * 0.15, anchor="e", text=zctrl.get_value2label(), fill=zynthian_gui_config.color_tx)


def engine_key(symbol):
    """The layout's name of a plugin-hostd controller: a CLAP parameter's number is p<number>, an LV2 port keeps its
    symbol (zynthian_engine_plugin_hostd.symbol_of)."""
    return f"p{symbol}" if symbol.isdigit() else symbol


class _Routed:
    """The panel as the one control Tk's helpers move: a press picks the row, the rest follow it."""

    def __init__(self, panel):
        self.panel = panel

    def press(self, x, y, t, pid, ptype):
        self.panel.press(x, y, t, pid, ptype)

    def motion(self, x, y, t, pid, fine=False):
        self.panel.motion(x, y, t, pid, fine)

    def release(self, x, y, t, pid):
        self.panel.release(x, y, t, pid)


# ------------------------------------------------------------------------------
