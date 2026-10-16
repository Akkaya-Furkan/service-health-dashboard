FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd -r -u 10001 shd && mkdir /data && chown shd /data
USER shd
ENV SHD_DB=/data/shd.sqlite SHD_BIND=0.0.0.0
EXPOSE 8000
CMD ["shd"]
