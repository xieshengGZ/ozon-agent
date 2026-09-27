FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# 阿里云服务器走国内镜像加速；海外环境去掉 -i 参数即可
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY app ./app
RUN mkdir -p data

EXPOSE 8501
CMD ["streamlit", "run", "app/ui/streamlit_app.py", "--server.address=0.0.0.0", "--server.headless=true"]
