# -*- coding: utf-8 -*-
#
# The drawn view of a plugin-hostd panel (zyngine/zynthian_omx_panel_view.py): which part of the panel a screen shows,
# the curve_bind line plugin-hostd draws a display from, the replies it reads back, and, where omx-panel's renderer is
# installed ($ZYNTHIAN_OMX_PANEL_RENDER and $ZYNTHIAN_OMX_PANEL_TOOLS in CI, built from the omx-panel commit the
# workflow names), the renderer drawing the bars, a curve and a meter by value. The fixtures are `omx-panel describe
# --layout` and `omx-panel describe` of omx-plugins' omx-eq8, omx-geq and omx-comp descriptors.
#
# Copyright (C) 2026 Pau Aliagas <linuxnow@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

import importlib.util
import json
import os
import socket
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "omx_panel")
_spec = importlib.util.spec_from_file_location("omx_panel_view", os.path.join(HERE, "..", "zyngine", "zynthian_omx_panel_view.py"))
view = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(view)


def fixture(name):
    with open(os.path.join(FIXTURES, f"{name}.json")) as f:
        return json.load(f)


def renderer():
    try:
        view.library()
        return True
    except view.RendererAbsent:
        return False


class ScreenPart(unittest.TestCase):

    def test_a_band_page_shows_its_section_and_the_curve_above_it(self):
        layout = fixture("omx-eq8")["layout"]
        part, displays = view.screen_part(layout, {"b2_freq", "b2_gain", "b2_q", "b2_on"})
        self.assertEqual([s["key"] for s in part["sections"]], ["bands.band_1", "bands.band_2"])
        self.assertEqual([d["kind"] for d in part["displays"]], ["eq_response"])
        self.assertEqual(displays, {0: 0})
        # the controls keep their section, renumbered to the part's
        self.assertEqual({c["symbol"]: c["section"] for c in part["controls"] if c["symbol"].startswith("b2_")},
                         {"b2_type": 1, "b2_freq": 1, "b2_gain": 1, "b2_q": 1, "b2_on": 1})
        self.assertTrue(all(0 <= c["section"] < len(part["sections"]) for c in part["controls"]))

    def test_a_graphic_eq_page_shows_the_whole_row_of_bars(self):
        layout = fixture("omx-geq")["layout"]
        part, displays = view.screen_part(layout, {"band05", "band06", "band07", "band08"})
        self.assertEqual([s["key"] for s in part["sections"]], ["geq"])
        self.assertEqual(sum(len(b["controls"]) for b in part["sections"][0]["bars"]), 31)
        self.assertEqual(displays, {})

    def test_the_meters_stand_on_every_page(self):
        layout = fixture("omx-comp")["layout"]
        part, _ = view.screen_part(layout, {"duckOn"})
        self.assertEqual([m["symbol"] for m in part["meters"]], [m["symbol"] for m in layout["meters"]])
        self.assertIn("comp", [s["key"] for s in part["sections"]], "the transfer curve's section")

    def test_what_has_a_drawn_view(self):
        for name in ("omx-eq8", "omx-geq", "omx-comp"):
            self.assertTrue(view.drawn_parts(fixture(name)["layout"]), name)
        plain = dict(fixture("omx-geq")["layout"], sections=[{"key": "x", "label": "X"}], displays=[], meters=[])
        self.assertFalse(view.drawn_parts(plain))


class CurveBinding(unittest.TestCase):

    def test_a_dynamics_curve_is_bound_by_its_gain_computer_roles_only(self):
        line = view.curve_bind_line(7, 0, fixture("omx-comp")["description"])
        self.assertEqual(line, "curve_bind 7 0 transfer_curve dynamics threshold=thresholdDb:db ratio=ratio:ratio "
                               "knee=kneeDb:db makeup=makeupDb:db")

    def test_an_eq_curve_indexes_its_bands(self):
        line = view.curve_bind_line(3, 0, fixture("omx-eq8")["description"]).split()
        self.assertEqual(line[:5], ["curve_bind", "3", "0", "eq_response", "eq"])
        self.assertIn("hpf_freq=hpf_freq:hz", line)
        self.assertIn("band_freq.0=b1_freq:hz", line)
        self.assertIn("band_gain.7=b8_gain:db", line)

    def test_shapes_follow_the_role(self):
        d = {"family": "eq", "displays": [{"kind": "eq_response"}],
             "roles": [{"role": "band_type", "index": 0, "symbol": "t", "unit": "none", "shapes": ["bell", "notch"]},
                       {"role": "band_freq", "index": 1, "symbol": "f", "unit": "hz", "shape": "lowshelf"}]}
        self.assertEqual(view.curve_bind_line(1, 0, d),
                         "curve_bind 1 0 eq_response eq band_type.0=t:none@bell|notch band_freq.1=f:hz@lowshelf")

    def test_a_display_that_is_no_curve_binds_nothing(self):
        d = {"family": "dynamics", "displays": [{"kind": "gain_reduction_bar"}], "roles": []}
        self.assertIsNone(view.curve_bind_line(1, 0, d))

    def test_the_points_of_a_reply(self):
        self.assertEqual(view.curve_points("3 -80.00,-80.00 -16.00,-17.56 0,-13.50"),
                         [(-80.0, -80.0), (-16.0, -17.56), (0.0, -13.5)])
        for bad in ("", "3 1,2 3,4", "2 1,2 nan,4", "x 1,2"):
            self.assertEqual(view.curve_points(bad), [], bad)

    def test_a_feedback_line(self):
        self.assertEqual(view.output_set("output_set 2 compGr -3.5"), (2, "compGr", -3.5))
        self.assertIsNone(view.output_set("xruns 2"))
        self.assertIsNone(view.output_set("output_set 2 compGr x"))


