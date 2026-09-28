"""DashScope（OpenAI 兼容模式）：图像解析直连 qwen-vl，不走 CrewAI（其对多模态输入支持不稳定）。"""
import base64
import json
import mimetypes
import re
import time
import urllib.request
from pathlib import Path

from openai import OpenAI

from .config import settings

client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)

DASHSCOPE_API = "https://dashscope.aliyuncs.com/api/v1"

# ---- 各步骤默认提示词 ----
VISION_PROMPT = (
    "你是电商选品分析助手。分析这张产品图，只输出 JSON（全部用中文）："
    '{"product_type": "产品类型", "appearance": "外观描述", "material": "材质", '
    '"features": ["特征1", "特征2"], "suggested_category": "建议类目", '
    '"usage_scenarios": ["场景1", "场景2"]}'
)

IMAGE_PROMPT_TEMPLATE = (
    "A professional e-commerce product photo of {ptype}. "
    "{desc}. Material: {material}. Features: {features}. "
    "The product is centered, shot from a slight front angle, "
    "on a pure white seamless background, soft even studio lighting, "
    "sharp focus, high resolution. "
    "IMPORTANT: Keep all original text, logos, brand names, and icons "
    "that appear on the product itself — do not remove, modify, or redraw them. "
    "Only remove watermarks, promotional stickers, and unrelated background objects."
)


def extract_json(text: str):
    """从模型输出提取 JSON，兼容 ```json 代码块包裹。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return json.loads((m.group(1) if m else text).strip())


def parse_product_image(image_path: str, prompt: str | None = None) -> dict:
    """解析产品图：外观、材质、特征、建议类目。prompt 可覆盖默认提示词。"""
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    text = prompt or VISION_PROMPT

    resp = client.chat.completions.create(
        model=settings.qwen_vl_model,
        temperature=0.2,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": text},
            ],
        }],
    )
    return extract_json(resp.choices[0].message.content)


def _dashscope_post(url: str, payload: dict, async_mode: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _dashscope_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {settings.dashscope_api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def generate_product_image(vision_result: dict, prompt: str | None = None) -> str:
    """通义万相生成 Ozon 白底主图，返回图片 URL。prompt 可覆盖默认模板。"""
    desc = vision_result.get("appearance", "")
    ptype = vision_result.get("product_type", "")
    material = vision_result.get("material", "")
    features = "、".join(vision_result.get("features", []))

    if prompt:
        text = prompt.format(ptype=ptype, desc=desc, material=material, features=features)
    else:
        text = IMAGE_PROMPT_TEMPLATE.format(ptype=ptype, desc=desc, material=material, features=features)

    submit = _dashscope_post(
        f"{DASHSCOPE_API}/services/aigc/text2image/image-synthesis",
        {
            "model": "wanx2.1-t2i-turbo",
            "input": {"prompt": text},
            "parameters": {"size": "768*1024", "n": 1},
        },
        async_mode=True,
    )
    task_id = submit["output"]["task_id"]

    for _ in range(60):  # 最多轮询 5 分钟
        time.sleep(5)
        result = _dashscope_get(f"{DASHSCOPE_API}/tasks/{task_id}")
        status = result["output"]["task_status"]
        if status == "SUCCEEDED":
            return result["output"]["results"][0]["url"]
        if status in ("FAILED", "UNKNOWN"):
            raise RuntimeError(f"万相生图失败: {result.get('output', {}).get('message', status)}")
    raise TimeoutError("万相生图超时")
