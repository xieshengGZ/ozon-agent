"""Streamlit 后台：新建任务（上传+执行流水线）与人工审核两个页签。"""
import json
import sys
import uuid
from pathlib import Path

import streamlit as st

# 兼容 `streamlit run app/ui/streamlit_app.py` 的启动方式（脚本目录会被加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import ListingTask, TaskStatus
from app.pipeline import create_task, review_task, run_pipeline

st.set_page_config(page_title="Ozon 上架 Agent", layout="wide")
init_db()

tab_new, tab_review = st.tabs(["新建任务", f"人工审核"])

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
        with st.spinner("流水线执行中：图像解析 → 卖点挖掘 → 俄语 Listing…"):
            try:
                run_pipeline(task_id)
            except Exception:
                st.error(f"任务 {task_id} 执行失败，详情见数据库 error 字段")
                st.stop()
        st.success(f"任务 {task_id} 已生成，请到「人工审核」页签核对")

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
            if selected.vision_result:
                with st.expander("图像解析"):
                    st.json(json.loads(selected.vision_result))
            if selected.selling_points:
                with st.expander("卖点", expanded=True):
                    st.json(json.loads(selected.selling_points))
            if selected.listing:
                with st.expander("俄语 Listing", expanded=True):
                    st.json(json.loads(selected.listing))

        col_ok, col_no, _ = st.columns([1, 1, 4])
        if col_ok.button("通过", type="primary", key=f"ok_{selected.id}"):
            review_task(selected.id, approved=True)
            st.rerun()
        if col_no.button("退回", key=f"no_{selected.id}"):
            review_task(selected.id, approved=False)
            st.rerun()
