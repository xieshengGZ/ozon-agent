"""DashScope（OpenAI 兼容模式）：图像解析直连 qwen-vl，不走 CrewAI（其对多模态输入支持不稳定）。"""
import base64
import json
import mimetypes
import re
from pathlib import Path

from openai import OpenAI

from .config import settings

client = OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url)


def extract_json(text: str):
    """从模型输出提取 JSON，兼容 ```json 代码块包裹。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return json.loads((m.group(1) if m else text).strip())


def parse_product_image(image_path: str) -> dict:
    """解析产品图：外观、材质、特征、建议类目。"""
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()

    resp = client.chat.completions.create(
        model=settings.qwen_vl_model,
        temperature=0.2,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": (
                    "你是电商选品分析助手。分析这张产品图，只输出 JSON（全部用中文）："
                    '{"product_type": "产品类型", "appearance": "外观描述", "material": "材质", '
                    '"features": ["特征1", "特征2"], "suggested_category": "建议类目", '
                    '"usage_scenarios": ["场景1", "场景2"]}'
                )},
            ],
        }],
    )
    return extract_json(resp.choices[0].message.content)
