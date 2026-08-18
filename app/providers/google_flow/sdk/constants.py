from __future__ import annotations

import itertools
import time

FLOW_API_BASE = "https://aisandbox-pa.googleapis.com"
TRPC_CREATE_PROJECT = "https://labs.google/fx/api/trpc/project.createProject"
TRPC_SEARCH_PROJECTS = "https://labs.google/fx/api/trpc/project.searchUserProjects"
VIDEO_I2V_URL = f"{FLOW_API_BASE}/v1/video:batchAsyncGenerateVideoStartImage"
VIDEO_OMNI_URL = f"{FLOW_API_BASE}/v1/video:batchAsyncGenerateVideoReferenceImages"
VIDEO_POLL_URL = f"{FLOW_API_BASE}/v1/video:batchCheckAsyncVideoGenerationStatus"
UPLOAD_IMAGE_URL = f"{FLOW_API_BASE}/v1/flow/uploadImage"

OMNI_FLASH_DURATION_KEYS = {4: "abra_r2v_4s", 6: "abra_r2v_6s", 8: "abra_r2v_8s", 10: "abra_r2v_10s"}
OMNI_FLASH_CREDIT_COST = {4: 15, 6: 20, 8: 25, 10: 30}
IMAGE_MODELS = {"NANO_BANANA_PRO": "GEM_PIX_2", "NANO_BANANA_2": "NARWHAL"}
DEFAULT_IMAGE_MODEL_KEY = "NANO_BANANA_PRO"
VIDEO_MODEL_KEYS = {
    "PAYGATE_TIER_ONE": {
        "lite": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_lite", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_lite"},
        "fast": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_s_fast", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_fast_portrait"},
        "quality": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_s", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_portrait"},
    },
    "PAYGATE_TIER_TWO": {
        "lite": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_lite", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_lite"},
        "fast": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_s_fast_ultra", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_fast_portrait_ultra"},
        "quality": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_s", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_portrait"},
        "lite_relaxed": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_lite_low_priority", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_lite_low_priority"},
        "fast_relaxed": {"VIDEO_ASPECT_RATIO_LANDSCAPE": "veo_3_1_i2v_s_fast_ultra_relaxed", "VIDEO_ASPECT_RATIO_PORTRAIT": "veo_3_1_i2v_s_fast_ultra_relaxed"},
    },
}
API_HEADERS = {"accept": "*/*", "origin": "https://labs.google", "referer": "https://labs.google/"}
TRPC_HEADERS = {"accept": "*/*", "content-type": "application/json"}
VALID_TIERS = {"PAYGATE_TIER_ONE", "PAYGATE_TIER_TWO"}
MAX_VARIANT_COUNT = 4
CAPTCHA_IMAGE = "IMAGE_GENERATION"
CAPTCHA_VIDEO = "VIDEO_GENERATION"
_token_counter = itertools.count(int(time.time()*1000))

def unique_token(): return next(_token_counter)
