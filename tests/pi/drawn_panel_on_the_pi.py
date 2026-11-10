# -*- coding: utf-8 -*-
#
# The drawn view of a REAL plugin on a REAL plugin-hostd, by hand (not collected by CI): the engine of this tree adds
# each plugin to a daemon already listening on PHD_PORT with its feedback port on PHD_PORT + 1 (a jackd of its own name
# and a plugin-hostd the caller started with -f), and for each the screen's part of the panel is drawn by omx-panel's
# renderer with the daemon's values, the curve plugin-hostd computes and the meters its feedback port gives. The
# pictures go to OUT, one per plugin and screen; the checks are by value: a curve of points, a meter reading.
#
#   ZYNTHIAN_OMX_PANEL_RENDER=.../libomx-panel-render.so.0 ZYNTHIAN_OMX_PANEL_TOOLS=.../tools PHD_PORT=17556 \
#   OUT=/tmp/drawn PHD_URIS="clap:/usr/lib/clap/omx-comp.clap#org.openmixer.comp urn:openmixer:eq8" \
#   python3 -m pytest -s tests/pi/drawn_panel_on_the_pi.py
#
# Copyright (C) 2026 Pau Aliagas <linuxnow@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from test_engine_plugin_hostd import plugin_hostd_case, ph  # noqa: E402
from zyngine import zynthian_omx_panel_view as pv  # noqa: E402
from zyngine import zynthian_plugin_hostd_catalogue as cat  # noqa: E402

PORT = int(os.environ.get("PHD_PORT", "17556"))
URIS = os.environ.get("PHD_URIS", "clap:/usr/lib/clap/omx-comp.clap#org.openmixer.comp").split()
OUT = os.environ.get("OUT", "/tmp/drawn")
W, H = int(os.environ.get("W", "480")), int(os.environ.get("H", "300"))


def png(path, rgba, w, h):
    from PIL import Image
    Image.frombuffer("RGBA", (w, h), rgba, "raw", "RGBA", 0, 1).save(path)


class OnThePi(plugin_hostd_case):

    def setUp(self):
        super().setUp()
        os.environ["PATH"] = os.environ.get("OMX_PANEL_PATH", "") + "/usr/bin:/bin"  # omx-panel and node
        self.port = PORT
        ph.daemon = ph.plugin_hostd_daemon(PORT)
        ph.daemon.start_script = os.path.join(self.tmp, "not-installed")
        os.makedirs(OUT, exist_ok=True)

    def add_real(self, uri, n):
        if uri.startswith("clap:"):
            path, _, ident = uri[5:].partition("#")
            scan = (f'file {path}\n  plugin {ident} "{ident}" vendor OpenMixer version 1\n    features audio-effect stereo\n'
                    '    in main 2 channels stereo "in" in_1\n    out main 2 channels stereo "out" out_1\n')
            cat.add_catalogue(self.info, clap=cat.parse_clap_scan(scan), binary="/usr/bin/plugin-hostd")
            code = "PH/" + uri
        else:
            self.info[f"JV/Real{n}"] = {**self.info["JV/Amp"], "ID": f"Real{n}", "NAME": f"Real{n}", "TITLE": f"Real{n}",
                                       "URL": uri}
            cat.add_catalogue(self.info, clap=[], binary="/usr/bin/plugin-hostd")
            code = f"PH/Real{n}"
        return self.add(self.chain(n), code)

    def test_each_plugin_is_drawn_with_its_curve_and_meters(self):
        for n, uri in enumerate(URIS, 1):
            proc = self.add_real(uri, n)
            engine = proc.engine
            deadline = time.monotonic() + 30
            while not engine.drawn and time.monotonic() < deadline:  # the daemon's scan of a first add takes a moment
                time.sleep(1)
                engine.set_drawn_view()
            self.assertIsNotNone(engine.drawn, f"{uri}: omx-panel describe --layout of the daemon's descriptor")
            layout, description = engine.drawn
            print(f"\n{uri}: instance {engine.instance}, family {description.get('family')}, "
                  f"{len(layout.get('displays') or [])} displays, {len(layout.get('meters') or [])} meters, "
                  f"bars in {[s['key'] for s in layout['sections'] if s.get('bars')]}, "
                  f"screen {os.path.basename(engine.custom_gui_fpath or '-')}", flush=True)
            for k, d in enumerate(description.get("displays") or []):
                if d["kind"] in pv.CURVE_KINDS:
                    points = engine.curve(k)
                    print(f"  curve {k} {d['kind']} {d['basis']}: {len(points)} points, {points[:2]} .. {points[-1:]}",
                          flush=True)
                    self.assertGreaterEqual(len(points), 2, f"{uri}: plugin-hostd computed the curve")
            for m in layout.get("meters") or []:
                engine.meter(m["symbol"])
            time.sleep(1.0)
            for m in layout.get("meters") or []:
                print(f"  meter {m['symbol']}: {engine.meter(m['symbol'])}", flush=True)
            keys = {pv_key(sym): z for sym, z in engine.zctrls.items()}
            for page, (name, rows) in enumerate(engine._ctrl_screens):
                on = {pv_key(k) for k in rows if k}
                part, dmap = pv.screen_part(layout, on)
                view = pv.View(part)
                view.set_title("", engine.name)
                for i, key in enumerate(view.symbols):
                    z = keys.get(key)
                    if z is None:
                        continue
                    span = z.value_max - z.value_min
                    view.set_value(i, (z.value - z.value_min) / span if span else 0.0, z.get_value2label())
                for k, display in dmap.items():
                    view.set_curve(k, engine.curve(display))
                for i, sym in enumerate(view.meters):
                    v = engine.meter(sym)
                    if v is not None:
                        view.set_meter(i, v, 0.0)
                name_ok = "".join(c if c.isalnum() else "-" for c in name)[:40]
                stem = "".join(c if c.isalnum() else "-" for c in engine.name)[:30]
                png(os.path.join(OUT, f"{n:02d}-{stem}-{page:02d}-{name_ok}.png"), view.draw(W, H), W, H)
                if page >= int(os.environ.get("PAGES", "3")):
                    break


def pv_key(symbol):
    symbol = str(symbol)
    return f"p{symbol}" if symbol.isdigit() else symbol


if __name__ == "__main__":
    unittest.main()
