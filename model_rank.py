"""How good is a model? -- the ranking behind Settings' model lists.

Scores are independent benchmarks that OpenRouter republishes in its public
model catalog (no key needed):

  text   Artificial Analysis Intelligence Index (0-100 scale). A model with
         only Design Arena results gets the AA value at the same percentile
         (quantile mapping over the models that have both), so one scale
         ranks every text model.
  image  Design Arena Elo, image category.
  audio  Design Arena Elo, text-to-speech / audio-realism categories.

Most models have no published benchmark (older ones such as gpt-4o-mini,
small open models, provider-specific builds), so two clearly-labelled
ESTIMATES fill the gaps -- the lists show them as "~42" instead of "42":

  price   text models in OpenRouter's catalog: a least-squares fit of the
          measured index on log(price per output token), release date and
          context length (rank correlation ~0.86 on the measured models,
          mean error ~8 points), applied to the unbenchmarked ones.
  family  any model, any provider: the nearest benchmarked sibling in the
          same family (qwen3-5-27b <- qwen3-5-35b-a3b, llama-3-1-8b <-
          llama-3-1-70b), shifted by 2.5 points per doubling of parameters
          for text. Mean error ~11 points in a leave-one-out test.

Measured scores always win; an estimate is only used when there is none.
Video, transcription and embedding models have no public cross-provider
score; they rank by connection status instead.

The scores ship as model_scores.json (a snapshot) and are refreshed from
OpenRouter at most once a week into Friday's memory folder; a Fetch models
on OpenRouter refreshes them too. A provider's own model id is matched to
the scored name with norm() -- "openai/gpt-oss-120b:free", "gpt-oss:120b"
and "GPT-OSS-120B" are the same model.
"""
import bisect
import json
import os
import re
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(_HERE, "model_scores.json")
CATALOG_URL = "https://openrouter.ai/api/v1/models?output_modalities=all"
REFRESH_AFTER_S = 7 * 24 * 3600

_SUFFIX = re.compile(r"(-(free|tee|fp8|fp16|bf16|fp4|nvfp4|mxfp4|int4|int8|awq|gguf|pt|instruct|it|chat"
                     r"|latest|preview|exp|hf|maas|versatile|fast|turbo|batch))+$")
_DATE = re.compile(r"-(\d{4}(-\d{2}){0,2}|\d{6,8})$")
_VARIANT = re.compile(r"(-(thinking|think|nothinking|reasoning|high|low|medium|minimal))+$")


# Orgs that prefix their own brand onto model names (NVIDIA-Nemotron-...),
# unlike deepseek/deepseek-v3 or qwen/qwen-image where it IS the name.
_BRAND_ORGS = ("nvidia", "microsoft", "ibm", "amazon", "tencent", "baidu")


def norm(model_id):
    """A provider's model id -> the name it's scored under."""
    s = str(model_id or "").strip().lower()
    s = s.split("@")[0]                                # requesty region suffix
    parts = s.split("/")
    s = parts[-1]
    if len(parts) > 1 and parts[-2] in _BRAND_ORGS and s.startswith(parts[-2] + "-"):
        s = s[len(parts[-2]) + 1:]                     # nvidia/NVIDIA-Nemotron-...
    s = re.sub(r"\[.*?\]", "", s)                      # claude-opus-5[1m]
    if re.search(r":\d+(\.\d+)?b$", s):                # ollama gpt-oss:120b
        s = s.replace(":", "-")
    s = s.split(":")[0]                                # :free :beta :batch
    s = re.sub(r"(?<=\d)p(?=\d)", ".", s)              # fireworks glm-5p2
    s = s.replace("_", "-").replace(" ", "-").replace(".", "-")
    for _ in range(3):
        trimmed = _DATE.sub("", _SUFFIX.sub("", s))
        if trimmed == s:
            break
        s = trimmed
    s = re.sub(r"^meta-(llama)", r"\1", s)
    s = re.sub(r"^(llama|gemma|phi)(?=\d)", r"\1-", s)  # ollama llama3.3 / gemma3 / phi4
    return s.strip("-")


# -- building scores from OpenRouter's catalog --------------------------------
def _elo(entries, categories):
    vals = [e.get("elo") for e in entries or []
            if isinstance(e, dict) and e.get("category") in categories
            and isinstance(e.get("elo"), (int, float))]
    return sum(vals) / len(vals) if vals else None


def _price_features(item):
    """[log10 $/M output tokens, years since 2023-11, log2 context, 1] or None."""
    import math
    try:
        out = float((item.get("pricing") or {}).get("completion") or 0)
    except (TypeError, ValueError):
        return None
    created = item.get("created") or 0
    if out <= 0 or not created:
        return None
    ctx = item.get("context_length") or 0
    return [math.log10(out * 1e6), (created - 1.7e9) / 3.15e7,
            math.log2(max(int(ctx), 1024)), 1.0]


