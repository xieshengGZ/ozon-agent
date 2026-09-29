"""全局配置：全部通过 .env 注入，默认值见 .env.example。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_vl_model: str = "qwen-vl-max"
    qwen_text_model: str = "qwen-plus"
    # 图生图模型（以原图为参考生成白底主图）
    wan_image_model: str = "wan2.7-image-pro"

    # 留空则使用项目目录下 data/app.db
    database_url: str | None = None
    upload_dir: str = "data/uploads"

    # 后台登录密码（Streamlit 页面访问口令；留空则拒绝所有访问）
    app_password: str = ""

    # Ozon Seller API（上架用，留空则不显示上架按钮）
    ozon_client_id: str = ""
    ozon_api_key: str = ""
    ozon_base_url: str = "https://api-seller.ozon.ru"
    # 售价 = 采购价(CNY) × markup × cny_to_rub
    ozon_price_markup: float = 2.0
    ozon_cny_to_rub: float = 12.5

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"

    @property
    def uploads(self) -> Path:
        p = Path(self.upload_dir)
        return p if p.is_absolute() else BASE_DIR / p


settings = Settings()
settings.uploads.mkdir(parents=True, exist_ok=True)
