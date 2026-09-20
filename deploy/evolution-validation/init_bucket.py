"""Idempotent initialization for the isolated validation object store."""
import os
from minio import Minio
client = Minio(os.environ['HARNESS_MINIO_ENDPOINT'],
               access_key=os.environ['HARNESS_MINIO_ACCESS_KEY'],
               secret_key=os.environ['HARNESS_MINIO_SECRET_KEY'], secure=False)
bucket = os.environ['HARNESS_MINIO_BUCKET']
if bucket != 'evolution-validation':
    raise SystemExit('Refusing to initialize a bucket outside the validation namespace')
if not client.bucket_exists(bucket):
    client.make_bucket(bucket)
print('Isolated artifact bucket is ready')
