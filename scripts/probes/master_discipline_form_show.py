"""Печать таблицы замера формы дисциплин (V-037).

    env -u PYTHONPATH .venv/bin/python scripts/probes/master_discipline_form_show.py
"""

from __future__ import annotations

import json
import pathlib

src = pathlib.Path(__file__).with_name("master_discipline_form.json")
data = json.loads(src.read_text(encoding="utf-8"))
for viewport, block in data["viewports"].items():
    meta = block.get("_meta")
    print(f"== {viewport} — {meta}")
    for name, row in block.items():
        if name.startswith("_"):
            continue
        print(f"  {name:12} sheet={row['sheet_h']:4} body={row['body_h']:4} "
              f"top={row['body_top']:4} bottom={row['body_bottom']:4} "
              f"overflow={row['overflow']:3} select={row['select'] and row['select']['width']}")
