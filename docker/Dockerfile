FROM python:3.11-slim

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app

ENV DATA_DIR=/data
ENV TZ=America/Denver

EXPOSE 4050
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "4050"]
