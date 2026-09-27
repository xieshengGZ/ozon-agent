"""任务表：一条记录 = 一个商品的完整流水线状态与产物。"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TaskStatus(str, enum.Enum):
    CREATED = "created"                    # 已上传，待执行
    ANALYZING = "analyzing"                # 流水线执行中（解析→卖点→文案）
    PENDING_REVIEW = "pending_review"      # 等待人工审核
    APPROVED = "approved"                  # 审核通过（后续切片：生图、上架）
    REJECTED = "rejected"                  # 审核退回
    FAILED = "failed"                      # 流水线执行失败


class Base(DeclarativeBase):
    pass


class ListingTask(Base):
    __tablename__ = "listing_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.CREATED, index=True)

    # 人工输入
    image_path: Mapped[str] = mapped_column(String(512))
    purchase_price: Mapped[float] = mapped_column(Float)   # 采购价（元）
    weight_g: Mapped[int] = mapped_column(Integer)         # 重量（克）

    # 流水线产物（JSON 字符串）
    vision_result: Mapped[str | None] = mapped_column(Text)    # 图像解析
    selling_points: Mapped[str | None] = mapped_column(Text)   # 卖点
    listing: Mapped[str | None] = mapped_column(Text)          # 俄语 Listing

    error: Mapped[str | None] = mapped_column(Text)
