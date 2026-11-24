"""Qt bridge between the QML Settings panel and Friday's existing settings code.

No settings logic lives here that isn't already in settings_store,
settings_checks, provider_autodetect or user_profile -- this file only
adapts them to Qt: a list model for the connection rows, slots for the
buttons, and signals for results that arrive from worker threads.
"""
import os
import threading
import time

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Qt,
                            QUrl, Property, Signal, Slot)

import provider_autodetect as pa
import settings_checks
import settings_store
import user_profile

AUTO_DETECT = "-- auto-detect from my key --"
_CHECK_SLOTS = threading.BoundedSemaphore(4)
_SEP = "\x1f"
_TIER_LABELS = [("free", "[FREE]"), ("trial", "[TRIAL]"),
                ("paid", "[PAID]"), ("unstated", "[open]")]

# The dark palette used by every other Friday panel (ide.py's default theme).
FALLBACK_THEME = {
    "editor": "#151517", "panel": "#1b1b1e", "chat": "#151517",
    "input": "#1e1e21", "border": "#35353b", "border_soft": "#29292e",
    "fg": "#e7e6e3", "fg_bright": "#f0efec", "fg_dim": "#b2b1b8",
    "fg_faint": "#909098", "accent": "#c7c3bc", "accent_hover": "#37373d",
    "success": "#a9bda6", "danger": "#d39b9e", "warn": "#c4b598",
    "hover": "#303035", "caret": "#c7c3bc",
}


def theme_colors():
    """Friday's live palette if the IDE module is already loaded, else the default."""
    import sys
    ide = sys.modules.get("ide")
    colors = dict(FALLBACK_THEME)
    if ide is not None and isinstance(getattr(ide, "C", None), dict):
        colors.update({k: v for k, v in ide.C.items()
                       if k in colors and isinstance(v, str)})
    return colors


def _key(faculty, entry_id):
    return f"{faculty}{_SEP}{entry_id}"


def _split(key):
    faculty, _, entry_id = key.partition(_SEP)
    return faculty, entry_id


def _model_hint_text(hint):
    if hint and hint.get("model_hint"):
        return f"e.g. {hint['model_hint']}  \u2014  or use Fetch models"
    return ""


