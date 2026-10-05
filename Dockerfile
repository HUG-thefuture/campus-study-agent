# 交付说明里的“Docker 启动”：SQLite 文件随容器卷持久化即可，无外部服务依赖。
# 本机构建：docker build -t study-agent . && docker run -p 8000:8000 study-agent
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV LLM_PROVIDER=mock
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
