"""
image_generation/generator.py
------------------------------
Generates poster background images using the configured backend.

Backends:
  stability  – Stability AI (Stable Diffusion XL)
  replicate  – Replicate.com (Flux model)
  gemini     – Google Gemini image generation
  mock       – Creates a solid-color placeholder (no API needed)

The IMAGE_BACKEND env var selects which backend to use.
"""

import io
import os
import time
from pathlib import Path
from PIL import Image

from config.logging_setup import get_logger

log = get_logger(__name__)

IMAGE_BACKEND    = os.environ.get("IMAGE_BACKEND",    "mock")
STABILITY_API_KEY = os.environ.get("STABILITY_API_KEY", "")
REPLICATE_API_KEY = os.environ.get("REPLICATE_API_KEY", "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
REQUEST_TIMEOUT   = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))
POSTERS_DIR       = Path(__file__).resolve().parent.parent / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)

# Standard output dimensions (width × height)
SIZES: dict[str, tuple[int, int]] = {
    "portrait":  (1080, 1350),   # Instagram portrait
    "square":    (1080, 1080),   # Square post
    "landscape": (1920, 1080),   # Banner / landscape
}


def generate_image(
    prompt: str,
    negative_prompt: str = "",
    size: str = "square",
    output_path: Path | None = None,
) -> Path | None:
    """
    Generate one image and save it to `output_path`.

    Parameters
    ----------
    prompt          : positive image prompt
    negative_prompt : negative prompt (what to avoid)
    size            : "portrait" | "square" | "landscape"
    output_path     : where to save the PNG (auto-named if None)

    Returns
    -------
    Path to the saved image, or None if generation failed.
    """
    w, h = SIZES.get(size, SIZES["square"])

    backend_fn = {
        "stability":   _generate_stability,
        "replicate":   _generate_replicate,
        "gemini":      _generate_gemini,
        "pollinations": _generate_pollinations,
        "mock":        _generate_mock,
    }.get(IMAGE_BACKEND, _generate_mock)

    log.info("Generating image  backend=%s  size=%s  %dx%d",
             IMAGE_BACKEND, size, w, h)
    try:
        img_bytes = backend_fn(prompt, negative_prompt, w, h)
    except Exception as exc:
        log.error("Image generation failed (%s): %s", IMAGE_BACKEND, exc)
        log.info("Falling back to branded mock background")
        img_bytes = _generate_mock(prompt, "", w, h)

    if img_bytes is None:
        log.warning("No image bytes returned; skipping save.")
        return None

    # Save
    if output_path is None:
        output_path = POSTERS_DIR / f"image_{int(time.time())}_{size}.png"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(io.BytesIO(img_bytes))
    img.save(output_path, format="PNG")
    log.info("Image saved → %s", output_path)
    return output_path


# ── Stability AI backend ──────────────────────────────────────────────────────

def _generate_stability(prompt: str, negative_prompt: str, w: int, h: int) -> bytes:
    import requests

    if not STABILITY_API_KEY:
        raise ValueError("STABILITY_API_KEY not set in .env")

    url = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
    headers = {
        "authorization": f"Bearer {STABILITY_API_KEY}",
        "accept": "image/*",
    }
    data = {
        "prompt":          prompt,
        "negative_prompt": negative_prompt,
        "width":           str(w),
        "height":          str(h),
        "output_format":   "png",
    }
    resp = requests.post(url, headers=headers, files={"none": ""}, data=data,
                         timeout=REQUEST_TIMEOUT * 4)
    resp.raise_for_status()
    return resp.content


# ── Replicate / Flux backend ──────────────────────────────────────────────────

