"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal, List
from datetime import datetime


class Submission(BaseModel):
    """
    Project submissions from authors
    Collection name: "submission"
    """
    name: str = Field(..., description="Author full name")
    email: EmailStr = Field(..., description="Author email")
    novel_title: str = Field(..., description="Title of the novel")
    synopsis: str = Field(..., description="Short synopsis")
    message: Optional[str] = Field(None, description="Additional message")
    file_url: Optional[str] = Field(None, description="Location of uploaded PDF (S3 or storage)")
    file_key: Optional[str] = Field(None, description="Storage key or path for the PDF")
    status: Literal['Pending', 'In Review', 'Approved', 'Completed'] = Field('Pending')
    notes: Optional[List[str]] = Field(default_factory=list, description="Internal admin notes")
    submitted_at: Optional[datetime] = None


class AdminLogin(BaseModel):
    email: EmailStr
    password: str


class UpdateSubmission(BaseModel):
    status: Optional[Literal['Pending', 'In Review', 'Approved', 'Completed']] = None
    add_note: Optional[str] = None


# Example schemas kept for reference (not used directly)
class User(BaseModel):
    name: str
    email: str
    address: str
    age: Optional[int] = None
    is_active: bool = True

class Product(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    category: str
    in_stock: bool = True
