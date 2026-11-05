ARG BASE_IMAGE=harbor.shdata.com:5000/deerflow/sandbox:1.11.0
FROM ${BASE_IMAGE}
USER root
RUN groupadd --gid 28000 kai-exec \
    && useradd --uid 28000 --gid 28000 --home-dir /home/kai --create-home --shell /bin/bash kai-exec \
    && install -d -m 700 /var/lib/kai-computer /var/lib/kai-computer/spool \
    && install -d -m 755 /opt/kai-computer
COPY src/harness/computer/supervisor.py /opt/kai-computer/supervisor.py
RUN chmod 644 /opt/kai-computer/supervisor.py
LABEL org.shdata.kai.computer.guard="1"
ENV HOME=/home/kai
WORKDIR /home/kai
USER 0:0
