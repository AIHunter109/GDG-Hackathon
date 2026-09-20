FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY main/requirements.txt main/requirements-cloud.txt ./main/
RUN pip install --no-cache-dir -r main/requirements-cloud.txt
COPY main ./main
EXPOSE 8080
CMD ["python", "main/dashboard.py"]
