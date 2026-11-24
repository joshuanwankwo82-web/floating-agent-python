"""What kind of model is this? -- text, image, video, audio, transcription,
embedding, or other (rerank / moderation / decision models Friday has no
use for).

Pure functions, no network. `classify(item)` reads a raw model item from a
provider's model list and answers (kind, source):

  source "provider" -- the provider's own label decided it
  source "name"     -- no usable label; the model id gave it away
  source "default"  -- nothing to go on; chat (text) is the safe guess

How each provider labels its models (researched November 2026, against
the live catalogs where they are public):

  type / category field
    Vercel AI Gateway   type: language | image | video | embedding | speech
                        | transcription | reranking | realtime | evaluation
    Together AI         type: chat | language | code | image | embedding
                        | rerank | moderation | audio | video | transcribe
    OpenCode Zen        type: chat | embedding | image | video | realtime
                        | decision
    LLM7.io             model_type: chat | image | video | audio_to_text
    LM Studio           type: llm | vlm | embeddings      (/api/v0/models)
    SiliconFlow         sub_type: chat | embedding | reranker | text-to-image
                        | image-to-image | speech-to-text | text-to-video
                        (a list filter; Friday asks per sub_type)
    Cloudflare          task.name: Text Generation | Text-to-Image | ...
                        (/ai/models/search)
  input / output modalities
    OpenRouter, Kilo,   architecture.output_modalities: text | image |
    Hugging Face,       video | speech | audio | transcription |
    NanoGPT, Aion,      embeddings | rerank | decisions
    Nebius (verbose),   (OpenRouter lists non-chat models only when
    Chutes, Mancer,     asked with ?output_modalities=all; NanoGPT keeps
    xAI                 them on /image-models, /video-models, ...)
    Vercel, LLM7,       modalities: {input: [...], output: [...]}
    OpenCode Zen
  endpoint / capability lists
    UnoRouter, Agnes    supported_endpoint_types: openai | anthropic |
    (New-API gateways)  gemini | embedding | image-generation | aihorde
                        -- API formats, so a Whisper model just says
                        "openai"; the name decides those
    Cohere              endpoints: chat | embed | rerank | classify
    Mistral             capabilities: completion_chat, audio_transcription,
                        audio_speech, ocr, moderation, ...
    Ollama              capabilities: completion | embedding | vision ...
    Google AI Studio    supportedGenerationMethods: generateContent |
                        embedContent | predict (Imagen) |
                        predictLongRunning (Veo)   (native /v1beta/models)
    Fireworks           kind: HF_BASE_MODEL | EMBEDDING_MODEL |
                        FLUMINA_BASE_MODEL (image); supports_chat
    Requesty            supports_image_generation
  ids only (the name decides)
    OpenAI, Groq, DeepSeek, Anthropic, Moonshot, Cerebras, SambaNova,
    NVIDIA, ModelScope, OVHcloud, DashScope, Z.ai / Zhipu, AI21, Nscale,
    Featherless, Glhf, BlackPig, OpenCode Console

A caller that knows more than the item says (it came from xAI's
/image-generation-models, NanoGPT's /video-models, SiliconFlow's
?sub_type=embedding ...) sets item["_kind"] and that wins.
"""
import re

KINDS = ("text", "image", "video", "audio", "transcription", "embedding", "other")
LABELS = {"text": "Text", "image": "Image", "video": "Video", "audio": "Audio",
          "transcription": "Transcription", "embedding": "Embedding",
          "other": "Other"}
# Where an added model of each kind is stored. Text goes to whichever chat
# bucket the user is adding to (see faculty_for); "other" is never auto-added.
KIND_FACULTY = {"image": "Image Generation", "video": "Video Generation",
                "audio": "Audio Generation", "transcription": "Transcription",
                "embedding": "Embedding"}
TEXT_FACULTIES = ("Voice", "Thinking", "Planning", "Review", "Verify",
                  "Coding", "Text Generation")


def faculty_kind(faculty):
    """The kind a storage bucket holds (Research -> None: it's a service)."""
    if faculty in TEXT_FACULTIES:
        return "text"
    for kind, fac in KIND_FACULTY.items():
        if fac == faculty:
            return kind
    return None


def faculty_for(kind, chosen_faculty):
    """Where a model of `kind` should be stored when the user is adding to
    `chosen_faculty`. None = leave it where the user put it."""
    if kind == "text":
        return chosen_faculty if chosen_faculty in TEXT_FACULTIES else "Thinking"
    return KIND_FACULTY.get(kind)


