"""Streamlit 后台：新建任务（上传+后台执行+实时进度）、人工审核、历史任务（含失败重试）。"""
import hmac
import json
import sys
import threading
import time
import uuid
from pathlib import Path

import streamlit as st

# 兼容 `streamlit run app/ui/streamlit_app.py` 的启动方式（脚本目录会被加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import ListingTask, TaskStatus
from app.pipeline import create_task, retry_task, review_task, run_pipeline

st.set_page_config(page_title="Ozon 上架 Agent", layout="wide")

STATUS_LABEL = {
    TaskStatus.CREATED: "待执行",
    TaskStatus.ANALYZING: "执行中",
    TaskStatus.PENDING_REVIEW: "待审核",
    TaskStatus.APPROVED: "已通过",
    TaskStatus.REJECTED: "已退回",
    TaskStatus.FAILED: "失败",
}


def login_gate() -> None:
    """会话级密码登录：未通过则渲染登录页并停止执行。"""
    if st.session_state.get("auth_ok"):
        return
    if not settings.app_password:
        st.error("未设置后台密码：请在服务器 .env 中配置 APP_PASSWORD 后重启应用")
        st.stop()
    st.markdown("## Ozon 上架 Agent")
    st.caption("内部后台，请输入访问密码")
    pwd = st.text_input("访问密码", type="password")
    if st.button("登录", type="primary"):
        if hmac.compare_digest(pwd.encode(), settings.app_password.encode()):
            st.session_state.auth_ok = True
            st.rerun()
        st.error("密码错误")
    st.stop()


login_gate()
init_db()

with st.sidebar:
    if st.button("退出登录"):
        st.session_state.pop("auth_ok", None)
        st.rerun()


def _steps_view(t: ListingTask, expanded: bool = False) -> None:
    """逐步展示流水线产物；未生成的步骤显示占位说明。"""
    for title, raw in (
        ("① 图像解析", t.vision_result),
        ("② 卖点挖掘", t.selling_points),
        ("③ 俄语 Listing", t.listing),
    ):
        with st.expander(title, expanded=expanded and bool(raw)):
            if raw:
                st.json(json.loads(raw))
            else:
                st.caption("未生成")
    if t.error:
        with st.expander("⚠️ 错误信息", expanded=True):
            st.code(t.error)


def _progress_line(t: ListingTask) -> str:
    def mark(done: bool) -> str:
        return "✅" if done else "⏳"

    return (
        f"{mark(bool(t.vision_result))} 图像解析 ｜ "
        f"{mark(bool(t.selling_points))} 卖点挖掘 ｜ "
        f"{mark(bool(t.listing))} 俄语 Listing"
    )


def _launch(task_id: int, retry: bool = False) -> None:
    """后台线程执行流水线；页面通过 watch_task_id 轮询展示进度。"""
    if retry:
        retry_task(task_id)
    threading.Thread(target=run_pipeline, args=(task_id,), daemon=True).start()
    st.session_state.watch_task_id = task_id


def _watch() -> None:
    """轮询执行中的任务，实时显示每一步已完成产物，直至终态。"""
    watch_id = st.session_state.get("watch_task_id")
    if not watch_id:
        return
    with SessionLocal() as s:
        t = s.get(ListingTask, watch_id)
    if t is None:
        st.session_state.pop("watch_task_id", None)
        return

    if t.status in (TaskStatus.CREATED, TaskStatus.ANALYZING):
        ph = st.empty()
        while t.status in (TaskStatus.CREATED, TaskStatus.ANALYZING):
            with ph.container():
                st.info(f"任务 #{watch_id} 执行中…  {_progress_line(t)}")
                _steps_view(t)
            time.sleep(2)
            with SessionLocal() as s:
                t = s.get(ListingTask, watch_id)
        ph.empty()

    st.session_state.pop("watch_task_id", None)
    if t.status == TaskStatus.PENDING_REVIEW:
        st.success(f"任务 #{watch_id} 已生成，请到「人工审核」页签核对")
    elif t.status == TaskStatus.FAILED:
        st.error(f"任务 #{watch_id} 执行失败，可到「历史任务」页签查看错误并重试")
    _steps_view(t, expanded=True)


