"""流水线状态机：CREATED → ANALYZING → PENDING_REVIEW → APPROVED / REJECTED / FAILED。"""
import json
import traceback

from .agents.crew import run_text_crew
from .db import SessionLocal, init_db
from .llm import parse_product_image
from .models import ListingTask, TaskStatus


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
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.status = TaskStatus.APPROVED if approved else TaskStatus.REJECTED
        s.commit()


def retry_task(task_id: int) -> None:
    """失败重试：清空旧产物与错误，状态复位，由调用方重新执行流水线。"""
    init_db()
    with SessionLocal() as s:
        task = s.get(ListingTask, task_id)
        task.status = TaskStatus.CREATED
        task.vision_result = None
        task.selling_points = None
        task.listing = None
        task.error = None
        s.commit()