# -- labels ------------------------------------------------------------------
# One table for every provider's type-like word, lower-cased.
_TYPE_WORDS = {
    "text": "text", "chat": "text", "language": "text", "llm": "text",
    "vlm": "text", "code": "text", "completion": "text", "chat-completion": "text",
    "text generation": "text", "text-generation": "text",
    "image": "image", "text-to-image": "image", "image-to-image": "image",
    "image-generation": "image", "aihorde": "image",
    "video": "video", "text-to-video": "video", "image-to-video": "video",
    "video-generation": "video",
    "audio": "audio", "speech": "audio", "tts": "audio", "text-to-speech": "audio",
    "music": "audio",
    "transcription": "transcription", "transcribe": "transcription",
    "audio_to_text": "transcription", "speech-to-text": "transcription",
    "stt": "transcription", "automatic speech recognition": "transcription",
    "embedding": "embedding", "embeddings": "embedding", "embed": "embedding",
    "text embeddings": "embedding", "text-embedding": "embedding",
    "rerank": "other", "reranker": "other", "reranking": "other",
    "moderation": "other", "evaluation": "other", "decision": "other",
    "decisions": "other", "systemone": "other", "typesafe-decisions": "other",
    "realtime": "audio", "extraction": "other", "classify": "other",
    "text classification": "other", "ocr": "other",
}


def _word(value):
    return _TYPE_WORDS.get(str(value or "").strip().lower())


def _modal_set(values):
    """['text', 'image/png', 'audio/pcm'] -> {'text', 'image', 'audio'}."""
    out = set()
    for v in values or []:
        v = str(v or "").strip().lower()
        if v:
            out.add(v.split("/", 1)[0])
    return out


def _from_modalities(inputs, outputs):
    if not outputs:
        return None
    if outputs & {"embeddings", "embedding"}:
        return "embedding"
    if outputs & {"rerank", "decisions", "decision", "json"} and "text" not in outputs:
        return "other"
    if "transcription" in outputs:
        return "transcription"
    if "video" in outputs:
        return "video"
    if "image" in outputs:
        return "image"
    if outputs & {"audio", "speech", "music", "voice"}:
        return "audio"
    if "text" in outputs:
        # audio in, text out, and nothing else to read -> speech-to-text
        if inputs and "audio" in inputs and not inputs & {"text", "image"}:
            return "transcription"
        return "text"
    return None


def _modality_string(value):
    """OpenRouter-style "text+image->text" -> (inputs, outputs)."""
    if not isinstance(value, str) or "->" not in value:
        return None, None
    left, _, right = value.partition("->")
    return _modal_set(left.split("+")), _modal_set(right.split("+"))


def _from_labels(item):
    """The provider's own say, or None."""
    hint = item.get("_kind")
    if hint in KINDS:
        return hint

    # 1. an explicit type word (wins over modalities: Vercel lists its
    #    embedding models with output ["text"])
    for field in ("type", "model_type", "displayType", "sub_type"):
        kind = _word(item.get(field))
        if kind:
            return kind
    task = item.get("task")
    if isinstance(task, dict):
        kind = _word(task.get("name"))
        if kind:
            return kind

    # 2. modalities, in each provider's shape
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    mods = item.get("modalities") if isinstance(item.get("modalities"), dict) else {}
    for inputs, outputs in (
            (arch.get("input_modalities"), arch.get("output_modalities")),
            (item.get("input_modalities"), item.get("output_modalities")),
            (mods.get("input"), mods.get("output")),
            (item.get("supported_input_modalities"), item.get("supported_output_modalities"))):
        kind = _from_modalities(_modal_set(inputs), _modal_set(outputs))
        if kind:
            return kind
    kind = _from_modalities(*_modality_string(arch.get("modality")))
    if kind:
        return kind

    # 3. endpoint and capability lists
    endpoints = [str(e).lower() for e in (item.get("supported_endpoint_types") or [])
                 if isinstance(e, str)]
    if any(e.startswith("embedding") for e in endpoints):
        return "embedding"
    for e in endpoints:
        kind = _word(e)
        if kind and kind != "text":
            return kind
    cohere = [str(e).lower() for e in (item.get("endpoints") or []) if isinstance(e, str)]
    if cohere:
        if "chat" in cohere or "generate" in cohere:
            return "text"
        if "embed" in cohere:
            return "embedding"
        if cohere and set(cohere) <= {"rerank", "classify"}:
            return "other"
    methods = item.get("supportedGenerationMethods") or []
    if methods:
        if "predictLongRunning" in methods:
            return "video"
        if "predict" in methods:
            return "image"
        if "embedContent" in methods or "embedText" in methods:
            return "embedding"
    caps = item.get("capabilities")
    if isinstance(caps, list):
        caps = [str(c).lower() for c in caps]
        if "embedding" in caps:
            return "embedding"
        if "completion" in caps:
            return "text"
    elif isinstance(caps, dict):
        if caps.get("audio_transcription") or caps.get("audio_transcription_realtime"):
            return "transcription"
        if caps.get("audio_speech"):
            return "audio"
        if caps.get("completion_chat"):
            return "text"
        if caps.get("ocr") or caps.get("moderation") or caps.get("classification"):
            return "other"
    fw = str(item.get("kind") or "").upper()
    if fw == "EMBEDDING_MODEL":
        return "embedding"
    if fw.startswith("FLUMINA"):
        return "image"
    if item.get("supports_image_generation") is True:
        return "image"
    if item.get("supports_chat") is True:
        return "text"
    return None