tab_new, tab_review, tab_history = st.tabs(["新建任务", "人工审核", "历史任务"])

with tab_new:
    st.header("上传产品，生成俄语 Listing")
    with st.form("new_task"):
        image = st.file_uploader("产品原图", type=["jpg", "jpeg", "png", "webp"])
        col1, col2 = st.columns(2)
        price = col1.number_input("采购价（元）", min_value=0.0, step=0.1)
        weight = col2.number_input("重量（克）", min_value=0, step=10)
        submitted = st.form_submit_button("开始生成", type="primary")

    if submitted:
        if not image:
            st.error("请先上传产品图片")
            st.stop()
        suffix = Path(image.name).suffix or ".jpg"
        save_path = settings.uploads / f"{uuid.uuid4().hex}{suffix}"
        save_path.write_bytes(image.getbuffer())

        task_id = create_task(str(save_path), price, weight)
        _launch(task_id)
        st.rerun()

    _watch()

with tab_review:
    st.header("待审核任务")
    with SessionLocal() as s:
        tasks = (
            s.query(ListingTask)
            .filter(ListingTask.status == TaskStatus.PENDING_REVIEW)
            .order_by(ListingTask.id.desc())
            .all()
        )

    if not tasks:
        st.info("暂无待审核任务")
    else:
        selected = st.selectbox("选择任务", tasks, format_func=lambda t: f"#{t.id} · 采购价 {t.purchase_price} 元 · {t.created_at:%m-%d %H:%M}")

        col_img, col_content = st.columns([1, 2])
        with col_img:
            st.image(selected.image_path, caption="产品原图", use_container_width=True)
        with col_content:
            _steps_view(selected, expanded=True)

        col_ok, col_no, _ = st.columns([1, 1, 4])
        if col_ok.button("通过", type="primary", key=f"ok_{selected.id}"):
            review_task(selected.id, approved=True)
            st.rerun()
        if col_no.button("退回", key=f"no_{selected.id}"):
            review_task(selected.id, approved=False)
            st.rerun()

with tab_history:
    st.header("历史任务")
    filters = {
        "全部": None,
        "待审核": TaskStatus.PENDING_REVIEW,
        "已通过": TaskStatus.APPROVED,
        "已退回": TaskStatus.REJECTED,
        "失败": TaskStatus.FAILED,
        "执行中": TaskStatus.ANALYZING,
    }
    choice = st.selectbox("按状态筛选", list(filters))
    with SessionLocal() as s:
        q = s.query(ListingTask).order_by(ListingTask.id.desc())
        if filters[choice]:
            q = q.filter(ListingTask.status == filters[choice])
        tasks = q.all()

    running = sum(1 for t in tasks if t.status in (TaskStatus.CREATED, TaskStatus.ANALYZING))
    if running and st.button(f"刷新（{running} 个任务执行中）"):
        st.rerun()

    if not tasks:
        st.info("没有符合条件的任务")
    else:
        selected = st.selectbox(
            "选择任务",
            tasks,
            format_func=lambda t: f"#{t.id} · {STATUS_LABEL[t.status]} · 采购价 {t.purchase_price} 元 · {t.created_at:%m-%d %H:%M}",
        )

        col_img, col_content = st.columns([1, 2])
        with col_img:
            st.image(selected.image_path, caption="产品原图", use_container_width=True)
            st.caption(f"状态：{STATUS_LABEL[selected.status]}")
        with col_content:
            _steps_view(selected, expanded=True)
            if selected.status == TaskStatus.FAILED:
                st.warning("重试会清空旧产物，重新执行完整流水线")
                if st.button("重试", type="primary", key=f"retry_{selected.id}"):
                    _launch(selected.id, retry=True)
                    st.rerun()
