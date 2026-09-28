"""流水线状态机：CREATED → ANALYZING → PENDING_REVIEW → IMAGE_GENERATING → COMPLETED。"""
import json
import threading
import traceback

from .agents.crew import run_text_crew
from .db import SessionLocal, init_db
from .llm import generate_product_image, parse_product_image
from .models import ListingTask, TaskStatus
from .ozon import import_and_wait


def create_task(image_path: str, purchase_price: float, weight_g: int) -> int:
    init_db()
    with SessionLocal() as s:
        task = ListingTask(
            image_path=image_path,
            purchase_price=purchase_price,
            weight_g=weight_g,
            status=TaskStatus.CREATED,
        )
        s.add(task)
        s.commit()
        return task.id


def run_pipeline(task_id: int) -> None:
    """执行文案链路：图像解析 → 卖点 → 俄语 Listing，逐阶段落库，失败可定位。"""
    init_db()
    with SessionLocal() as s:
        s.get(ListingTask, task_id).status = TaskStatus.ANALYZING
        s.commit()

    try:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)

            vision = parse_product_image(task.image_path)
            task.vision_result = json.dumps(vision, ensure_ascii=False)
            s.commit()

            selling, listing = run_text_crew(vision, task.purchase_price, task.weight_g)
            task.selling_points = json.dumps(selling, ensure_ascii=False)
            task.listing = json.dumps(listing, ensure_ascii=False)
            task.status = TaskStatus.PENDING_REVIEW
            s.commit()
    except Exception:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            task.status = TaskStatus.FAILED
            task.error = traceback.format_exc(limit=3)
            s.commit()
        raise


def review_task(task_id: int, approved: bool) -> None:
    """审核：通过则进入生图阶段，退回则置 REJECTED。"""
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        if approved:
            task.status = TaskStatus.IMAGE_GENERATING
            s.commit()
            threading.Thread(target=generate_image, args=(task_id,), daemon=True).start()
        else:
            task.status = TaskStatus.REJECTED
            s.commit()


def generate_image(task_id: int, prompt: str | None = None) -> None:
    """通义万相生成 Ozon 主图，完成后置 COMPLETED。prompt 可覆盖默认模板。"""
    init_db()
    try:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            vision = json.loads(task.vision_result) if task.vision_result else {}
            url = generate_product_image(vision, prompt=prompt)
            task.image_url = url
            task.status = TaskStatus.COMPLETED
            s.commit()
    except Exception:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            task.status = TaskStatus.FAILED
            task.error = traceback.format_exc(limit=3)
            s.commit()
        raise


def retry_task(task_id: int) -> None:
    """失败重试：清空旧产物与错误，状态复位，由调用方重新执行流水线。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.status = TaskStatus.CREATED
        task.vision_result = None
        task.selling_points = None
        task.listing = None
        task.image_url = None
        task.error = None
        s.commit()


def rerun_vision(task_id: int, prompt: str | None = None) -> None:
    """单独重跑 ① 图像解析：清空后续所有产物。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.vision_result = None
        task.selling_points = None
        task.listing = None
        task.image_url = None
        task.error = None
        task.status = TaskStatus.ANALYZING
        s.commit()
    threading.Thread(target=_run_vision_only, args=(task_id, prompt), daemon=True).start()


def _run_vision_only(task_id: int, prompt: str | None = None) -> None:
    try:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            vision = parse_product_image(task.image_path, prompt=prompt)
            task.vision_result = json.dumps(vision, ensure_ascii=False)
            task.status = TaskStatus.PENDING_REVIEW
            s.commit()
    except Exception:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            task.status = TaskStatus.FAILED
            task.error = traceback.format_exc(limit=3)
            s.commit()
        raise


def rerun_text(
    task_id: int,
    selling_prompt: str | None = None,
    listing_prompt: str | None = None,
) -> None:
    """单独重跑 ②③ 卖点+Listing：清空主图（依赖图像解析结果）。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.selling_points = None
        task.listing = None
        task.image_url = None
        task.error = None
        task.status = TaskStatus.ANALYZING
        s.commit()
    threading.Thread(
        target=_run_text_only,
        args=(task_id, selling_prompt, listing_prompt),
        daemon=True,
    ).start()


def _run_text_only(
    task_id: int,
    selling_prompt: str | None = None,
    listing_prompt: str | None = None,
) -> None:
    try:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            vision = json.loads(task.vision_result)
            selling, listing = run_text_crew(
                vision, task.purchase_price, task.weight_g,
                selling_prompt=selling_prompt, listing_prompt=listing_prompt,
            )
            task.selling_points = json.dumps(selling, ensure_ascii=False)
            task.listing = json.dumps(listing, ensure_ascii=False)
            task.status = TaskStatus.PENDING_REVIEW
            s.commit()
    except Exception:
        with SessionLocal() as s:
            task = s.get(ListingTask, task_id)
            task.status = TaskStatus.FAILED
            task.error = traceback.format_exc(limit=3)
            s.commit()
        raise


def rerun_image(task_id: int, prompt: str | None = None) -> None:
    """单独重跑 ④ Ozon 主图。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.image_url = None
        task.error = None
        task.status = TaskStatus.IMAGE_GENERATING
        s.commit()
    threading.Thread(target=generate_image, args=(task_id, prompt), daemon=True).start()


def upload_to_ozon(task_id: int) -> dict:
    """提交商品到 Ozon（异步导入并等待结果），返回 Ozon 返回信息。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.ozon_status = "pending"
        s.commit()
    try:
        result = import_and_wait(task)
        status = result.get("result", {}).get("status", "unknown")
        ozon_task_id = result.get("result", {}).get("task_id")
        with SessionLocal() as s:
            t = s.get(ListingTask, task_id)
            t.ozon_status = status
            if ozon_task_id:
                t.ozon_task_id = str(ozon_task_id)
            s.commit()
        return result
    except Exception:
        with SessionLocal() as s:
            t = s.get(ListingTask, task_id)
            t.ozon_status = "failed"
            t.error = traceback.format_exc(limit=5)
            s.commit()
        raise
