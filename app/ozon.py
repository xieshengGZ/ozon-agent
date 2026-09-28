"""Ozon Seller API：商品上架（异步导入 + 状态查询）。"""
import json
import time
import urllib.request

from .config import settings

OZON_BASE = settings.ozon_base_url


def _ozon_post(path: str, payload: dict) -> dict:
    headers = {
        "Client-Id": settings.ozon_client_id,
        "Api-Key": settings.ozon_api_key,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        f"{OZON_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _ozon_get(path: str, payload: dict) -> dict:
    headers = {
        "Client-Id": settings.ozon_client_id,
        "Api-Key": settings.ozon_api_key,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        f"{OZON_BASE}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def build_import_payload(task) -> dict:
    """根据任务产物构造 Ozon 商品导入 payload。"""
    listing = json.loads(task.listing) if task.listing else {}
    vision = json.loads(task.vision_result) if task.vision_result else {}

    title = listing.get("title_ru", vision.get("product_type", "Product"))
    description = listing.get("description_ru", "")
    keywords = listing.get("keywords_ru", [])

    price_rub = round(task.purchase_price * settings.ozon_price_markup * settings.ozon_cny_to_rub)
    old_price_rub = round(price_rub * 1.3)

    images = []
    if task.image_url:
        images.append(task.image_url)

    return {
        "items": [
            {
                "name": title[:200],
                "offer_id": f"ozon-agent-{task.id}",
                "price": str(price_rub),
                "old_price": str(old_price_rub),
                "currency_code": "RUB",
                "weight": task.weight_g,
                "weight_unit": "g",
                "depth": 10,
                "height": 10,
                "width": 10,
                "dimension_unit": "cm",
                "images": images,
                "attributes": [
                    {"complex_id": 0, "id": 4191, "values": [{"value": description}]}  # 4191 = 描述
                ],
            }
        ]
    }


def import_product(task) -> dict:
    """提交商品导入，返回 Ozon 任务信息。"""
    payload = build_import_payload(task)
    result = _ozon_post("/v3/product/import", payload)
    return result


def get_import_status(task_id: int) -> dict:
    """查询导入任务状态。"""
    return _ozon_get("/v1/product/import/info", {"task_id": task_id})


def import_and_wait(task, timeout: int = 120) -> dict:
    """提交导入并轮询至完成，返回最终结果。"""
    submit = import_product(task)
    ozon_task_id = submit.get("result", {}).get("task_id")
    if not ozon_task_id:
        return submit

    for _ in range(timeout // 5):
        time.sleep(5)
        info = get_import_status(ozon_task_id)
        status = info.get("result", {}).get("status")
        if status in ("success", "failed"):
            return info
    return {"result": {"status": "timeout", "task_id": ozon_task_id}}
