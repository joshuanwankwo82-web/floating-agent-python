"""Qt bridge between the QML Settings panel and Friday's existing settings code.

No settings logic lives here that isn't already in settings_store,
settings_checks, provider_autodetect or user_profile -- this file only
adapts them to Qt: a list model for the connection rows, slots for the
buttons, and signals for results that arrive from worker threads.
"""
import os
import sys
import threading
import time

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Qt,
                            QTimer, QUrl, Property, Signal, Slot)

import model_kinds
import model_rank
import token_meter
import provider_autodetect as pa
import settings_checks
import settings_store
import user_profile

AUTO_DETECT = "-- auto-detect from my key --"
_CHECK_SLOTS = threading.BoundedSemaphore(4)
_SEP = "\x1f"
# The Keys & Connect tabs: (id, label, storage buckets shown). "All" shows
# every bucket, including services that fit no tab (Research = web search).
CATEGORIES = [
    ("all", "All", None),
    ("text", "Text", ("Voice", "Thinking", "Planning", "Review", "Verify",
                      "Coding", "Text Generation")),
    ("image", "Image", ("Image Generation",)),
    ("video", "Video", ("Video Generation",)),
    ("audio", "Audio", ("Audio Generation",)),
    ("transcription", "Transcription", ("Transcription",)),
    ("embedding", "Embedding", ("Embedding",)),
]
_CATEGORY_FACULTIES = {cid: facs for cid, _label, facs in CATEGORIES}
# Where "+ Add model" puts a new entry while a tab is open.
_ADD_FACULTY = {"all": "Thinking", "text": "Thinking",
                "image": "Image Generation", "video": "Video Generation",
                "audio": "Audio Generation", "transcription": "Transcription",
                "embedding": "Embedding"}
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
    ide = sys.modules.get("ide")
    colors = dict(FALLBACK_THEME)
    if ide is not None and isinstance(getattr(ide, "C", None), dict):
        colors.update({k: v for k, v in ide.C.items()
                       if k in colors and isinstance(v, str)})
    return colors


def _refresh_chat_badges():
    """Open chat headers re-read the user's name/photo right away. Chat views
    are Tk widgets on their own thread; tkinter queues the after() call onto
    that thread, and a worker thread does the waiting so Qt never blocks."""
    view = sys.modules.get("friday_chat.view")
    refresh = getattr(view, "refresh_presence_from_any_thread", None)
    if refresh is not None:
        threading.Thread(target=refresh, name="FridayPresenceRefresh",
                         daemon=True).start()


def _key(faculty, entry_id):
    return f"{faculty}{_SEP}{entry_id}"


def _split(key):
    faculty, _, entry_id = key.partition(_SEP)
    return faculty, entry_id


def _model_hint_text(hint):
    if hint and hint.get("model_hint"):
        return f"e.g. {hint['model_hint']}  \u2014  or use Fetch models"
    return ""


_SCORE_SOURCES = {
    "aa": "Artificial Analysis Intelligence Index (via OpenRouter)",
    "arena": "Design Arena Elo (via OpenRouter)",
    "price": "estimate -- no benchmark published for this model; inferred from its "
             "OpenRouter price, release date and context length",
    "family": "estimate -- no benchmark published for this model; taken from the "
              "nearest benchmarked model in its family",
}
# Hosts of Friday's built-in entries, for naming providers in the token totals.
_BUILTIN_HOSTS = {"generativelanguage.googleapis.com": "Google Gemini",
                  "api.groq.com": "Groq", "api.mistral.ai": "Mistral",
                  "api.stability.ai": "Stability AI", "api.ideogram.ai": "Ideogram",
                  "api.cloudflare.com": "Cloudflare Workers AI",
                  "api.openai.com": "OpenAI"}


def _provider_name(host, entries_by_host):
    """A token-total host -> the name people know it by: a preset's name,
    else one of the user's own entries on that host, else the host."""
    if host in _BUILTIN_HOSTS:
        return _BUILTIN_HOSTS[host]
    names = [p["name"] for p in pa.COMMON_PROVIDERS
             if token_meter.host_of(p.get("base_url") or "") == host]
    if names:
        name = min(names, key=len)
        return name.split(" (")[0]
    return entries_by_host.get(host) or host or "unknown"
