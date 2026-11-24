FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector ./collector
RUN useradd --system --uid 10001 collector && mkdir /data && chown collector /data
USER collector
VOLUME /data
CMD ["python", "-m", "collector"]
