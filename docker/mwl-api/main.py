from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
import json
from mwl_handler import create_mwl_file
from dotenv import load_dotenv
from db_utils import insert_mwl_record, get_DB

load_dotenv()

app = FastAPI()

# Create directories if they don't exist
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Add templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

WORKLIST_DIR = os.environ.get("WORKLIST_DIR", "/worklist")

@app.post("/mwl/create_from_json")
async def create_mwl_from_json(request: Request):
    try:
        json_data = await request.json()
        # Generate filename from AccessionNumber or timestamp
        from datetime import datetime
        filename = f"{json_data.get('AccessionNumber', datetime.now().strftime('%Y%m%d%H%M%S'))}.wl"
        output_path = os.path.join(WORKLIST_DIR, filename)
        ds = create_mwl_file(json_data, output_path)
        row_id = insert_mwl_record(json_data, ds)
        return JSONResponse({
            "status": "success",
            "message": f"MWL file created: {filename}",
            "path": output_path,
            "db_row_id": row_id
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page"""
    try:
        conn = get_DB()
        cursor = conn.cursor(dictionary=True)
        
        # Get basic stats
        cursor.execute("SELECT COUNT(*) as total FROM mwl")
        total_mwl = cursor.fetchone()['total']
        
        cursor.execute("SELECT COUNT(*) as completed FROM mwl WHERE completed = 1")
        completed_mwl = cursor.fetchone()['completed']
        
        cursor.execute("SELECT COUNT(*) as pending FROM mwl WHERE completed = 0")
        pending_mwl = cursor.fetchone()['pending']
        
        # Check if MPPS table exists
        cursor.execute("SHOW TABLES LIKE 'mpps'")
        has_mpps = cursor.fetchone() is not None
        
        mpps_stats = {"total": 0, "in_progress": 0, "completed": 0}
        if has_mpps:
            cursor.execute("SELECT COUNT(*) as total FROM mpps")
            mpps_stats['total'] = cursor.fetchone()['total']
            
            cursor.execute("SELECT COUNT(*) as in_progress FROM mpps WHERE status = 'IN_PROGRESS'")
            mpps_stats['in_progress'] = cursor.fetchone()['in_progress']
            
            cursor.execute("SELECT COUNT(*) as completed FROM mpps WHERE status = 'COMPLETED'")
            mpps_stats['completed'] = cursor.fetchone()['completed']
        
        cursor.close()
        conn.close()
        
        stats = {
            "mwl": {"total": total_mwl, "completed": completed_mwl, "pending": pending_mwl},
            "mpps": mpps_stats,
            "has_mpps": has_mpps
        }
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request, 
            "stats": stats
        })
        
    except Exception as e:
        return HTMLResponse(f"<h1>Error: {str(e)}</h1>", status_code=500)

@app.get("/mwl", response_class=HTMLResponse)
async def mwl_list(request: Request):
    """List all MWL entries"""
    try:
        conn = get_DB()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT id, AccessionNumber, PatientName, PatientID, 
                   ScheduledProcedureStepStartDate, ScheduledStationAETitle,
                   completed, created_at
            FROM mwl 
            ORDER BY created_at DESC 
            LIMIT 50
        """)
        mwl_entries = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return templates.TemplateResponse("mwl_list.html", {
            "request": request,
            "mwl_entries": mwl_entries
        })
        
    except Exception as e:
        return HTMLResponse(f"<h1>Error: {str(e)}</h1>", status_code=500)

@app.get("/mpps", response_class=HTMLResponse)  
async def mpps_list(request: Request):
    """List all MPPS entries"""
    try:
        conn = get_DB()
        cursor = conn.cursor(dictionary=True)
        
        # Check if MPPS table exists
        cursor.execute("SHOW TABLES LIKE 'mpps'")
        if not cursor.fetchone():
            return HTMLResponse("<h1>MPPS table not found</h1>", status_code=404)
        
        cursor.execute("""
            SELECT id, AccessionNumber, PatientID, status,
                   performed_station_ae_title, started_at, completed_at
            FROM mpps 
            ORDER BY started_at DESC 
            LIMIT 50
        """)
        mpps_entries = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return templates.TemplateResponse("mpps_list.html", {
            "request": request,
            "mpps_entries": mpps_entries
        })
        
    except Exception as e:
        return HTMLResponse(f"<h1>Error: {str(e)}</h1>", status_code=500)