"""Streamlit 后台：任务列表 → 任务详情（每步可单独重跑）。"""
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
from app.llm import IMAGE_PROMPT_TEMPLATE, VISION_PROMPT
from app.agents.crew import LISTING_PROMPT_TEMPLATE, SELLING_PROMPT_TEMPLATE
from app.models import ListingTask, TaskStatus
from app.pipeline import (
    create_task,
    generate_image,
    rerun_image,
    rerun_text,
    rerun_vision,
    retry_task,
    review_task,
    run_pipeline,
    upload_to_ozon,
)

st.set_page_config(page_title="Ozon 上架 Agent", layout="wide")

# 深色科技感主题：克制的边框、统一间距、红色主色，无渐变无emoji
st.markdown(
    """
    <style>
    .stApp { background-color: #0b0e14; }
    [data-testid="stHeader"] { background: transparent; }
    .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1100px; }

    [data-testid="stExpander"] {
        background: #141821;
        border: 1px solid #232836;
        border-radius: 8px;
        margin-bottom: 0.6rem;
    }
    [data-testid="stExpander"]:hover { border-color: #3a3f50; }

    [data-testid="stForm"] {
        background: #141821;
        border: 1px solid #232836;
        border-radius: 10px;
        padding: 1.2rem;
    }
    [data-testid="stFileUploader"] { background: #0f131a; border: 1px dashed #2a3040; border-radius: 8px; }

    .stButton > button {
        border-radius: 6px;
        font-weight: 500;
        border: 1px solid #2a3040;
    }
    .stButton > button[kind="primary"] { background-color: #FF4B4B; border-color: #FF4B4B; }
    .stButton > button[kind="primary"]:hover { background-color: #e63939; border-color: #e63939; }

    [data-testid="stSidebar"] { background-color: #0f131a; border-right: 1px solid #232836; }
    [data-testid="stSidebarUserContent"] { padding-top: 1rem; }

    .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
    .dot-done { background: #2ea043; }
    .dot-doing { background: #d29922; }
    .dot-pending { background: #484f58; }

    .task-card {
        background: #141821;
        border: 1px solid #232836;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
        cursor: pointer;
    }
    .task-card:hover { border-color: #3a3f50; }
    </style>
    """,
    unsafe_allow_html=True,
)

STATUS_LABEL = {
    TaskStatus.CREATED: "待执行",
    TaskStatus.ANALYZING: "执行中",
    TaskStatus.PENDING_REVIEW: "待审核",
    TaskStatus.IMAGE_GENERATING: "生成主图中",
    TaskStatus.COMPLETED: "已完成",
    TaskStatus.APPROVED: "已通过",
    TaskStatus.REJECTED: "已退回",
    TaskStatus.FAILED: "失败",
}

STATUS_COLOR = {
    TaskStatus.CREATED: "#484f58",
    TaskStatus.ANALYZING: "#d29922",
    TaskStatus.PENDING_REVIEW: "#58a6ff",
    TaskStatus.IMAGE_GENERATING: "#d29922",
    TaskStatus.COMPLETED: "#2ea043",
    TaskStatus.APPROVED: "#2ea043",
    TaskStatus.REJECTED: "#f85149",
    TaskStatus.FAILED: "#f85149",
}


def login_gate() -> None:
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


def _dot(done: bool) -> str:
    return '<span class="dot dot-done"></span>' if done else '<span class="dot dot-pending"></span>'


def _progress_line(t: ListingTask) -> str:
    return (
        f"{_dot(bool(t.vision_result))}图像解析 ｜ "
        f"{_dot(bool(t.selling_points))}卖点 ｜ "
        f"{_dot(bool(t.listing))}Listing ｜ "
        f"{_dot(bool(t.image_url))}主图"
    )


def _get_task(task_id: int) -> ListingTask | None:
    with SessionLocal() as s:
        return s.get(ListingTask, task_id)


def _watch_task(task_id: int) -> None:
    """轮询执行中的任务，实时显示进度，直至终态。"""
    active = (TaskStatus.CREATED, TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING)
    t = _get_task(task_id)
    if t is None:
        return
    if t.status not in active:
        return
    ph = st.empty()
    while True:
        t = _get_task(task_id)
        if t is None or t.status not in active:
            break
        with ph.container():
            st.markdown(f"**执行中…**　{_progress_line(t)}", unsafe_allow_html=True)
        time.sleep(2)
    ph.empty()