class ConnectionsModel(QAbstractListModel):
    """One row per configured model/service, in settings_store.FACULTIES order.

    Rows are inserted/removed individually (never a full reset) so the
    QML ListView can animate additions, removals and the rows that move
    to fill the gap.
    """
    ROLES = ("key", "faculty", "entryId", "name", "modelName", "maskedKey",
             "status", "detail", "selected")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def roleNames(self):
        return {Qt.UserRole + i: name.encode() for i, name in enumerate(self.ROLES)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        i = role - Qt.UserRole
        if 0 <= i < len(self.ROLES):
            return self._rows[index.row()].get(self.ROLES[i])
        return None

    def rows(self):
        return self._rows

    def index_of(self, key):
        for i, row in enumerate(self._rows):
            if row["key"] == key:
                return i
        return -1

    def update_row(self, key, **changes):
        i = self.index_of(key)
        if i < 0:
            return
        self._rows[i].update(changes)
        idx = self.index(i)
        self.dataChanged.emit(idx, idx)

    def sync(self, new_rows):
        """Move from the current rows to `new_rows` with minimal row signals."""
        new_keys = [r["key"] for r in new_rows]
        keep = set(new_keys)
        for i in range(len(self._rows) - 1, -1, -1):
            if self._rows[i]["key"] not in keep:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        for i, row in enumerate(new_rows):
            if i < len(self._rows) and self._rows[i]["key"] == row["key"]:
                if self._rows[i] != row:
                    self._rows[i] = row
                    self.dataChanged.emit(self.index(i), self.index(i))
                continue
            self.beginInsertRows(QModelIndex(), i, i)
            self._rows.insert(i, row)
            self.endInsertRows()


class SettingsBackend(QObject):
    """Everything the QML panel can ask for. Lives on the Qt GUI thread."""

    countsChanged = Signal()
    selectionChanged = Signal()
    fetchFinished = Signal("QVariantMap")
    loadErrorChanged = Signal()
    _checkDone = Signal(str, bool, str)
    _fetchDone = Signal("QVariantMap")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = ConnectionsModel(self)
        self._data = {}
        self._selected = set()
        self._closing = False
        self._load_error = ""
        self._display_to_id = {}
        self._bulk_models = []
        self._fetch_serial = 0
        self._checkDone.connect(self._finish_check)
        self._fetchDone.connect(self._finish_fetch)
        try:
            self._data = settings_store.load()
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc) or exc.__class__.__name__
        self._rebuild()

    # -- properties -----------------------------------------------------
    @Property(QObject, constant=True)
    def connections(self):
        return self._model

    @Property(str, notify=loadErrorChanged)
    def loadError(self):
        return self._load_error

    def _counts(self):
        return settings_store.connection_counts(self._data)

    @Property(int, notify=countsChanged)
    def connectedCount(self):
        return self._counts()["connected"]

    @Property(int, notify=countsChanged)
    def notConnectedCount(self):
        return self._counts()["not_connected"]

    @Property(int, notify=countsChanged)
    def notCheckedCount(self):
        return self._counts()["not_checked"]

    @Property(int, notify=selectionChanged)
    def selectedCount(self):
        live = {r["key"] for r in self._model.rows()}
        return len(self._selected & live)

    @Property("QVariantList", constant=True)
    def providerNames(self):
        return [AUTO_DETECT] + [p["name"] for p in pa.COMMON_PROVIDERS]

    # -- rows -----------------------------------------------------------
    def _entry(self, key):
        faculty, entry_id = _split(key)
        for entry in self._data.get(faculty, []):
            if entry.get("id") == entry_id:
                return faculty, entry
        return faculty, None

    def _row_for(self, faculty, entry, status=None):
        key = _key(faculty, entry["id"])
        return {
            "key": key, "faculty": faculty, "entryId": entry["id"],
            "name": entry.get("name", ""),
            "modelName": entry.get("model") or "\u2014",
            "maskedKey": settings_store.mask_key(entry.get("api_key", "")),
            "status": status or entry.get("status") or "unknown",
            "detail": entry.get("detail") or "",
            "selected": key in self._selected,
        }

    def _rebuild(self):
        checking = {r["key"] for r in self._model.rows() if r["status"] == "checking"}
        rows = []
        for faculty in settings_store.FACULTIES:
            for entry in self._data.get(faculty, []):
                key = _key(faculty, entry["id"])
                rows.append(self._row_for(
                    faculty, entry, "checking" if key in checking else None))
        self._model.sync(rows)
        self._selected &= {r["key"] for r in rows}
        self.countsChanged.emit()
        self.selectionChanged.emit()

    # -- selection ------------------------------------------------------
    @Slot(str, bool)
    def setSelected(self, key, on):
        if on:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._model.update_row(key, selected=bool(on))
        self.selectionChanged.emit()

    @Slot(bool)
    def selectAll(self, on):
        for row in self._model.rows():
            self.setSelected(row["key"], on)

    # -- removal ----------------------------------------------------------
    @Slot(str)
    def removeOne(self, key):
        faculty, entry_id = _split(key)
        settings_store.remove_model(self._data, faculty, entry_id)
        self._selected.discard(key)
        self._rebuild()

    @Slot()
    def removeSelected(self):
        for key in list(self._selected):
            faculty, entry_id = _split(key)
            settings_store.remove_model(self._data, faculty, entry_id)
        self._selected.clear()
        self._rebuild()

    @Slot()
    def removeDisconnected(self):
        settings_store.remove_disconnected_models(self._data)
        self._rebuild()

    @Slot(str, result=str)
    def nameOf(self, key):
        _faculty, entry = self._entry(key)
        return (entry or {}).get("name", "")

    # -- connectivity checks ----------------------------------------------
    @Slot(str)
    def check(self, key):
        if self._closing:
            return
        _faculty, entry = self._entry(key)
        if entry is None:
            return
        self._model.update_row(key, status="checking", detail="")
        snapshot = dict(entry)

        def worker():
            try:
                with _CHECK_SLOTS:
                    if self._closing:
                        return
                    ok, detail = settings_checks.run_check(snapshot)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, str(exc) or "The check failed unexpectedly."
            if not self._closing:
                self._checkDone.emit(key, bool(ok), str(detail or ""))

        threading.Thread(target=worker, name="FridaySettingsCheck",
                         daemon=True).start()

    @Slot()
    def checkAll(self):
        for faculty, entries in self._data.items():
            for entry in entries:
                if (entry.get("api_key") or entry.get("base_url")
                        or entry.get("check_kind", "").startswith("local")):
                    self.check(_key(faculty, entry["id"]))

    @Slot(str, bool, str)
    def _finish_check(self, key, ok, detail):
        if self._closing:
            return
        faculty, entry_id = _split(key)
        settings_store.update_check_result(self._data, faculty, entry_id, ok,
                                           detail, time.time())
        self._model.update_row(key, status="connected" if ok else "error",
                               detail=detail)
        self.countsChanged.emit()

    # -- add / edit dialog ------------------------------------------------
    @Slot(str, result="QVariantMap")
    def entryForEdit(self, key):
        faculty, entry = self._entry(key)
        if entry is None:
            return {}
        out = {"faculty": faculty, "name": entry.get("name", ""),
               "model": entry.get("model", ""),
               "base_url": entry.get("base_url", ""),
               "api_key": entry.get("api_key", ""),
               "api_style": entry.get("api_style", ""), "note": "",
               "model_hint": ""}
        if out["base_url"] or out["api_key"]:
            hint = (pa.preset_for_base_url(out["base_url"])
                    or pa.detect_provider(out["api_key"]))
            if hint:
                out["api_style"] = hint["api_style"]
                out["note"] = f"Recognized: {hint['name']}"
        out["model_hint"] = _model_hint_text(pa.preset_for_base_url(out["base_url"]))
        return out

    @Slot(str, result="QVariantMap")
    def pickPreset(self, name):
        hint = pa.preset_by_name(name)
        if not hint:
            return {}
        if hint["slug"] == "custom":
            return {"api_style": hint["api_style"], "base_url": None,
                    "note": "Fully custom -- fill the base URL below yourself.",
                    "model_hint": ""}
        note = f"Using {hint['name']}."
        if hint.get("no_key_ok"):
            note += " Local -- a key usually isn't needed."
        return {"api_style": hint["api_style"], "base_url": hint["base_url"],
                "note": note, "model_hint": _model_hint_text(hint)}

    @Slot(str, str, result="QVariantMap")
    def keyChanged(self, api_key, current_base):
        self.invalidateBulk()
        hint = pa.detect_provider(api_key)
        if hint:
            current = current_base.strip()
            refill = not current or pa.preset_for_base_url(current)
            return {"api_style": hint["api_style"],
                    "base_url": hint["base_url"] if refill else None,
                    "note": (f"Detected: {hint['name']} -- its base URL is "
                             "filled in for you."),
                    "model_hint": _model_hint_text(hint)}
        if api_key.strip():
            return {"note": ("This key's shape isn't recognized -- pick the "
                             "provider above, or fill the base URL yourself."),
                    "base_url": None}
        return {"note": "", "base_url": None}

    @Slot(str, result=str)
    def modelHintFor(self, base_url):
        return _model_hint_text(pa.preset_for_base_url(base_url))

    @Slot()
    def invalidateBulk(self):
        self._bulk_models = []

    @Slot(str, str, str)
    def fetchModels(self, base_url, api_key, api_style):
        base_url, api_key = base_url.strip(), api_key.strip()
        self._fetch_serial += 1
        serial = self._fetch_serial
        self.invalidateBulk()
        snapshot = {"base_url": base_url, "api_key": api_key,
                    "api_style": api_style or ""}

        def worker():
            try:
                result = settings_checks.fetch_model_list(
                    {"base_url": base_url, "api_key": api_key,
                     "api_style": api_style or None})
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            self._fetchDone.emit({"serial": serial, "snapshot": snapshot,
                                  "result": result})

        threading.Thread(target=worker, name="FridayFetchModels",
                         daemon=True).start()

    @Slot("QVariantMap")
    def _finish_fetch(self, payload):
        if payload["serial"] != self._fetch_serial:
            return
        result = payload["result"] or {}
        out = {"ok": bool(result.get("ok")), "values": [], "note": "",
               "bulkCount": 0, "snapshot": payload["snapshot"]}
        if not out["ok"]:
            out["note"] = ("Couldn't fetch the list -- type the model manually. "
                           f"({result.get('error') or 'unknown error'})")
            self.fetchFinished.emit(out)
            return
        ids = list(result.get("ids") or [])
        tiers = result.get("tiers")
        self._display_to_id = {}
        self._bulk_models = []
        note = (f"{len(ids)} model(s) from the provider itself -- pick from "
                "the list or type your own.")
        if tiers:
            self._bulk_models = list(dict.fromkeys(
                list(tiers.get("free") or []) + list(tiers.get("trial") or [])))
            values, counts = [], []
            for tier, label in _TIER_LABELS:
                bucket = tiers.get(tier) or []
                if bucket:
                    counts.append(f"{len(bucket)} price-unstated" if tier == "unstated"
                                  else f"{len(bucket)} {tier.upper()}")
                for model_id in bucket:
                    shown = f"{label} {model_id}"
                    values.append(shown)
                    self._display_to_id[shown] = model_id
            if values:
                ids = values
                note = (" \u00b7 ".join(counts) + " -- best-effort classes read "
                        "from the provider's own pricing metadata.")
            else:
                note = f"{len(ids)} model(s) -- the provider shared no pricing info."
        out["values"] = ids
        out["note"] = note
        out["bulkCount"] = len(self._bulk_models)
        self.fetchFinished.emit(out)

    def _style_and_hint(self, base_url, api_style):
        base_hint = pa.preset_for_base_url(base_url)
        style = (api_style or (base_hint["api_style"] if base_hint else "")
                 or pa.infer_api_style(base_url))
        return style, (base_hint["name"] if base_hint else "")

    @Slot(str, str, str, str, str, str, str, result=str)
    def saveEntry(self, edit_key, faculty, name, model, api_key, base_url,
                  api_style):
        """Add (edit_key empty) or update an entry. Returns an error or ''."""
        name, base_url, api_key = name.strip(), base_url.strip(), api_key.strip()
        model = model.strip()
        model = self._display_to_id.get(model, model)
        if not name:
            return "Give this model a display name."
        style, provider_hint = self._style_and_hint(base_url, api_style)
        if edit_key:
            _faculty, entry = self._entry(edit_key)
            if entry is None:
                return "That entry no longer exists."
            entry.update(name=name or entry["name"], model=model,
                         api_key=api_key, base_url=base_url)
            if style:
                entry["api_style"] = style
            if provider_hint:
                entry["provider_hint"] = provider_hint
            settings_store.save(self._data)
            self._rebuild()
            return ""
        entry = settings_store.add_model(self._data, faculty, name, model,
                                         api_key, base_url)
        if style:
            entry["api_style"] = style
        if provider_hint:
            entry["provider_hint"] = provider_hint
        if style or provider_hint:
            settings_store.save(self._data)
        self._rebuild()
        if api_key or base_url:
            self.check(_key(faculty, entry["id"]))
        return ""

    @Slot(str, str, str, str, str, result="QVariantMap")
    def addFreeTrial(self, faculty, name, api_key, base_url, api_style):
        models = list(self._bulk_models)
        if not models:
            return {"ok": False, "message": "Fetch models first."}
        if not name.strip():
            return {"ok": False, "message":
                    "Give this provider a display name before adding its models."}
        base_url = base_url.strip()
        style, provider_hint = self._style_and_hint(base_url, api_style)
        try:
            added = settings_store.add_models(
                self._data, faculty, name.strip(), models, api_key.strip(),
                base_url, api_style=style, provider_hint=provider_hint)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc) or exc.__class__.__name__}
        self._rebuild()
        count = len(added or [])
        if not count:
            return {"ok": True, "message":
                    "These free/trial models are already in the connection list."}
        return {"ok": True, "message":
                f"Added {count} model row(s). They stay unchecked until you "
                "check them; no generation calls were made."}

    # -- profile ------------------------------------------------------------
    @Slot(result="QVariantMap")
    def loadProfile(self):
        return user_profile.load()

    @Slot("QVariantMap", result=str)
    def saveProfile(self, profile):
        data = user_profile.default_profile()
        for key in ("name", "dev_names", "bio", "interests", "goals", "photo"):
            data[key] = str(profile.get(key) or "").strip()
        links = profile.get("links") or {}
        for key in data["links"]:
            data["links"][key] = str(links.get(key) or "").strip()
        apps = str(profile.get("connected_apps") or "")
        data["connected_apps"] = [a.strip() for a in apps.split(",") if a.strip()]
        for key in ("emails", "github_authors", "credentials"):
            data[key] = [dict(row) for row in (profile.get(key) or [])]
        try:
            user_profile.save(data)
        except Exception as exc:  # noqa: BLE001
            return "Save FAILED: " + str(exc)
        return "Saved \u2014 Friday now knows " + (data["name"] or "you") + "."

    @Slot(str, result=str)
    def photoUrl(self, text):
        text = (text or "").strip()
        if text.lower().startswith(("http://", "https://", "file:")):
            return text
        if text and os.path.isfile(os.path.expanduser(text)):
            return QUrl.fromLocalFile(os.path.expanduser(text)).toString()
        return ""

    @Slot(str, result=str)
    def localPath(self, url):
        return QUrl(url).toLocalFile() or url

    # -- lifecycle --------------------------------------------------------
    @Slot()
    def shutdown(self):
        self._closing = True
