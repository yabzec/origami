"""Local, in-process inference for image description.

The only module that imports torch / transformers. Those imports are deferred into
`describe_image` so that importing this module (which `app.services.llm` does at
import time) never pulls in torch. That matters beyond startup speed: the API
process only ever embeds — which is remote — so it never reaches this code and
stays at ~30MB instead of carrying torch's ~700MB runtime floor.

Nothing here is resident. The vision model loads per call and is released before
returning; see `describe_image`. Embeddings are not local at all — they go to
Cloudflare Workers AI via `app.services.llm.embed`.
"""

import logging
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("origami.local_models")


def describe_image(image_path: Path, prompt: str) -> str:
    """Answer `prompt` about the image at `image_path` using the local vision model.

    The model is loaded, used, and released within this call. That costs ~20-30s of
    load time per call but keeps ~3.7GB out of the worker's steady-state footprint.
    The trade is right because this path is rare by design: `pipeline._ensure_summary`
    only reaches vision for images with under IMAGE_SUMMARY_TEXT_THRESHOLD chars of
    extracted text (photos), and ingestion is async so the latency is not user-facing.
    """
    import gc

    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM

    settings = get_settings()
    name = settings.vision_model_name
    revision = settings.vision_model_revision

    # Open/validate the image before loading the ~3.7GB model, so a corrupt or
    # missing image fails fast instead of wasting a full model load.
    with Image.open(image_path) as image:
        rgb_image = image.convert("RGB")

    log.info("loading vision model %s (cpu, bfloat16)", name)
    model = AutoModelForCausalLM.from_pretrained(
        name, revision=revision, trust_remote_code=True, torch_dtype=torch.bfloat16
    )
    try:
        answer = model.query(rgb_image, prompt)["answer"]
    finally:
        del model
        gc.collect()
    return answer
