"""Copy only model-routing metadata and its encrypted credentials for verification."""
import subprocess

old, new = "agent-studio-173-postgres-1", "agent-evolution-173-postgres"
for table, columns, where in (
    ("capability_catalogs", "tenant_id,revision,updated_by,updated_at,payload", ""),
    ("mcp_credentials", "tenant_id,owner_user_id,reference,revision,key_names,ciphertext,updated_by,updated_at", "WHERE owner_user_id = 'tenant:model-control-plane'"),
):
    count = subprocess.check_output(["docker", "exec", new, "psql", "-U", "harness", "-d", "evolution",
                                     "-Atc", f"SELECT count(*) FROM {table}"]).strip()
    if count != b"0":
        print(f"{table}: existing isolated configuration retained")
        continue
    data = subprocess.check_output(["docker", "exec", old, "psql", "-U", "harness", "-d", "harness",
                                   "-c", f"COPY (SELECT {columns} FROM {table} {where}) TO STDOUT"])
    subprocess.run(["docker", "exec", "-i", new, "psql", "-U", "harness", "-d", "evolution",
                    "-c", f"COPY {table} ({columns}) FROM STDIN"], input=data, check=True)
print("Copied routing metadata and encrypted model credentials only; no accounts or business records")
