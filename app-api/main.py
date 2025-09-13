# File: app-api/main.py

import os
import uuid
import io
import logging
import secrets
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from minio import Minio
from minio.error import S3Error

# Tải các biến môi trường
load_dotenv()

# --- SỬA LỖI: Khởi tạo HTTPBearer ---
security = HTTPBearer()

# --- SỬA LỖI: Lấy token từ biến môi trường và kiểm tra sự tồn tại ---
SECRET_TOKEN = os.getenv("SECRET_TOKEN")
if not SECRET_TOKEN:
    raise RuntimeError("SECRET_TOKEN is not set in the environment variables.")

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Xác thực Bearer Token."""
    # SỬA LỖI: Sử dụng credentials.credentials thay vì credentials.token
    if not secrets.compare_digest(credentials.credentials, SECRET_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# Log sẽ có định dạng: INFO: 2025-09-13 11:15:00,123 - [thông điệp]
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:     %(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

app = FastAPI(title="MinIO Uploader API")

# Cấu hình MinIO Client
MINIO_API_ENDPOINT = os.getenv("MINIO_API_ENDPOINT", "localhost:9000")
MINIO_PUBLLIC_URL = os.getenv("MINIO_PUBLLIC_URL", "http://minio.local")
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD")
# --- CẢI TIẾN: Sử dụng biến môi trường để xác định chế độ secure ---
USE_SECURE_MINIO = os.getenv("MINIO_SECURE", "false").lower() == "true"


# Khởi tạo MinIO Client
minio_client = Minio(
    MINIO_API_ENDPOINT.replace("http://", "").replace("https://", ""),
    access_key=MINIO_ROOT_USER,
    secret_key=MINIO_ROOT_PASSWORD,
    secure=USE_SECURE_MINIO
)

class DeleteFileRequest(BaseModel):
    bucket_name: str
    object_name: str


@app.get("/")
def read_root():
    return {"message": "Welcome to MinIO File Uploader API"}


@app.post("/minio/upload", dependencies=[Depends(verify_token)])
async def create_upload_file(
    bucket: str = Form(...),
    folder: str = Form(""),
    file: UploadFile = File(...)
):
    log.info(f"POST /minio/upload - Params: bucket='{bucket}', folder='{folder}', filename='{file.filename}'")

    try:
        found = minio_client.bucket_exists(bucket)
        if not found:
            minio_client.make_bucket(bucket)
            log.info(f"Bucket '{bucket}' created successfully.")

        contents = await file.read()
        file_size = len(contents)
        content_type = file.content_type

        if file_size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        object_name = file.filename

        if folder:
            object_name = f"{folder.strip('/')}/{object_name}"

        data_stream = io.BytesIO(contents)
        minio_client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=data_stream,
            length=file_size,
            content_type=content_type,
        )
        
        # Xây dựng URL công khai từ biến môi trường MINIO_PUBLLIC_URL ---
        public_url = f"{MINIO_PUBLLIC_URL.strip('/')}/{bucket}/{object_name}"

        return {
            "bucket_name": bucket,
            "file_name": object_name,
            "url": public_url,
            "file_size": file_size,
            "content_type": content_type,
        }
    except S3Error as exc:
        log.error(f"MinIO S3Error: {exc}")
        raise HTTPException(status_code=500, detail=f"MinIO error: {exc}")
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.delete("/minio/delete", dependencies=[Depends(verify_token)])
async def delete_file(request: DeleteFileRequest):
    log.info(f"DELETE /minio/delete - Params: {request.dict()}")

    try:
        # Kiểm tra xem object có tồn tại không để trả về lỗi 404 chính xác
        minio_client.stat_object(request.bucket_name, request.object_name)

        # Xóa object
        minio_client.remove_object(request.bucket_name, request.object_name)

        return {
            "status": "success",
            "message": f"File '{request.object_name}' was successfully deleted from bucket '{request.bucket_name}'."
        }
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            raise HTTPException(
                status_code=404,
                detail=f"File '{request.object_name}' not found in bucket '{request.bucket_name}'."
            )
        log.error(f"MinIO S3Error on delete: {exc}")
        raise HTTPException(status_code=500, detail=f"MinIO error: {exc.code}")
    except Exception as e:
        log.error(f"Unexpected error on delete: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")