# Unscored models sort by their latest check: working ones first.
_STATUS_ORDER = {"connected": 0, "unknown": 1, "error": 2}


def _token_key(entry):
    return token_meter.key_for(token_meter.host_of(entry), entry.get("model") or "")


def _rank_key(entry, kind, position):
    """Best first: scored models by their standing among every model scored
    for that kind (so a top image model and a top text model sit together
    in the All tab), then unscored models by status, then as added."""
    model = entry.get("model") or ""
    pct = model_rank.percentile(model, kind)
    if pct is not None:
        return (0, -pct, -(model_rank.score_for(model, kind) or (0,))[0], position)
    status = _STATUS_ORDER.get(entry.get("status") or "unknown", 1)
    return (1, status, 0, position)


class ConnectionsModel(QAbstractListModel):
    """One row per configured model/service, in settings_store.FACULTIES order.

    Rows are inserted/removed individually (never a full reset) so the
    QML ListView can animate additions, removals and the rows that move
    to fill the gap.
    """
    ROLES = ("key", "faculty", "entryId", "name", "modelName", "maskedKey",
             "status", "detail", "selected", "score", "scoreSource",
             "scoreEstimated", "tokens", "tokenTotal")

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
        """Move from the current rows to `new_rows` with minimal row signals
        (removes, moves -- the ranking reorders rows -- inserts, changes)."""
        new_keys = [r["key"] for r in new_rows]
        keep = set(new_keys)
        for i in range(len(self._rows) - 1, -1, -1):
            if self._rows[i]["key"] not in keep:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        for i, row in enumerate(new_rows):
            j = next((n for n in range(i, len(self._rows))
                      if self._rows[n]["key"] == row["key"]), -1)
            if j < 0:
                self.beginInsertRows(QModelIndex(), i, i)
                self._rows.insert(i, row)
                self.endInsertRows()
                continue
            if j != i:
                self.beginMoveRows(QModelIndex(), j, j, QModelIndex(), i)
                self._rows.insert(i, self._rows.pop(j))
                self.endMoveRows()
            if self._rows[i] != row:
                self._rows[i] = row
                self.dataChanged.emit(self.index(i), self.index(i))