@unittest.skipUnless(renderer(), "omx-panel's renderer is not installed")
class Drawn(unittest.TestCase):
    """The renderer itself, through this module: what a value, a curve and a meter change in the pixels."""

    W, H = 480, 300

    def draw(self, v):
        return v.draw(self.W, self.H)

    def differs(self, a, b):
        """The rows where two drawings differ."""
        rows = set()
        for y in range(self.H):
            if a[y * self.W * 4:(y + 1) * self.W * 4] != b[y * self.W * 4:(y + 1) * self.W * 4]:
                rows.add(y)
        return rows

    def test_a_bar_moves_with_its_value_and_nothing_else_does(self):
        part, _ = view.screen_part(fixture("omx-geq")["layout"], {"band01"})
        v = view.View(part)
        i = v.symbols.index("band01")
        for k in range(len(v.symbols)):
            v.set_value(k, 0.5, "0 dB")
        before = self.draw(v)
        v.set_value(i, 1.0, "+12 dB")
        after = self.draw(v)
        self.assertTrue(self.differs(before, after), "the bar moved")
        v.set_value(i, 0.5, "0 dB")
        self.assertEqual(self.draw(v), before, "and came back")

    def test_a_curve_is_drawn_from_the_points_given(self):
        part, displays = view.screen_part(fixture("omx-comp")["layout"], {"thresholdDb"})
        v = view.View(part)
        empty = self.draw(v)
        self.assertTrue(v.set_curve(0, [(-80.0, -80.0), (-18.0, -18.0), (0.0, -13.5)]))
        drawn = self.draw(v)
        self.assertTrue(self.differs(empty, drawn))
        self.assertFalse(v.set_curve(0, [(0.0, 1.0)]), "one point is refused")
        self.assertEqual(self.draw(v), drawn, "and changes nothing")

    def test_a_meter_fills_to_its_reading(self):
        part, _ = view.screen_part(fixture("omx-comp")["layout"], {"thresholdDb"})
        v = view.View(part)
        frame = self.draw(v)
        self.assertTrue(v.set_meter(0, -12.0, 0.0))
        self.assertTrue(self.differs(frame, self.draw(v)))

    def test_a_drag_moves_a_bar_through_the_gestures(self):
        part, _ = view.screen_part(fixture("omx-geq")["layout"], {"band01"})
        v = view.View(part)
        v.set_surface(self.W, self.H)
        i = v.symbols.index("band01")
        v.set_travel(i, -12.0, 12.0, 0.0)
        v.set_value(i, 0.5, "0 dB")
        (x, y, w, h), travel = v.target_rect(i)
        # a press on the bar's track and a drag up, past the drag's threshold, by half its travel
        cx, cy = x + w / 2, y + h / 2
        v.input(view.EVENT_DOWN, 0.0, cx, cy)
        for k in range(1, 11):
            changes, word, _ = v.input(view.EVENT_MOVE, 10.0 * k, cx, cy - k * travel / 20)
        v.input(view.EVENT_UP, 120.0, cx, cy - travel / 2)
        self.assertEqual(word, "drag")
        self.assertTrue(changes, "a press and a drag on a bar moved it")
        self.assertEqual([c[0] for c in changes], [i])
        _, fraction, value = changes[0]
        self.assertGreater(fraction, 0.5)
        self.assertAlmostEqual(value, -12.0 + 24.0 * fraction, places=6)  # the value is the travel's, -12..12


class Feedback(unittest.TestCase):
    """The engine's feedback reader on a socket: the meters' last values by (instance, symbol)."""

    def test_the_last_value_of_each_output(self):
        import sys
        sys.path.insert(0, HERE)
        from test_engine_plugin_hostd import ph  # noqa: E402 (the engine module under the tests' stand-ins)
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        fb = ph.plugin_hostd_feedback(server.getsockname()[1])
        fb.connect()
        conn, _ = server.accept()
        conn.sendall(b"output_set 4 compGr -1.5\0xruns 1\0output_set 4 compGr -6.25\0output_set 5 lvl 0.5\0")
        deadline = time.monotonic() + 2
        while len(fb.values) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(fb.values, {(4, "compGr"): -6.25, (5, "lvl"): 0.5})
        self.assertEqual(fb.generation, 1)
        fb.close()
        conn.close()
        server.close()

    def test_no_feedback_port_is_said_and_nothing_else(self):
        import sys
        sys.path.insert(0, HERE)
        from test_engine_plugin_hostd import ph  # noqa: E402
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        fb = ph.plugin_hostd_feedback(port)
        fb.connect()
        self.assertIsNone(fb.sock)
        self.assertEqual(fb.values, {})


if __name__ == "__main__":
    unittest.main()
