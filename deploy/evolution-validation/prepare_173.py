"""Run on 173 in a dedicated directory. Never print or commit environment values."""
import json
import os
import secrets
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "agent-evolution-173"


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def save(name, data):
    target = ROOT / name
    target.write_text(data)
    target.chmod(0o600)


if (ROOT / "compose.json").exists():
    raise SystemExit("Configuration exists; reuse it rather than rotating validation credentials")
for port in (8802, 3302):
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", port))
source = inspect("agent-studio-173-api-1")
env = dict(line.split("=", 1) for line in source["Config"]["Env"])
env = {key: value for key, value in env.items() if key.startswith("HARNESS_")}
password, api_token = secrets.token_hex(24), secrets.token_hex(32)
env.update({
    "HARNESS_DATABASE_URL": f"postgresql+asyncpg://harness:{password}@postgres:5432/evolution",
    "HARNESS_REDIS_URL": "redis://redis:6379/0", "HARNESS_MINIO_ENDPOINT": "minio:9000",
    "HARNESS_MINIO_ACCESS_KEY": "evolution", "HARNESS_MINIO_SECRET_KEY": password,
    "HARNESS_MINIO_BUCKET": "evolution-validation", "HARNESS_MINIO_SECURE": "false",
    "HARNESS_API_BEARER_TOKEN": api_token, "HARNESS_OTEL_ENABLED": "false",
    "HARNESS_AUTH_ALLOW_REGISTRATION": "true", "HARNESS_AUTH_DEFAULT_TENANT_ID": "local",
    "HARNESS_RELIABILITY_REAPER_INTERVAL_SECONDS": "5",
    "HARNESS_WORKER_CONCURRENCY": "2", "HARNESS_WEKNORA_BASE_URL": "",
    "HARNESS_WEKNORA_EMAIL": "", "HARNESS_WEKNORA_PASSWORD": "",
    "HARNESS_AUTH_GOOGLE_CLIENT_ID": "", "HARNESS_AUTH_GOOGLE_CLIENT_SECRET": "",
    "HARNESS_AUTH_GITHUB_CLIENT_ID": "", "HARNESS_AUTH_GITHUB_CLIENT_SECRET": "",
})
# Secrets stay in a root-only file on this host. JWT key is reused only to decrypt
# copied tenant model connections; no existing user accounts/tokens are copied.
save("api.env", "".join(f"{k}={v}\n" for k, v in env.items()))
save("web.env", "\n".join([
    "HARNESS_API_URL=http://api:8000", f"HARNESS_API_BEARER_TOKEN={api_token}",
    "HARNESS_PUBLIC_API_URL=http://172.20.109.173:8802", "HARNESS_API_PUBLIC_PORT=8802",
    "AUTH_PUBLIC_URL=http://172.20.109.173:3302", "AUTH_COOKIE_SECURE=false",
    "AUTH_COOKIE_PREFIX=harness_evolution",
    "HARNESS_OTEL_ENABLED=false", "HOSTNAME=0.0.0.0", "PORT=3000", "",
]))
services = {
    "postgres": {"image": "harbor.shdata.com:5000/agent-studio/amd64/axis-postgres:18.1-vector0.8.6",
                 "environment": {"POSTGRES_USER":"harness", "POSTGRES_PASSWORD":password,
                                 "POSTGRES_DB":"evolution"}, "volumes":["pg:/var/lib/postgresql"]},
    "redis": {"image":"harbor.shdata.com:5000/shdata_scip/amd64/redis:7.2-alpine"},
    "minio": {"image":inspect("agent-studio-173-minio-1")["Config"]["Image"],
              "environment":{"MINIO_ROOT_USER":"evolution", "MINIO_ROOT_PASSWORD":password},
              "command":["server","/data"], "volumes":["objects:/data"]},
    "api": {"image":"kai/axis-api:evolution-20260920", "env_file":["api.env"],
            "ports":["8802:8000"], "depends_on":["postgres","redis","minio"]},
    "worker": {"image":"kai/axis-api:evolution-20260920", "env_file":["api.env"],
               "entrypoint":["entrypoint-worker"], "depends_on":["postgres","redis","minio"],
               "healthcheck":{"test":["CMD","python","-c",
                   "from pathlib import Path; raise SystemExit('harness-worker' not in Path('/proc/1/cmdline').read_text())"],
                   "interval":"10s", "timeout":"3s", "start_period":"20s", "retries":6}},
    "web": {"image":"kai/axis-web:evolution-20260920", "env_file":["web.env"],
            "ports":["3302:3000"], "depends_on":["api"]},
}
for name, service in services.items():
    service["container_name"] = f"{PREFIX}-{name}"
    service["restart"] = "unless-stopped"
save("compose.json", json.dumps({"name":PREFIX,"services":services,
                                    "volumes":{"pg":{},"objects":{}}}, indent=2))
print("Prepared isolated validation stack: API 8802, Web 3302; secret values withheld")