class SettingsBackend(QObject):
    """Everything the QML panel can ask for. Lives on the Qt GUI thread."""

    countsChanged = Signal()
    categoryChanged = Signal()
    selectionChanged = Signal()
    fetchFinished = Signal("QVariantMap")
    notice = Signal(str, str)
    loadErrorChanged = Signal()
    tokensChanged = Signal()
    _tokensDirty = Signal()           # from token_meter, on any thread
    _scoresRefreshed = Signal()
    _checkDone = Signal(str, bool, str)
    _fetchDone = Signal("QVariantMap")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = ConnectionsModel(self)
        self._data = {}
        self._selected = set()
        self._checking = set()
        self._category = "all"
        self._closing = False
        self._load_error = ""
        self._display_to_id = {}
        self._bulk_models = []
        self._kinds = {}          # model id -> {"kind", "source"} from the last fetch
        self._fetch_faculty = ""
        self._fetch_serial = 0
        self._checkDone.connect(self._finish_check)
        self._fetchDone.connect(self._finish_fetch)
        # Token counts arrive from worker threads; repaint at most ~3x/second.
        self._token_timer = QTimer(self)
        self._token_timer.setSingleShot(True)
        self._token_timer.setInterval(300)
        self._token_timer.timeout.connect(self._refresh_tokens)
        self._tokensDirty.connect(self._token_timer.start)
        token_meter.add_listener(self._tokensDirty.emit)
        self._scoresRefreshed.connect(self._rebuild)
        try:
            self._data = settings_store.load()
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc) or exc.__class__.__name__
        self._rebuild()
        model_rank.refresh_in_background(self._scoresRefreshed.emit)

    # -- properties -----------------------------------------------------
    @Property(QObject, constant=True)
    def connections(self):
        return self._model

    @Property(str, notify=loadErrorChanged)
    def loadError(self):
        return self._load_error

    def _counts(self):
        return settings_store.connection_counts(
            self._data, _CATEGORY_FACULTIES[self._category])

    # -- tabs -------------------------------------------------------------
    @Property("QVariantList", constant=True)
    def categories(self):
        return [{"id": cid, "label": label} for cid, label, _f in CATEGORIES]

    @Property(str, notify=categoryChanged)
    def category(self):
        return self._category

    @Property(str, notify=categoryChanged)
    def addFaculty(self):
        return _ADD_FACULTY[self._category]

    @Property("QVariantMap", notify=countsChanged)
    def categoryCounts(self):
        """Models per tab, e.g. {"all": 12, "text": 7, ...}."""
        out = {}
        for cid, _label, facs in CATEGORIES:
            out[cid] = sum(len(entries) for fac, entries in self._data.items()
                           if facs is None or fac in facs)
        return out

    def _reveal(self, faculty):
        """A new entry landing outside the open tab switches to the tab that
        shows it (or All), so it never seems to vanish. Caller rebuilds."""
        shown = _CATEGORY_FACULTIES[self._category]
        if shown is None or faculty in shown:
            return
        self._category = next((cid for cid, _l, facs in CATEGORIES
                               if facs and faculty in facs), "all")
        self.categoryChanged.emit()

    @Slot(str)
    def setCategory(self, category):
        if category not in _CATEGORY_FACULTIES or category == self._category:
            return
        self._category = category
        self.categoryChanged.emit()
        self._rebuild()

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
        kind = model_kinds.faculty_kind(faculty) or "text"
        hit = model_rank.score_for(entry.get("model") or "", kind)
        used = token_meter.model_total(_token_key(entry))
        return {
            "key": key, "faculty": faculty, "entryId": entry["id"],
            "name": entry.get("name", ""),
            "modelName": entry.get("model") or "\u2014",
            "maskedKey": settings_store.mask_key(entry.get("api_key", "")),
            "status": status or entry.get("status") or "unknown",
            "detail": entry.get("detail") or "",
            "selected": key in self._selected,
            "score": model_rank.score_label(entry.get("model") or "", kind),
            "scoreSource": _SCORE_SOURCES.get(hit[1], "") if hit else "",
            "scoreEstimated": bool(hit) and model_rank.is_estimate(hit[1]),
            "tokens": token_meter.short(used) if used else "",
            "tokenTotal": used,
        }

    def _rebuild(self):
        shown = _CATEGORY_FACULTIES[self._category]
        ranked = []
        for order, faculty in enumerate(settings_store.FACULTIES):
            if shown is not None and faculty not in shown:
                continue
            kind = model_kinds.faculty_kind(faculty) or "text"
            for entry in self._data.get(faculty, []):
                key = _key(faculty, entry["id"])
                row = self._row_for(
                    faculty, entry, "checking" if key in self._checking else None)
                ranked.append((_rank_key(entry, kind, len(ranked)), row))
        rows = [row for _k, row in sorted(ranked, key=lambda pair: pair[0])]
        self._model.sync(rows)
        self._selected &= {r["key"] for r in rows}
        self.countsChanged.emit()
        self.selectionChanged.emit()
        self.tokensChanged.emit()

    # -- token calculator ---------------------------------------------------
    @Property("QVariantMap", notify=tokensChanged)
    def tokenUsage(self):
        """What the token calculator shows: everything Friday has pulled from
        its models (the headline), plus today's, the open tab's share, the
        in/out split and the biggest users. Numbers come as text -- totals
        can outgrow a 32-bit int."""
        everything = token_meter.usage_for()
        tab_keys = {_token_key(self._entry(r["key"])[1] or {}) for r in self._model.rows()}
        tab = token_meter.usage_for(tab_keys)
        names, entries_by_host = {}, {}
        for faculty in settings_store.FACULTIES:
            for entry in self._data.get(faculty, []):
                names.setdefault(_token_key(entry), entry.get("name") or "")
                entries_by_host.setdefault(token_meter.host_of(entry), entry.get("name") or "")
        providers = []
        grand = everything["total"] or 1
        for host, h in token_meter.by_host():
            providers.append({
                "name": _provider_name(host, entries_by_host), "host": host,
                "tokens": token_meter.short(h["total"]), "exact": f"{h['total']:,}",
                "share": h["total"] / grand, "calls": h["calls"], "models": h["models"]})
        top = []
        for key, total in token_meter.top_models(6):
            host, _sep, model = key.partition("|")
            top.append({"name": names.get(key) or host, "model": model,
                        "tokens": token_meter.short(total),
                        "connected": key in names})
        return {
            "total": token_meter.short(everything["total"]),
            "exact": f"{everything['total']:,}",
            "input": f"{everything['in']:,}",
            "output": f"{everything['out']:,}",
            "today": token_meter.short(everything["today"]),
            "calls": f"{everything['calls']:,}",
            "estimated": token_meter.short(everything["estimated"]),
            "hasEstimated": everything["estimated"] > 0,
            "tab": token_meter.short(tab["total"]),
            "since": time.strftime("%d %b %Y", time.localtime(everything["since"]))
                     if everything["since"] else "",
            "top": top,
            "providers": providers,
            "providerCount": len(providers),
            "empty": everything["total"] == 0,
        }

    @Slot()
    def _refresh_tokens(self):
        if self._closing:
            return
        for row in list(self._model.rows()):
            _faculty, entry = self._entry(row["key"])
            if entry is None:
                continue
            used = token_meter.model_total(_token_key(entry))
            if used != row.get("tokenTotal"):
                self._model.update_row(row["key"], tokenTotal=used,
                                       tokens=token_meter.short(used) if used else "")
        self.tokensChanged.emit()

    @Slot()
    def resetTokens(self):
        token_meter.reset()
        token_meter.flush()
        self._refresh_tokens()

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
        settings_store.remove_disconnected_models(
            self._data, _CATEGORY_FACULTIES[self._category])
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
        self._checking.add(key)
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
        self._checking.discard(key)
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
        self._kinds = {}

    @Slot(str, str, str, str)
    def fetchModels(self, base_url, api_key, api_style, faculty=""):
        base_url, api_key = base_url.strip(), api_key.strip()
        self._fetch_faculty = faculty or ""
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
        self._kinds = dict(result.get("kinds") or {})
        self._display_to_id = {}
        self._bulk_models = []
        wanted = model_kinds.faculty_kind(self._fetch_faculty)

        def kind_of(model_id):
            return (self._kinds.get(model_id) or {}).get("kind", "text")

        def shown(model_id, tier_label=""):
            kind = kind_of(model_id)
            tag = "" if kind == "text" else f"[{model_kinds.LABELS[kind]}] "
            text = f"{tier_label} {tag}{score_tag(model_id)}{model_id}".strip()
            self._display_to_id[text] = model_id
            return text

        def score_tag(model_id):
            label = model_rank.score_label(model_id, kind_of(model_id))
            return f"\u2605{label} " if label else ""

        # The open tab's kind first (an Image tab lists image models on top),
        # then best first within each kind; unscored keep the provider's order.
        def rank(model_id, position):
            kind = kind_of(model_id)
            hit = model_rank.score_for(model_id, kind)
            return (0 if not wanted or kind == wanted else 1,
                    0 if hit else 1, -(hit[0] if hit else 0), position)

        def ordered(model_ids):
            model_ids = list(model_ids)
            return [m for _r, m in sorted(
                (rank(m, n), m) for n, m in enumerate(model_ids))]

        tier_note = ""
        if tiers:
            self._bulk_models = [m for m in dict.fromkeys(
                list(tiers.get("free") or []) + list(tiers.get("trial") or []))
                if kind_of(m) != "other"]
            pairs, counts = [], []
            for tier, label in _TIER_LABELS:
                bucket = tiers.get(tier) or []
                if bucket:
                    counts.append(f"{len(bucket)} price-unstated" if tier == "unstated"
                                  else f"{len(bucket)} {tier.upper()}")
                pairs += [(m, label) for m in bucket]
            if pairs:
                # Kind first, then the price tier (FREE above paid), then best first.
                tier_rank = {label: n for n, (_t, label) in enumerate(_TIER_LABELS)}
                pairs = [p for _r, p in sorted(
                    ((rank(m, n)[0], tier_rank.get(label, 99)) + rank(m, n)[1:], (m, label))
                    for n, (m, label) in enumerate(pairs))]
                ids = [shown(m, label) for m, label in pairs]
                tier_note = " \u00b7 ".join(counts) + " (prices from the provider's own metadata)."
        if not tier_note:
            ids = [shown(m) for m in ordered(ids)]
        out["values"] = ids
        out["note"] = self._kinds_note(len(ids)) + (" " + tier_note if tier_note else "")
        hits = [model_rank.score_for(m, kind_of(m)) for m in self._display_to_id.values()]
        measured = sum(1 for h in hits if h and not model_rank.is_estimate(h[1]))
        estimated = sum(1 for h in hits if h and model_rank.is_estimate(h[1]))
        if measured or estimated:
            out["note"] += (f" Best first: \u2605 = quality score ({measured} benchmarked "
                            "-- Artificial Analysis index for text, Design Arena Elo for "
                            f"image/audio, via OpenRouter; {estimated} estimated, shown "
                            "as \u2605~); unscored follow.")
        out["bulkCount"] = len(self._bulk_models)
        self.fetchFinished.emit(out)

    def _kinds_note(self, count):
        """'739 models: 454 text, 74 image, ... -- sorted by the provider's
        own labels (12 guessed from the name).'"""
        if not self._kinds:
            return f"{count} model(s) from the provider itself -- pick from the list or type your own."
        tally = {}
        guessed = 0
        for info in self._kinds.values():
            tally[info["kind"]] = tally.get(info["kind"], 0) + 1
            guessed += info["source"] != "provider"
        parts = [f"{tally[k]} {model_kinds.LABELS[k].lower()}"
                 for k in model_kinds.KINDS if tally.get(k)]
        note = f"{len(self._kinds)} models: " + ", ".join(parts)
        if guessed == len(self._kinds):
            return note + " -- sorted by model name (this provider doesn't label its models)."
        if guessed:
            return note + f" -- sorted by the provider's own labels ({guessed} by model name)."
        return note + " -- sorted by the provider's own labels."


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
        info = self._kinds.get(model)
        if info and info["kind"] != "other":
            target = model_kinds.faculty_for(info["kind"], faculty)
            if target and target != faculty:
                where = "the provider" if info["source"] == "provider" else "its name"
                self.notice.emit("Saved under " + model_kinds.LABELS[info["kind"]],
                                 f"{model} is a {model_kinds.LABELS[info['kind']].lower()} "
                                 f"model (going by {where}), so it was saved under "
                                 f"{target} instead of {faculty}.")
                faculty = target
        entry = settings_store.add_model(self._data, faculty, name, model,
                                         api_key, base_url)
        if style:
            entry["api_style"] = style
        if provider_hint:
            entry["provider_hint"] = provider_hint
        if style or provider_hint:
            settings_store.save(self._data)
        self._reveal(faculty)
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
        groups = {}                         # faculty -> [model ids], in order
        for model_id in models:
            kind = (self._kinds.get(model_id) or {}).get("kind", "text")
            target = model_kinds.faculty_for(kind, faculty)
            if target:
                groups.setdefault(target, []).append(model_id)
        added_by = {}
        try:
            for target, group in groups.items():
                added_by[target] = settings_store.add_models(
                    self._data, target, name.strip(), group, api_key.strip(),
                    base_url, api_style=style, provider_hint=provider_hint) or []
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc) or exc.__class__.__name__}
        count = sum(len(v) for v in added_by.values())
        if count:
            # Show the bucket that got most of them.
            self._reveal(max(added_by, key=lambda f: len(added_by[f])))
        self._rebuild()
        if not count:
            return {"ok": True, "message":
                    "These free/trial models are already in the connection list."}
        spread = ", ".join(f"{len(v)} to {f}" for f, v in added_by.items() if v)
        return {"ok": True, "message":
                f"Added {count} model row(s), each under its own kind: {spread}. "
                "They stay unchecked until you check them; no generation calls were made."}

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
        _refresh_chat_badges()
        return "Saved \u2014 Friday now knows " + (data["name"] or "you") + "."

    @Slot(str, result=str)
    def maskSecret(self, secret):
        """Fixed-length mask from user_profile (never leaks the length)."""
        return user_profile.mask(secret)

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
        token_meter.remove_listener(self._tokensDirty.emit)
        token_meter.flush()