# ---------- 页面路由 ----------
page = st.session_state.get("page", "list")

if page == "new":
    st.header("新建任务")
    if st.button("← 返回列表"):
        st.session_state.page = "list"
        st.rerun()
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
        threading.Thread(target=run_pipeline, args=(task_id,), daemon=True).start()
        st.session_state.selected_task_id = task_id
        st.session_state.page = "detail"
        st.rerun()

elif page == "detail":
    task_id = st.session_state.get("selected_task_id")
    t = _get_task(task_id) if task_id else None

    if t is None:
        st.error("任务不存在")
        if st.button("返回列表"):
            st.session_state.page = "list"
            st.rerun()
    else:
        # 顶部：返回 + 状态
        col_back, col_title = st.columns([1, 5])
        with col_back:
            if st.button("← 返回列表"):
                st.session_state.page = "list"
                st.rerun()
        with col_title:
            color = STATUS_COLOR[t.status]
            st.markdown(
                f"### 任务 #{t.id} &nbsp; "
                f'<span style="color:{color};font-size:0.9rem">{STATUS_LABEL[t.status]}</span>',
                unsafe_allow_html=True,
            )

        # 实时轮询（执行中时）
        _watch_task(t.id)

        # 基本信息
        col_img, col_info = st.columns([1, 2])
        with col_img:
            st.image(t.image_path, caption="产品原图", use_container_width=True)
        with col_info:
            st.write(f"**采购价：** {t.purchase_price} 元")
            st.write(f"**重量：** {t.weight_g} 克")
            st.write(f"**创建时间：** {t.created_at:%Y-%m-%d %H:%M}")
            st.markdown(_progress_line(t), unsafe_allow_html=True)

        st.divider()

        # 步骤 ① 图像解析
        with st.expander("① 图像解析", expanded=True):
            if t.vision_result:
                st.json(json.loads(t.vision_result))
            else:
                st.caption("未生成")
            st.markdown("**提示词（可修改）**")
            vision_prompt = st.text_area(
                "图像解析提示词",
                value=VISION_PROMPT,
                height=100,
                key=f"vision_prompt_{t.id}",
                label_visibility="collapsed",
            )
            if t.status not in (TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING):
                if st.button("重新执行此步骤", key=f"re_vision_{t.id}"):
                    rerun_vision(t.id, prompt=vision_prompt)
                    st.rerun()

        # 步骤 ②③ 卖点 + Listing
        with st.expander("② 卖点挖掘", expanded=True):
            if t.selling_points:
                st.json(json.loads(t.selling_points))
            else:
                st.caption("未生成")
            st.markdown("**卖点提示词（可修改，支持 {vision} {purchase_price} {weight_g} 占位）**")
            selling_prompt = st.text_area(
                "卖点提示词",
                value=SELLING_PROMPT_TEMPLATE,
                height=100,
                key=f"selling_prompt_{t.id}",
                label_visibility="collapsed",
            )
        with st.expander("③ 俄语 Listing", expanded=True):
            if t.listing:
                st.json(json.loads(t.listing))
            else:
                st.caption("未生成")
            st.markdown("**Listing 提示词（可修改，支持 {vision} {purchase_price} {weight_g} 占位）**")
            listing_prompt = st.text_area(
                "Listing 提示词",
                value=LISTING_PROMPT_TEMPLATE,
                height=100,
                key=f"listing_prompt_{t.id}",
                label_visibility="collapsed",
            )
        if t.status not in (TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING):
            if st.button("重新执行 ②③（卖点 + Listing）", key=f"re_text_{t.id}"):
                if not t.vision_result:
                    st.error("需要先有图像解析结果")
                else:
                    rerun_text(t.id, selling_prompt=selling_prompt, listing_prompt=listing_prompt)
                    st.rerun()

        # 审核操作（仅 PENDING_REVIEW 时显示）
        if t.status == TaskStatus.PENDING_REVIEW:
            st.divider()
            st.markdown("**人工审核**")
            col_ok, col_no, _ = st.columns([1, 1, 4])
            if col_ok.button("通过并生成主图", type="primary", key=f"ok_{t.id}"):
                review_task(t.id, approved=True)
                st.rerun()
            if col_no.button("退回", key=f"no_{t.id}"):
                review_task(t.id, approved=False)
                st.rerun()

        # 步骤 ④ Ozon 主图
        with st.expander("④ Ozon 主图（通义万相）", expanded=True):
            if t.image_url:
                st.image(t.image_url, caption="生成的 Ozon 主图", use_container_width=True)
                st.caption(t.image_url)
            else:
                st.caption("未生成")
            st.markdown("**生图提示词（可修改，支持 {ptype} {desc} {material} {features} 占位）**")
            image_prompt = st.text_area(
                "生图提示词",
                value=IMAGE_PROMPT_TEMPLATE,
                height=140,
                key=f"image_prompt_{t.id}",
                label_visibility="collapsed",
            )
            if t.status not in (TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING):
                if st.button("重新生成主图", key=f"re_image_{t.id}"):
                    if not t.vision_result:
                        st.error("需要先有图像解析结果")
                    else:
                        rerun_image(t.id, prompt=image_prompt)
                        st.rerun()

        # 旧版 APPROVED 兼容
        if t.status == TaskStatus.APPROVED:
            st.info("该任务为旧版审核通过数据，尚未生成 Ozon 主图")
            if st.button("生成 Ozon 主图", type="primary", key=f"genimg_{t.id}"):
                rerun_image(t.id)
                st.rerun()

        # 错误信息
        if t.error:
            with st.expander("错误信息", expanded=True):
                st.code(t.error, language="text")

        # Ozon 上架
        if settings.ozon_client_id and settings.ozon_api_key and t.listing and t.image_url:
            st.divider()
            st.markdown("**Ozon 上架**")
            price_rub = round(t.purchase_price * settings.ozon_price_markup * settings.ozon_cny_to_rub)
            st.caption(f"售价：{price_rub} ₽（采购价 × {settings.ozon_price_markup} × {settings.ozon_cny_to_rub}）")
            if t.ozon_status:
                st.caption(f"上次上架状态：{t.ozon_status}")
            if st.button("上架到 Ozon", type="primary", key=f"ozon_{t.id}"):
                with st.spinner("正在提交到 Ozon…"):
                    try:
                        result = upload_to_ozon(t.id)
                        st.success(f"上架提交完成：{result.get('result', {}).get('status', '未知')}")
                        st.json(result)
                    except Exception as e:
                        st.error(f"上架失败：{e}")
                st.rerun()

        # 整体重新执行
        if t.status not in (TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING):
            st.divider()
            st.caption("整体重新执行会清空所有产物并从头开始")
            if st.button("整体重新执行", type="primary", key=f"retry_all_{t.id}"):
                retry_task(t.id)
                threading.Thread(target=run_pipeline, args=(t.id,), daemon=True).start()
                st.rerun()

