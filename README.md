# Ozon 上架 Agent

用 AI 自动生成 Ozon（俄罗斯电商）商品 Listing：上传一张产品图，自动完成「图像解析 → 卖点挖掘 → 俄语 Listing」，人工审核后即可上架。面向做俄罗斯市场的中国卖家，解决语言壁垒和上架效率问题。

## 核心流程

```
产品图 + 采购价/重量
      │
      ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  ① 图像解析   │────▶│  ② 卖点挖掘   │────▶│ ③ 俄语 Listing│
│ qwen-vl-max  │     │ qwen-plus    │     │ CrewAI Agent  │
│ 多模态看图    │     │ 中文卖点输出  │     │ 俄语标题/描述 │
└──────────────┘     └──────────────┘     └──────────────┘
                                                   │
                                                   ▼
                                           ┌──────────────┐
                                           │  ④ 人工审核   │
                                           │ 通过 / 退回   │
                                           └──────────────┘
```

- **① 图像解析**：通义千问 VL 多模态模型识别产品品类、材质、颜色、功能等特征
- **② 卖点挖掘**：基于图像特征 + 采购价，生成针对俄罗斯市场的中文卖点
- **③ 俄语 Listing**：CrewAI Agent 将卖点转化为符合 Ozon 规范的俄语标题和描述
- **④ 人工审核**：在后台逐项核对，通过即完成，退回可重试

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| Web UI | Streamlit | 浏览器操作界面，含登录、任务进度、历史 |
| Agent 编排 | CrewAI | 串行流水线：卖点 → 俄语 Listing |
| LLM | DashScope（通义千问） | qwen-vl-max 看图 + qwen-plus 文本，OpenAI 兼容接口 |
| 数据库 | SQLite | 任务记录、产物、审核状态落库 |
| 部署 | Docker Compose | 单容器，镜像与代码分离 |

## 部署方式

采用「开发机 build + 线上机 run」分离模式，代码通过 NAS 共享挂载：

```
开发机                          线上机（1.6G ECS）
docker build ──▶ /cephfs ──▶ docker load
（只需 Dockerfile +            docker compose up -d
  requirements.txt）            代码 ./app 本地挂载
```

- 改代码：线上机直接改 `./app`，Streamlit 自动加载，无需动镜像
- 改依赖：开发机重新 build → save 到 NAS → 线上 load → 重启

## 项目结构

```
ozon-agent/
├── app/
│   ├── config.py          # 配置（.env 读取）
│   ├── db.py              # SQLite 会话
│   ├── models.py          # 数据模型
│   ├── llm.py             # DashScope LLM 封装
│   ├── pipeline.py        # 流水线编排
│   ├── agents/
│   │   └── crew.py        # CrewAI Agent 定义
│   └── ui/
│       └── streamlit_app.py  # Web 后台
├── Dockerfile             # 只装依赖，不含代码
├── docker-compose.yml     # image 模式 + 代码挂载
└── requirements.txt
```

## 配置

复制 `.env.example` 为 `.env`，填入：

```bash
DASHSCOPE_API_KEY=sk-xxx     # 百炼平台 API Key
APP_PASSWORD=your-password   # 后台登录密码
```

## 后续规划

- 产品图生成（通义万相 wanx）：审核通过后自动生成 Ozon 主图
- 真正上架：对接 Ozon Seller API 或一键导出资料包
- MCP 服务化：将流水线暴露为 MCP tool，支持 API 调用
