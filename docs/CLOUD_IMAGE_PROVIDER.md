# Cloud Image Generation for Friday (Cloudflare Workers AI + Stability AI)

Friday's Image Generation panel is **cloud-only**: it generates real AI images through **your own** provider account (Cloudflare Workers AI or Stability AI), chosen per render in the panel's ENGINE dropdown -- badge `cloud · <model>`. With no usable key it shows honest setup guidance and renders **nothing** (badge `no provider key · add one in Settings`): there is no local art renderer and no local stand-in image.

**One entry at a time, in panel order.** Settings → Image Generation entries
that carry a key are tried top-to-bottom; the first *genuinely usable* one
becomes the active backend. A broken entry (wrong URL, expired token) no
longer blocks a valid one below it — but if every keyed entry is broken the
panel tells you the first real problem instead of silently rendering locally.

Two providers are delivered:

- **Cloudflare Workers AI** — the everyday workhorse: **10,000 free Neurons
  per day, resetting 00:00 UTC**, no credit card. When the daily allowance
  is spent the request fails with a clear panel error.
- **Stability AI** — higher-end models on **25 free starter credits** (e.g.
  SD 3.5 Flash = 2.5 credits/image); after that it's paid top-up.

## Provider A — Stability AI

1. Create an account at <https://platform.stability.ai> → **Account → API
   Keys** → copy your key (new accounts start with 25 credits).
2. Friday → **Settings → Image Generation → + Add Model**:

| Field | Value |
|---|---|
| **Name** | anything, e.g. `STABILITY` |
| **Model** | `sd3.5-flash` (cheapest) · also: `core` (SDXL), `sd3.5-large`, `ultra` |
| **API key** | your Stability key |
| **API base URL** | `https://api.stability.ai` ← **exactly this** |

> ⚠️ The base URL is the **API host**, not the account web page —
> `https://platform.stability.ai/account/keys` is where you *create* the key,
> it is not an API endpoint. Pasting the account page is the #1 setup
> mistake; the panel will say so explicitly.

3. Press **Check**: Friday reads your credit balance (0 credits spent) and
   shows it. The badge then reads `cloud · sd3.5-flash`.

## Provider B — Cloudflare Workers AI

---

### Get your free Cloudflare credentials

1. Create a free account at <https://dash.cloudflare.com> (no card required).
2. On the dashboard's right-hand column, copy your **Account ID**.
3. Go to **My Profile → API Tokens → Create Token**, choose the
   **Workers AI** template (or a custom token with the
   *Account → Workers AI → Read/Run* permissions), and create it.
4. Copy the token — it is shown only once.

### Add the key in Friday

1. Open Friday → **Settings**.
2. In the **Image Generation** region, press **+ Add Model**.
3. Fill in the fields exactly like this:

| Field | Value |
|---|---|
| **Name** | anything, e.g. `My Cloudflare` |
| **Model** | `@cf/black-forest-labs/flux-1-schnell` (fastest free option) |
| **API key** | your API token from step 1 |
| **API base URL** | `https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>` |

4. Press **Check** on the row: Friday authenticates the token against your
   account's Workers AI model catalog (this does **not** spend generation
   quota). You should see *"Token authenticated — Workers AI is reachable."*

The key is stored Fernet-encrypted in `~/.friday/settings/ai_models.json.enc`,
exactly like every other region; it is never written into source code or
`.env`. **Edit → Show** reveals it temporarily in memory if you need it.
**Remove** deletes it; with no usable key left, the panel returns to the setup-needed state on the very next Generate.

## 3. Use it (either provider)

## Panel controls (current layout)

- **Select provider** / **Side by side** buttons open the provider selector.
  Single mode renders with one engine; side-by-side runs the same brief
  through **two** engines back-to-back and drops both results in the queue.
- **Generate** lives inside the Engine section (there is no header button
  and no separate preview/console area -- the queue is the result surface).
- **Queue cards show the actual image** with its engine/aspect/format/seed
  overlaid on it, plus open/reveal/copy actions.
- Supported providers: Cloudflare Workers AI, Stability AI, **any
  OpenAI-compatible `/v1` images API** (OpenAI, OpenRouter, proxies) and
  **Ideogram** -- every keyed entry under Settings -> Image Generation is
  offered in the selector.

## The Engine picker (choose your model per render)