def _solve(rows, ys):
    """Least squares via the normal equations (4 unknowns, pure Python)."""
    n = len(rows[0])
    a = [[sum(r[i] * r[j] for r in rows) + (1e-6 if i == j else 0) for j in range(n)]
         for i in range(n)]
    b = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(n)]
    for col in range(n):                                  # Gauss-Jordan
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[piv], b[col], b[piv] = a[piv], a[col], b[piv], b[col]
        div = a[col][col] or 1e-12
        a[col] = [v / div for v in a[col]]
        b[col] /= div
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
                b[r] -= f * b[col]
    return b


def _price_estimates(items, measured):
    """{name: estimated index} for priced, unbenchmarked OpenRouter text models."""
    import model_kinds
    feats = {}
    for item in items or []:
        if not isinstance(item, dict) or model_kinds.classify(item)[0] != "text":
            continue
        f = _price_features(item)
        name = norm(item.get("id"))
        if f and name and (name not in feats or f[0] > feats[name][0]):
            feats[name] = f
    train = [n for n in feats if n in measured]
    if len(train) < 15:
        return {}
    w = _solve([feats[n] for n in train], [measured[n] for n in train])
    import math
    lo, hi = min(measured.values()), max(measured.values())
    out = {}
    for name, f in feats.items():
        if name not in measured:
            est = sum(x * y for x, y in zip(f, w))
            # The fit is trained on recent models; for older ones it runs
            # below the scale. Squash that tail into (0, lowest measured)
            # so the order survives without inventing negative scores.
            if est < lo:
                est = lo * math.exp((est - lo) / 30.0)
            out[name] = round(min(hi, est), 1)
    return out


_SIZE = re.compile(r"^(a?)(\d+(?:\.\d+)?)([bmt])$")


def _size_b(name):
    """Total parameters in billions from a name (qwen3-235b-a22b -> 235)."""
    size = None
    for tok in name.split("-"):
        m = _SIZE.match(tok)
        if m and not m.group(1):
            size = float(m.group(2)) * {"b": 1, "m": 1e-3, "t": 1e3}[m.group(3)]
    return size


def _family(name):
    return [t for t in name.split("-") if not _SIZE.match(t)]


