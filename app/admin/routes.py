from fastapi import APIRouter, Request, HTTPException, Depends, Header, Query
from fastapi.templating import Jinja2Templates
from app.database.connection import files_col, users_col, logs_col, settings
import psutil
import time
from typing import Optional

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")

def require_admin(
    request: Request,
    token: Optional[str] = Query(None, alias="token"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
):
    """
    Admin is locked when ADMIN_TOKEN / EL_TOKEN is empty (not publicly exposed).
    When set, require matching ?token= or X-Admin-Token header.
    """
    expected = settings.effective_admin_token
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Admin panel disabled (ADMIN_TOKEN / EL_TOKEN not configured).",
        )
    provided = (token or x_admin_token or "").strip()
    if provided != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token.")
    return True

@router.get("/")
async def admin_dashboard(request: Request, _=Depends(require_admin)):
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
async def admin_users(request: Request, _=Depends(require_admin)):
    users = await users_col.find().to_list(100)
    return templates.TemplateResponse("admin/users.html", {"request": request, "users": users})

@router.get("/files")
async def admin_files(request: Request, q: str = None, _=Depends(require_admin)):
    query = {}
    if q:
        query = {"filename": {"$regex": q, "$options": "i"}}
    
    files = await files_col.find(query).sort("created_at", -1).to_list(100)
    return templates.TemplateResponse("admin/files.html", {"request": request, "files": files, "query": q})

@router.post("/files/delete/{short_code}")
async def delete_file(request: Request, short_code: str, _=Depends(require_admin)):
    await files_col.delete_one({"short_code": short_code})
    return {"status": "success"}