The Generate button has an **ENGINE** dropdown beneath it. It lists, live
from Settings: **Automatic (first working provider)** (default), **Local art
renderer**, and every keyed Image Generation entry such
as `Cloudflare Workers AI · flux-1-schnell` or `Stability AI · sd3.5-flash`.
Entries with broken configuration appear marked **⚠ … (needs attention)** and
Generate then reports the exact fix instead of swapping providers behind your
back. Your choice is remembered (encrypted with the rest of Friday's state).



Open the **Image Generation** panel. The header badge shows the active
provider, e.g. `cloud · flux-1-schnell` or `cloud · sd3.5-flash`. Write a
brief and press **Generate** — the panel sends the prompt over HTTPS and
saves the returned image to your output folder with its usual thumbnail and
history entry.

No restart is ever needed: the panel re-reads Settings **on every Generate**,
so adding, editing, removing, or *reordering* entries takes effect on the
next render. To switch providers, remove or fix the entry above the one you
want — the first genuinely usable keyed entry wins.

### Cloudflare models you can put in the Model field

| Model | Notes |
|---|---|
| `@cf/black-forest-labs/flux-1-schnell` | Recommended. Very fast, 4–8 steps. Aspect/size is chosen by the model. |
| `@cf/stabilityai/stable-diffusion-xl-base-1.0` | Respects the panel's aspect + seed + quality (steps) controls. |
| `@cf/lykon/dreamshaper-8-lcm` | Respects aspect + seed + quality controls. |

### Stability models

| Model | Endpoint | Cost |
|---|---|---|
| `sd3.5-flash` | SD 3.5 Flash | 2.5 credits/image — best free-tier fit |
| `sd3.5-large` / `sd3.5-medium` | SD 3.5 family | higher per image |
| `core` | Stable Image Core (SDXL) | 3 credits/image |
| `ultra` | Stable Image Ultra | highest per image |

Stability accepts the panel's aspect (mapped to its nearest supported
ratio), seed, and output format (PNG/JPEG/WebP). Quality/steps and the
local style/composition presets are not sent.

## 4. Honest limitations (by design)

- **No fake previews, ever.** Cloud models cannot be pre-rendered
  honestly, so the preview card is a styled placeholder until the finished
  image appears inline.
- **Style/composition/detail/reference-image controls belong to the local
  renderer.** They are not sent to the cloud provider; the cloud adapter
  uses your prompt, aspect-derived size, quality-derived step count, and
  seed (for the models that accept one).
- **A broken entry fails loudly.** If the base URL/token is wrong, the
  badge reads `image provider entry needs attention` and Generate reports a
  fixable, specific error — it does not quietly produce a local image
  behind your back.
- **Daily quota errors are reported as-is** from Cloudflare (HTTP error +
  provider message in the panel's console).

## Housekeeping

- Your output folder keeps **exactly one file per prompt** -- the rendered image.
- Queue thumbnails live in Friday's private cache at `~/.friday/generated/thumbnails/` and are never written beside your image.
- On Generate, the finished image appears **inline in the preview card** automatically (the queue's eye icon re-shows it at full size; the open/folder icons remain for external use).

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Check fails *401/403* | Token lacks Workers AI permissions — recreate it with the Workers AI template. |
| Check fails *404* | Wrong Account ID in the base URL. |
| Badge error mentions a `dash.cloudflare.com/...` URL | You pasted the dashboard page. Newer builds auto-normalize it to the API endpoint; on older builds, use `https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>` yourself. |
| Badge says *needs attention* | Base URL doesn't look like `.../accounts/<ID>` — fix it in Edit. |
| Render error mentions Neurons/quota | Free daily allowance spent; resets 00:00 UTC. |
| Badge shows `no provider key` | Every Image Generation entry was removed, or its token field is empty -- add/fix a key to generate. |

## Architecture (for contributors)

```
Settings (Image Generation entry, encrypted at rest)
        │  image_backend_resolver.resolve_image_backend()
        ▼
CloudflareImageBackend ── implements ── ImageBackend protocol
(friday_image/providers.py)              (friday_image/backend.py)
        │ render(request, output_dir, progress, cancel)
        ▼
quiet image panel (friday_image/view.py) — same worker, queue, history
```

`friday_image` keeps zero app dependencies: adapters are built from plain
credentials; only the tiny root module `image_backend_resolver.py` reads
`settings_store`. New providers (Stability, Replicate, Pollinations) slot in
as another class with `name/description/badge/provider_kind + render()` and
one extra branch in the resolver.
