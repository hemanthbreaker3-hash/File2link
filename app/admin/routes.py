from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from fastapi.templating import Jinja2Templates
from app.database.connection import files_col, users_col, logs_col, settings
import psutil
import time

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "templates"))

security = HTTPBasic(auto_error=False)

async def is_admin(credentials: HTTPBasicCredentials | None = Depends(security)):
    token = settings.ADMIN_PANEL_TOKEN
    if not token or credentials is None:
        raise HTTPException(status_code=401, detail="Admin authentication required", headers={"WWW-Authenticate": "Basic"})
    valid_user = secrets.compare_digest(credentials.username, "admin")
    valid_token = secrets.compare_digest(credentials.password, token)
    if not (valid_user and valid_token):
        raise HTTPException(status_code=401, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return True

@router.get("/")
async def admin_dashboard(request: Request, _: bool = Depends(is_admin)):
        
    total_users = await users_col.count_documents({})
    total_files = await files_col.count_documents({})
    
    # System Stats
    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "total_users": total_users,
        "total_files": total_files,
        "cpu": cpu_usage,
        "ram": ram_usage
    })

@router.get("/users")
async def admin_users(request: Request, _: bool = Depends(is_admin)):
    users = await users_col.find().to_list(100)
    return templates.TemplateResponse("admin/users.html", {"request": request, "users": users})

@router.get("/files")
async def admin_files(request: Request, q: str = None, _: bool = Depends(is_admin)):
    query = {}
    if q:
        query = {"filename": {"$regex": __import__("re").escape(q), "$options": "i"}}
    
    files = await files_col.find(query).sort("created_at", -1).to_list(100)
    return templates.TemplateResponse("admin/files.html", {"request": request, "files": files, "query": q})

@router.post("/files/delete/{short_code}")
async def delete_file(request: Request, short_code: str, _: bool = Depends(is_admin)):
        
    await files_col.delete_one({"short_code": short_code})
    return {"status": "success"}
