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

    # 留空则使用项目目录下 data/app.db
    database_url: str | None = None
    upload_dir: str = "data/uploads"

    # 后台登录密码（Streamlit 页面访问口令；留空则拒绝所有访问）
    app_password: str = ""

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"

    @property
    def uploads(self) -> Path:
        p = Path(self.upload_dir)
        return p if p.is_absolute() else BASE_DIR / p


settings = Settings()
settings.uploads.mkdir(parents=True, exist_ok=True)
