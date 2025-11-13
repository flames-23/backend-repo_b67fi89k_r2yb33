import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import db, create_document, get_documents
from schemas import Submission, AdminLogin, UpdateSubmission

# JWT settings
SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key-change")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12

# Use pbkdf2_sha256 to avoid bcrypt backend issues in some environments
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/login")

# Simple in-memory admin for demo; could be moved to DB if needed
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@arstudios.com")
ADMIN_HASHED_PASSWORD = pwd_context.hash(os.getenv("ADMIN_PASSWORD", "admin1234"))

app = FastAPI(title="AR Studios API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Token(BaseModel):
    access_token: str
    token_type: str


# Auth helpers

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def authenticate_admin(email: str, password: str):
    if email.lower() == ADMIN_EMAIL.lower() and verify_password(password, ADMIN_HASHED_PASSWORD):
        return {"email": ADMIN_EMAIL}
    return None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_admin(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None or email.lower() != ADMIN_EMAIL.lower():
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    return {"email": ADMIN_EMAIL}


@app.get("/")
def read_root():
    return {"message": "AR Studios API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = getattr(db, 'name', '✅ Connected')
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# Collaborate submission endpoint (multipart form with file)
@app.post("/api/submit", response_model=dict)
async def submit_project(
    name: str = Form(...),
    email: str = Form(...),
    novel_title: str = Form(...),
    synopsis: str = Form(...),
    message: Optional[str] = Form(None),
    file: UploadFile = File(None)
):
    file_url = None
    file_key = None

    # In this environment, we'll store file bytes in DB's GridFS-like as base64 or skip storage.
    # For production, integrate S3; here we just keep metadata and reject overly large files.
    if file is not None:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF uploads are allowed")
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 10MB in demo)")
        # Save in database as a separate collection to simulate storage
        from base64 import b64encode
        encoded = b64encode(content).decode('utf-8')
        file_key = create_document('submission_files', {
            'filename': file.filename,
            'content_b64': encoded,
            'mime': file.content_type or 'application/pdf'
        })
        file_url = f"/api/files/{file_key}"

    data = Submission(
        name=name,
        email=email,
        novel_title=novel_title,
        synopsis=synopsis,
        message=message,
        file_url=file_url,
        file_key=file_key,
        submitted_at=datetime.now(timezone.utc)
    )

    inserted_id = create_document('submission', data)

    # Email notification placeholder (logged to DB in this environment)
    create_document('notifications', {
        'type': 'new_submission',
        'to': os.getenv('STUDIO_EMAIL', 'studio@arstudios.com'),
        'subject': f"New Submission: {novel_title}",
        'payload': data.model_dump(),
        'created_at': datetime.now(timezone.utc)
    })

    return {"success": True, "id": inserted_id}


# Serve stored PDF file
@app.get("/api/files/{file_id}")
async def get_file(file_id: str):
    from bson import ObjectId
    doc = db['submission_files'].find_one({"_id": ObjectId(file_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="File not found")
    from base64 import b64decode
    content = b64decode(doc['content_b64'])
    return StreamingResponse(iter([content]), media_type=doc.get('mime', 'application/pdf'))


# Admin authentication
@app.post("/api/admin/login", response_model=Token)
async def admin_login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_admin(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token({"sub": user["email"]})
    return {"access_token": access_token, "token_type": "bearer"}


# Admin list submissions with filters and pagination
@app.get("/api/admin/submissions")
async def list_submissions(
    page: int = 1,
    page_size: int = 10,
    q: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    query = {}
    if status:
        query['status'] = status
    if q:
        # Basic regex search across fields
        query['$or'] = [
            {'name': {'$regex': q, '$options': 'i'}},
            {'email': {'$regex': q, '$options': 'i'}},
            {'novel_title': {'$regex': q, '$options': 'i'}},
        ]
    if start_date or end_date:
        dt_query = {}
        if start_date:
            dt_query['$gte'] = datetime.fromisoformat(start_date)
        if end_date:
            dt_query['$lte'] = datetime.fromisoformat(end_date)
        if dt_query:
            query['submitted_at'] = dt_query

    total = db['submission'].count_documents(query)
    cursor = db['submission'].find(query).sort('submitted_at', -1).skip((page-1)*page_size).limit(page_size)
    items = []
    for it in cursor:
        it['id'] = str(it.pop('_id'))
        items.append(it)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# Get single submission
@app.get("/api/admin/submissions/{submission_id}")
async def get_submission(submission_id: str, admin=Depends(get_current_admin)):
    from bson import ObjectId
    doc = db['submission'].find_one({"_id": ObjectId(submission_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    doc['id'] = str(doc.pop('_id'))
    return doc


# Update submission: status, add note, mark reviewed
@app.patch("/api/admin/submissions/{submission_id}")
async def update_submission(submission_id: str, payload: UpdateSubmission, admin=Depends(get_current_admin)):
    from bson import ObjectId
    updates = {"updated_at": datetime.now(timezone.utc)}
    if payload.status:
        updates['status'] = payload.status
    if payload.add_note:
        db['submission'].update_one({"_id": ObjectId(submission_id)}, {"$push": {"notes": payload.add_note}})
    db['submission'].update_one({"_id": ObjectId(submission_id)}, {"$set": updates})
    return {"success": True}


# Delete submission
@app.delete("/api/admin/submissions/{submission_id}")
async def delete_submission(submission_id: str, admin=Depends(get_current_admin)):
    from bson import ObjectId
    db['submission'].delete_one({"_id": ObjectId(submission_id)})
    return {"success": True}


# Download PDF route (alias)
@app.get("/api/admin/submissions/{submission_id}/download")
async def download_submission_pdf(submission_id: str, admin=Depends(get_current_admin)):
    from bson import ObjectId
    doc = db['submission'].find_one({"_id": ObjectId(submission_id)})
    if not doc or not doc.get('file_key'):
        raise HTTPException(status_code=404, detail="File not found")
    return await get_file(doc['file_key'])


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
