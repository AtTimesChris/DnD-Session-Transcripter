"""Column order for tables (v2.42): a default that keeps the controls together, and the order you drag remembered.

Only the on-screen order changes (QHeaderView visual order). Every column keeps its number in the code, so reading,
saving and editing rows are unaffected. The order is kept in data/ui_layout.json (not in config.json, which the app
rewrites, and never in the release zips).
"""
import json
import os

from .config import app_root


def _path():
    return app_root() / "data" / "ui_layout.json"


def _load():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(key, order):
    try:
        data = _load()
        data[key] = order
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass   # a layout that cannot be saved is not worth an error message


def valid_order(order, count):
    return isinstance(order, list) and sorted(order) == list(range(count))


def arrange(header, key, default):
    """Put the columns in the saved order for key (or default, a list of column numbers left to right), let the user
    drag headers to reorder, and remember the new order."""
    count = header.count()
    saved = _load().get(key)
    order = saved if valid_order(saved, count) else (default if valid_order(default, count) else list(range(count)))
    header.blockSignals(True)
    for visual, logical in enumerate(order):
        header.moveSection(header.visualIndex(logical), visual)
    header.blockSignals(False)
    header.setSectionsMovable(True)
    header.setToolTip("Drag a column header to put the columns in the order you like; it is remembered.")
    header.sectionMoved.connect(lambda *_: _save(key, [header.logicalIndex(v) for v in range(header.count())]))
    return order