def _generate_replicate(prompt: str, negative_prompt: str, w: int, h: int) -> bytes:
    import replicate
    import requests

    if not REPLICATE_API_KEY:
        raise ValueError("REPLICATE_API_KEY not set in .env")

    output = replicate.run(
        "black-forest-labs/flux-1.1-pro",
        input={
            "prompt":          prompt,
            "negative_prompt": negative_prompt,
            "width":           w,
            "height":          h,
            "output_format":   "png",
        }
    )
    # replicate returns a URL or file-like; handle both
    if hasattr(output, "read"):
        return output.read()
    url = str(output)
    resp = requests.get(url, timeout=REQUEST_TIMEOUT * 4)
    resp.raise_for_status()
    return resp.content


# ── Pollinations.ai backend (free, no API key required) ──────────────────────

def _generate_pollinations(prompt: str, negative_prompt: str, w: int, h: int) -> bytes:
    """
    Pollinations.ai — completely free, no sign-up, no API key.
    Returns a real AI-generated image via a single GET request.
    Docs: https://pollinations.ai

    Notes:
    - `enhance=true` triggers an extra server-side LLM prompt rewrite that is
      unreliable at large sizes; it is intentionally omitted.
    - Pollinations caps practical output at 1024px per axis for free requests,
      so we request at that size and Pillow resizes to the target afterwards.
    """
    import requests
    from urllib.parse import quote

    safe_prompt = prompt[:500]
    encoded     = quote(safe_prompt)
    seed        = int(time.time()) % 2**31

    # Request at ≤1024 — more reliable than asking for exact 1080
    req_w = min(w, 1024)
    req_h = min(h, 1024)

    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={req_w}&height={req_h}&seed={seed}&nologo=true&model=flux"
    )

    log.info("Pollinations request  size=%dx%d  seed=%d", req_w, req_h, seed)
    resp = requests.get(url, timeout=180, stream=True)
    resp.raise_for_status()
    raw = resp.content

    # Resize to the exact target if Pollinations returned a different size
    if req_w != w or req_h != h:
        buf = io.BytesIO(raw)
        img = Image.open(buf).convert("RGB").resize((w, h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    return raw


# ── Gemini image backend ──────────────────────────────────────────────────────

def _generate_gemini(prompt: str, negative_prompt: str, w: int, h: int) -> bytes:
    from google import genai
    from google.genai import types

    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=GEMINI_API_KEY)
    ratio  = "1:1" if w == h else ("9:16" if h > w else "16:9")
    response = client.models.generate_images(
        model="imagen-4.0-generate-001",
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio=ratio,
            output_mime_type="image/png",
        ),
    )
    img = response.generated_images[0].image
    buf = io.BytesIO(img.image_bytes)
    return buf.getvalue()


# ── Mock backend (Boishakhi TV branded gradient, no API required) ────────────

def _generate_mock(prompt: str, negative_prompt: str, w: int, h: int) -> bytes:
    """
    Generate a branded dark gradient background matching Boishakhi TV colors
    (deep green → near-black → deep red glow).  No API call needed.
    """
    from PIL import ImageDraw, ImageFilter

    # Base: very dark near-black
    img  = Image.new("RGB", (w, h), color=(8, 12, 10))
    draw = ImageDraw.Draw(img)

    # Top-left green glow  (#006a4e at 60% opacity blended into dark)
    for i in range(min(w, h) // 2):
        ratio  = 1 - (i / (min(w, h) // 2))
        alpha  = int(110 * ratio ** 1.8)
        green  = (0, int(50 * ratio), int(35 * ratio))
        draw.ellipse(
            [-i, -i, i, i],
            fill=(green[0], green[1] + alpha // 5, green[2] + alpha // 6),
        )

    # Bottom-right red glow  (#f42a41 hue)
    for i in range(min(w, h) // 3):
        ratio = 1 - (i / (min(w, h) // 3))
        r     = int(80 * ratio)
        draw.ellipse(
            [w - i, h - i, w + i, h + i],
            fill=(r, int(r * 0.10), int(r * 0.12)),
        )

    # Subtle diagonal scan-lines for a broadcast look
    for y in range(0, h, 4):
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, 30), width=1)

    # Soft blur to blend everything naturally
    img = img.filter(ImageFilter.GaussianBlur(radius=max(6, w // 180)))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()