def _sibling(name, table, per_doubling):
    """Estimate from the closest scored model of the same family: the longest
    shared family prefix (at least two parts), nearest in size."""
    import math
    fam, size = _family(name), _size_b(name)
    bases = [(m, v[0]) for m, v in table.items() if v[1] in MEASURED + ("price",)]
    for length in range(len(fam), 1, -1):
        prefix = fam[:length]
        cands = [(m, v) for m, v in bases if _family(m)[:length] == prefix]
        if not cands:
            continue
        sized = [(m, v) for m, v in cands if _size_b(m)] if size else []
        if sized and per_doubling:
            m, v = min(sized, key=lambda mv: abs(math.log(_size_b(mv[0]) / size)))
            shift = max(-10.0, min(10.0, per_doubling * math.log2(size / _size_b(m))))
            return v + shift
        values = sorted(v for _m, v in cands)
        return values[len(values) // 2]
    return None


MEASURED = ("aa", "arena")
ESTIMATED = {"price": "estimated from its OpenRouter price, release date and context",
             "family": "estimated from the nearest benchmarked model in its family"}


def build_scores(items):
    """OpenRouter catalog items -> {"text": {name: [score, source]}, "image":
    {...}, "audio": {...}, "built": epoch}. source: "aa" (Artificial
    Analysis), "arena" (Design Arena, mapped onto the AA scale for text)."""
    import model_kinds
    aa, arena_text, image, audio = {}, {}, {}, {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = norm(item.get("id"))
        if not name:
            continue
        bench = item.get("benchmarks") or {}
        kind = model_kinds.classify(item)[0]
        index = (bench.get("artificial_analysis") or {}).get("intelligence_index")
        arena = bench.get("design_arena") or []
        if kind == "text":
            if isinstance(index, (int, float)):
                aa[name] = max(aa.get(name, 0), float(index))
            elo = _elo(arena, {e.get("category") for e in arena
                               if isinstance(e, dict) and e.get("arena") == "models"})
            if elo is not None:
                arena_text[name] = elo
        elif kind == "image":
            elo = _elo(arena, {"image", "graphicdesign", "logo", "imageediting"})
            if elo is not None:
                image[name] = round(elo)
        elif kind == "audio":
            elo = _elo(arena, {"text-to-speech", "audiorealism"})
            if elo is not None:
                audio[name] = round(elo)

    text = {name: [round(v, 1), "aa"] for name, v in aa.items()}
    both = [n for n in arena_text if n in aa]
    if len(both) >= 5:                       # quantile-map arena Elo onto AA
        elos = sorted(arena_text[n] for n in both)
        idx = sorted(aa[n] for n in both)
        for name, elo in arena_text.items():
            if name in text:
                continue
            p = bisect.bisect_left(elos, elo) / max(1, len(elos) - 1)
            p = min(1.0, max(0.0, p))
            text[name] = [round(idx[round(p * (len(idx) - 1))], 1), "arena"]
    for name, est in _price_estimates(items, aa).items():
        text.setdefault(name, [est, "price"])          # measured scores win
    return {"built": int(time.time()), "text": text,
            "image": {n: [v, "arena"] for n, v in image.items()},
            "audio": {n: [v, "arena"] for n, v in audio.items()}}


# -- loading / refreshing ------------------------------------------------------
_lock = threading.Lock()
_scores = None
_refreshing = [False]


def _cache_path():
    """Next to Friday's settings file (memory folder / settings)."""
    import memory
    return os.path.join(memory._ensure_folder("settings"), "model_scores.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and "text" in data else None
    except Exception:  # noqa: BLE001
        return None


def scores():
    """The freshest scores available (cache, else the bundled snapshot)."""
    global _scores
    with _lock:
        if _scores is None:
            cached, bundled = _read(_cache_path()), _read(BUNDLED)
            pick = [s for s in (cached, bundled) if s]
            _scores = max(pick, key=lambda s: s.get("built", 0)) if pick else \
                {"built": 0, "text": {}, "image": {}, "audio": {}}
        return _scores


def update_from_catalog(items):
    """Rebuild from OpenRouter catalog items (only if they carry benchmarks)."""
    global _scores
    if not any(isinstance(i, dict) and i.get("benchmarks") for i in items or []):
        return False
    built = build_scores(items)
    if len(built["text"]) < 20:              # a partial list -- keep what we have
        return False
    with _lock:
        _scores = built
    try:
        path = _cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(built, f)
    except Exception:  # noqa: BLE001
        pass
    return True


def refresh_in_background(on_done=None):
    """Re-download the catalog if the scores are over a week old."""
    if _refreshing[0] or time.time() - scores().get("built", 0) < REFRESH_AFTER_S:
        return

    def work():
        _refreshing[0] = True
        try:
            import requests
            resp = requests.get(CATALOG_URL, timeout=15)
            if resp.status_code == 200 and update_from_catalog(resp.json().get("data")):
                if on_done:
                    on_done()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _refreshing[0] = False

    threading.Thread(target=work, name="FridayModelScores", daemon=True).start()


# -- lookups -------------------------------------------------------------------
_family_cache = {}


def score_for(model_id, kind="text", estimates=True):
    """(score, source) or None. kind is a model_kinds kind. source is "aa" /
    "arena" (measured) or "price" / "family" (estimated -- see the module
    docstring); estimates=False returns measured scores only."""
    table = scores().get(kind if kind in ("text", "image", "audio") else "", {})
    if not table:
        return None
    name = norm(model_id)
    hit = table.get(name) or table.get(_VARIANT.sub("", name))
    if hit and (estimates or hit[1] in MEASURED):
        return hit[0], hit[1]
    if not estimates or not name:
        return None
    key = (id(table), kind, name)
    if key not in _family_cache:
        if len(_family_cache) > 5000:
            _family_cache.clear()
        est = _sibling(_VARIANT.sub("", name), table, 2.5 if kind == "text" else 0)
        _family_cache[key] = None if est is None else (
            round(est, 1) if kind == "text" else round(est), "family")
    return _family_cache[key]


def is_estimate(source):
    return source in ESTIMATED


_sorted_cache = {}


def percentile(model_id, kind="text"):
    """0..100 -- the share of that kind's scored models this one beats or
    ties. Lets the All tab rank an image model (Elo) beside a text model
    (intelligence index) by standing rather than raw number. None = unscored."""
    hit = score_for(model_id, kind)
    if not hit:
        return None
    table = scores().get(kind, {})
    cached = _sorted_cache.get(kind)
    if cached is None or cached[0] is not table:
        cached = _sorted_cache[kind] = (table, sorted(v[0] for v in table.values()))
    values = cached[1]
    import bisect
    return 100.0 * bisect.bisect_right(values, hit[0]) / len(values)


def score_label(model_id, kind="text"):
    """'62.1' / 'Elo 1320' measured, '~41' / '~Elo 1200' estimated, or ''."""
    hit = score_for(model_id, kind)
    if not hit:
        return ""
    value = (f"{hit[0]:.0f}" if hit[0] >= 10 else f"{hit[0]:.1f}") \
        if is_estimate(hit[1]) else f"{hit[0]:g}"
    text = f"Elo {hit[0]:.0f}" if kind in ("image", "audio") else value
    return ("~" + text) if is_estimate(hit[1]) else text
