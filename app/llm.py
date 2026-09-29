"""DashScope：图像解析直连 qwen-vl，主图使用 wan 图生图模型。"""
import base64
import json
import mimetypes
import re
from pathlib import Path

from dashscope.aigc.image_generation import ImageGeneration
from dashscope.api_entities.dashscope_response import Message
from openai import OpenAI

from .config import settings

client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)

# ---- 各步骤默认提示词 ----
VISION_PROMPT = (
    "你是电商选品分析助手。分析这张产品图，只输出 JSON（全部用中文）："
    '{"product_type": "产品类型", "appearance": "外观描述", "material": "材质", '
    '"features": ["特征1", "特征2"], "suggested_category": "建议类目", '
    '"usage_scenarios": ["场景1", "场景2"]}'
)

IMAGE_PROMPT_TEMPLATE = (
    "将图片中的产品放在纯白色无缝背景上，生成专业的电商白底产品主图。"
    "保持产品本身完全不变，不要修改产品上的任何文字、标签、Logo或图标。"
    "仅去除原有背景，不要添加任何新的文字或图案。"
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


def generate_product_image(image_path: str, prompt: str | None = None) -> str:
    """以图生图：基于原图 + 文字指令生成 Ozon 白底主图，返回图片 URL。

    使用 DashScope wan 图生图模型（默认 wan2.7-image-pro），
    传入原图 base64 + 指令，SDK 自动上传原图并同步等待结果。
    prompt 可覆盖默认指令。
    """
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    data_url = f"data:{mime};base64,{b64}"

    text = prompt or IMAGE_PROMPT_TEMPLATE

    message = Message(
        role="user",
        content=[
            {"text": text},
            {"image": data_url},
        ],
    )

    rsp = ImageGeneration.call(
        model=settings.wan_image_model,
        api_key=settings.dashscope_api_key,
        messages=[message],
        n=1,
    )

    if rsp.status_code == 200:
        for choice in rsp.output.choices:
            for content in choice["message"]["content"]:
                if content.get("type") == "image" and content.get("image"):
                    return content["image"]
        raise RuntimeError("图生图成功但未找到图片URL")
    raise RuntimeError(f"图生图失败: {rsp.message or rsp}")
