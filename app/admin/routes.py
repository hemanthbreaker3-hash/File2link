from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.database.connection import files_col, users_col, logs_col, settings
import psutil
import secrets

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()

def is_admin(credentials: HTTPBasicCredentials = Depends(security)):
    valid_user = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    valid_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True

@router.get("/")
async def admin_dashboard(request: Request, _: bool = Depends(is_admin)):
    total_users = await users_col.count_documents({})
    total_files = await files_col.count_documents({})
    cpu_usage = psutil.cpu_percent(interval=None)
    ram_usage = psutil.virtual_memory().percent
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "total_users": total_users,
        "total_files": total_files,
        "cpu": cpu_usage,
        "ram": ram_usage,
    })

@router.get("/users")
async def admin_users(request: Request, _: bool = Depends(is_admin)):
    users = await users_col.find().sort("joined_at", -1).to_list(100)
    return templates.TemplateResponse("admin/users.html", {"request": request, "users": users})

@router.get("/files")
async def admin_files(request: Request, q: str = None, _: bool = Depends(is_admin)):
    query = {}
    if q:
        query = {"filename": {"$regex": q[:100], "$options": "i"}}
    files = await files_col.find(query).sort("created_at", -1).to_list(100)
    return templates.TemplateResponse("admin/files.html", {"request": request, "files": files, "query": q})

@router.post("/files/delete/{short_code}")
async def delete_file(short_code: str, _: bool = Depends(is_admin)):
    await files_col.delete_one({"short_code": short_code})
    return {"status": "success"}
