# Follow-up input epoch verification on the deployed pilot viewer.
FROM kai/computer-viewer:20261103-remote-computer-v1
COPY src/harness/computer/viewer.py /app/project/lib/python3.12/site-packages/harness/computer/viewer.py
