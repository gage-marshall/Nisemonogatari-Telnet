FROM python:3.12-slim
WORKDIR /app
COPY telnet_server.py .
COPY output/ output/
EXPOSE 23
CMD ["python", "telnet_server.py", "--host", "0.0.0.0", "--port", "23"]