else:
    # ---------- 首页：任务列表 ----------
    col_title, col_new = st.columns([5, 1])
    with col_title:
        st.header("任务列表")
    with col_new:
        if st.button("+ 新建任务", type="primary"):
            st.session_state.page = "new"
            st.rerun()

    filters = {
        "全部": None,
        "待审核": TaskStatus.PENDING_REVIEW,
        "生成主图中": TaskStatus.IMAGE_GENERATING,
        "已完成": TaskStatus.COMPLETED,
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

    running = sum(1 for t in tasks if t.status in (TaskStatus.CREATED, TaskStatus.ANALYZING, TaskStatus.IMAGE_GENERATING))
    if running:
        st.caption(f"{running} 个任务执行中，页面每 3 秒自动刷新")
        time.sleep(3)
        st.rerun()

    if not tasks:
        st.info("暂无任务，点击右上角「新建任务」开始")
    else:
        for t in tasks:
            color = STATUS_COLOR[t.status]
            with st.container():
                st.markdown(
                    f'<div class="task-card">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="font-size:1.05rem;font-weight:600;">#{t.id} &nbsp; '
                    f'<span style="color:{color};font-size:0.85rem">{STATUS_LABEL[t.status]}</span></span>'
                    f'<span style="color:#8b949e;font-size:0.8rem">{t.created_at:%m-%d %H:%M}</span>'
                    f'</div>'
                    f'<div style="margin-top:0.5rem;color:#8b949e;font-size:0.85rem">'
                    f'采购价 {t.purchase_price} 元 · {t.weight_g}g'
                    f'</div>'
                    f'<div style="margin-top:0.4rem;font-size:0.8rem">{_progress_line(t)}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("查看详情", key=f"view_{t.id}"):
                    st.session_state.selected_task_id = t.id
                    st.session_state.page = "detail"
                    st.rerun()