# -- names -------------------------------------------------------------------
_NAME_RULES = [
    ("other", r"rerank|moderat|guard\b|guard-|nemoguard|content-safety|safety-guard"
              r"|-detector|\bocr\b|-ocr"),
    ("transcription", r"whisper|transcri|speech-to-text|\bstt\b|-stt|stt-|\basr\b|-asr"
                      r"|parakeet|scribe|nova-[23]\b|canary-"),
    ("embedding", r"embed|\bbge-|\bbge\b|\be5-|\bgte-|nomic-|nvclip|\bclip\b"
                  r"|retriever|colbert|jina-v"),
    ("audio", r"\btts\b|-tts|tts-|text-to-speech|orpheus|kokoro|eleven|piper|lyria"
              r"|\bmusic|suno|sonic-|-audio\b|audio-gen|voice-design|speech-0|bark\b"),
    ("video", r"\bveo\b|veo-|\bsora\b|sora-|\bkling|seedance|hailuo|runway|\bgen-[34]"
              r"|luma|ray-2|\bpika|ltx-|ltxv|cogvideo|hunyuan-?video|mochi"
              r"|wan-?[\d.]+.*(t2v|i2v)|[-_/]t2v|[-_/]i2v|-video\b|video-gen|agnes-video"),
    ("image", r"dall-e|gpt-image|imagen|\bflux|stable-diffusion|sdxl|\bsd3|\bsd-|sd1\.5"
              r"|midjourney|ideogram|recraft|seedream|kandinsky|playground-v|hidream"
              r"|kolors|cogview|hunyuan-?image|-image\b|-image-|image-edit|image-gen"
              r"|pony|illustrious|dreamshaper|juggernaut|realistic-?vision|absolutereality"
              r"|epicrealism|majicmix|anything-v|deliberate|rev-animated|nano-banana"),
]
_NAME_RES = [(kind, re.compile(rx, re.I)) for kind, rx in _NAME_RULES]


def classify_name(model_id):
    """A best guess from the id alone, or None."""
    name = str(model_id or "").lower()
    for kind, rx in _NAME_RES:
        if rx.search(name):
            return kind
    return None


_ROUTER = re.compile(r"(^|/)auto($|-|:)", re.I)


def classify(item):
    """A raw model item (dict, or a bare id) -> (kind, source)."""
    if not isinstance(item, dict):
        item = {"id": item}
    kind = _from_labels(item)
    # Auto-routers (openrouter/auto, auto-code ...) list every output any
    # model behind them can make, image included -- they're chat endpoints.
    if kind in ("image", "audio", "video") and _ROUTER.search(str(item.get("id") or "")):
        return "text", "provider"
    if kind:
        return kind, "provider"
    kind = classify_name(item.get("id") or item.get("name") or item.get("model"))
    if kind:
        return kind, "name"
    return "text", "default"


def classify_items(items):
    """[items] -> {id: {"kind": ..., "source": ...}} (first sighting wins)."""
    out = {}
    for item in items or []:
        model_id = item.get("id") if isinstance(item, dict) else item
        model_id = str(model_id or "").strip()
        if model_id and model_id not in out:
            kind, source = classify(item)
            out[model_id] = {"kind": kind, "source": source}
    return out
