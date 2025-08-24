import math
import os
import jwt
import datetime
import json
import numpy as np
from io import BytesIO
from fastapi import FastAPI, HTTPException, Depends, File, UploadFile, Query
from pydantic import BaseModel
from pymongo.common import alias
from starlette.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from dotenv import load_dotenv
from google.auth.transport import requests
from google.oauth2 import id_token
import pandas as pd
from bson import ObjectId
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from typing import Dict
import uvicorn

load_dotenv()

app = FastAPI()

# CORS setup to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Update this for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = MongoClient(os.getenv("MONGO_URI"))
db = client["asset_management_system"]
users_collection = db["users"]
print("Database connected successfully")

SECRET_KEY = os.getenv("SECRET_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class TokenRequest(BaseModel):
    token: str
@app.get("/")
async def asset_management_system():
    return {"message": "Asset Management System Backend"}

@app.post("/auth/google")
async def auth_google(data: TokenRequest):
    try:
        user_info = id_token.verify_oauth2_token(
            data.token, requests.Request(), GOOGLE_CLIENT_ID
        )
        if not user_info["email"].endswith(("@drishticps.org", "@iiti.ac.in")):
            raise HTTPException(status_code=403, detail="Unauthorized email domain")

        existing_user = users_collection.find_one({"email": user_info["email"]})
        if not existing_user:
            new_user = {
                "name": user_info["name"],
                "email": user_info["email"],
                "picture": user_info["picture"],
                "role": "employee",
                "created_at": datetime.datetime.utcnow(),
            }
            users_collection.insert_one(new_user)

        payload = {
            "sub": user_info["email"],
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
            "iat": datetime.datetime.utcnow(),
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {"message": "Login successful", "token": token, "user": user_info}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid token")

@app.post("/upload_excel")
async def upload_excel(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        excel_data = pd.ExcelFile(BytesIO(contents))
        for sheet_name in excel_data.sheet_names:
            df = excel_data.parse(sheet_name)
            collection_name = f"{file.filename.split('.')[0]}_{sheet_name}"
            new_collection = db[collection_name]
            data = df.to_dict(orient="records")
            if data:
                new_collection.insert_many(data)
        return {"message": "File uploaded and all sheets stored in separate collections."}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Error processing the Excel file")

# @app.get("/get-summary")
# async def get_summary():
#     try:
#         collections = db.list_collection_names()
#         total_assets = 0
#         available_assets = 0
#         assigned_assets = 0
#         assets_in_maintenance = 0
#         category_summary = {}
#
#         for collection_name in collections:
#             collection = db[collection_name]
#
#             total_assets += collection.count_documents({})
#             available_assets += collection.count_documents({"status": "available"})
#             assigned_assets += collection.count_documents({"status": "assigned"})
#             assets_in_maintenance += collection.count_documents({"status": "maintenance"})
#
#             # Count assigned assets by category
#             pipeline = [
#                 {"$match": {"status": "assigned"}},
#                 {"$group": {"_id": "$category", "count": {"$sum": 1}}}
#             ]
#             category_data = list(collection.aggregate(pipeline))
#
#             for data in category_data:
#                 category = data["_id"]
#                 count = data["count"]
#                 category_summary[category] = category_summary.get(category, 0) + count
#
#         return {
#             "total_assets": total_assets,
#             "available_assets": available_assets,
#             "assigned_assets": assigned_assets,
#             "assets_in_maintenance": assets_in_maintenance,
#             "category_summary": category_summary
#         }
#     except Exception as e:
#         return {"error": str(e)}
inventory_collections = [
    "Inventory_DRISHTI_Any other Non - Consumable Item",
    "Inventory_DRISHTI_Furniture",
    "Inventory_DRISHTI_Laptop",
    "Inventory_DRISHTI_Mouse+Keyboard",
    "Inventory_DRISHTI_Others",
]

import math

@app.get("/get-summary")
async def get_summary():
    try:
        summary = {
            "total_assets": 0,
            "available_assets": 0,
            "assigned_assets": 0,
            "assets_in_maintenance": 0,
            "retired_assets": 0,
            "category_summary": {},
            "total_spent_summary": {},
        }

        for collection_name in inventory_collections:
            collection = db[collection_name]

            # Count assets based on "Issued to"
            assigned_count = collection.count_documents({"Issued to": {"$ne": ""}})  # If assigned, "Issued to" is not empty
            available_count = collection.count_documents({"Issued to": ""})  # Available if "Issued to" is empty

            # Assuming "Under Maintenance" and "Retired" have dedicated fields or separate logic
            maintenance_count = collection.count_documents({"status": "Under Maintenance"})  # Adjust based on your DB
            retired_count = collection.count_documents({"status": "Retired"})

            total_assets = available_count + assigned_count + maintenance_count + retired_count

            # Aggregate total spending per category
            total_spent_cursor = collection.aggregate([{
                "$group": {"_id": None, "total_spent": {"$sum": "$Total Price"}}
            }])
            total_price = next(total_spent_cursor, {}).get("total_spent", 0)

            # ✅ Ensure total_price is a valid number
            if isinstance(total_price, str) or math.isnan(total_price):
                total_price = 0

            # Update summary
            summary["total_assets"] += total_assets
            summary["available_assets"] += available_count
            summary["assigned_assets"] += assigned_count
            summary["assets_in_maintenance"] += maintenance_count
            summary["retired_assets"] += retired_count
            summary["category_summary"][collection_name] = total_assets
            summary["total_spent_summary"][collection_name] = total_price

        return summary

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def clean_data(asset):
    """Convert NaN, None, or Infinity values to a valid format."""
    for key, value in asset.items():
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            asset[key] = 0  # Replace NaN or Inf with 0
        elif value is None:
            asset[key] = "N/A"  # Replace None with "N/A"
    return asset

@app.get("/get-all-assets")
async def get_all_assets(
    page: int = Query(1, alias="page"),
    limit: int = Query(50, alias="limit", le=500),
    collection: str = Query("all", alias="collection")  # Default "all" for all collections
):
    try:
        skip = (page - 1) * limit
        all_collections = [col for col in db.list_collection_names() if "Inventory_DRISHTI_" in col]

        if collection != "all":
            if collection not in all_collections:
                raise HTTPException(status_code=400, detail="Invalid collection name")
            selected_collections = [collection]
        else:
            selected_collections = all_collections

        total_assets = sum(db[col].count_documents({}) for col in selected_collections)
        all_assets = []

        for collection_name in selected_collections:
            col = db[collection_name]
            assets = list(col.find({}, {"_id": 0}).skip(skip).limit(limit))
            cleaned_assets = [clean_data(asset) for asset in assets]  # Convert NaN to valid values
            all_assets.extend(cleaned_assets)

            if len(all_assets) >= limit:
                break  # Stop once limit is reached

        total_pages = max(1, math.ceil(total_assets / limit))

        return {
            "assets": all_assets,
            "total_pages": total_pages,
            "current_page": page,
            "total_assets": total_assets
        }
    except Exception as e:
        print("Error:", str(e))
        raise HTTPException(status_code=500, detail="Error fetching assets")

def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

@app.get("/employee/{email}")
def get_employee(email: str):
    try:
        user = db["users"].find_one({"email": email}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=404, detail="Employee not found")

        employee_name = user["name"]
        assigned_assets = []

        for collection_name in db.list_collection_names():
            collection = db[collection_name]
            assets = list(collection.find({"Issued To": employee_name}, {"_id": 0}))

            # Replace NaN values with None
            for asset in assets:
                for key, value in asset.items():
                    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                        asset[key] = None  # JSON-compliant

            assigned_assets.extend(assets)

        return {"employee": user, "assigned_assets": assigned_assets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ContactMessage(BaseModel):
    name: str
    email: str
    subject: str
    message: str

class AdminReply(BaseModel):
    reply: str

messages_collection = db["messages"]

@app.post("/submit_message")
async def submit_message(data: ContactMessage):
    messages_collection.insert_one({"name": data.name, "email": data.email, "subject": data.subject, "message": data.message, "reply": None, "timestamp": datetime.datetime.utcnow()})
    return {"message": "Message submitted successfully"}

@app.get("/messages")
async def get_messages():
    messages = list(messages_collection.find({}))
    for msg in messages:
        msg["_id"] = str(msg["_id"])
    return messages

@app.post("/reply/{message_id}")
async def reply_to_message(message_id: str, reply_data: AdminReply):
    updated = messages_collection.update_one({"_id": ObjectId(message_id)}, {"$set": {"reply": reply_data.reply}})
    if updated.modified_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"message": "Reply sent successfully"}

@app.get("/messages/{email}")
async def get_messages(email: str):
    messages = list(
        messages_collection.find({"email": email}, {"_id": 1, "subject": 1, "message": 1, "reply": 1, "timestamp": 1}))

    print("Fetched Messages from DB:", messages)  # Debugging

    for msg in messages:
        msg["_id"] = str(msg["_id"])  # Convert ObjectId to string
        print("Formatted Message:", msg)  # Debugging

    return messages

@app.get("/unassigned-assets")
def get_unassigned_assets():
    try:
        unassigned_assets = []

        # Fetch collections that contain "Inventory_DRISHTI" in the name
        for collection_name in db.list_collection_names():
            if "Inventory_DRISHTI" in collection_name:
                collection = db[collection_name]

                # Find assets where "Issued To" is empty, None, or NaN
                assets = list(collection.find(
                    {
                        "$or": [
                            {"Issued To": None},  # Matches None
                            {"Issued To": ""},    # Matches empty string
                            {"Issued To": {"$exists": False}},  # Matches missing field
                        ]
                    },
                    {"_id": 0}  # Exclude MongoDB ID from results
                ))

                # Replace NaN values with None for JSON compliance
                for asset in assets:
                    for key, value in asset.items():
                        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                            asset[key] = None

                unassigned_assets.extend(assets)

        return {"unassigned_assets": unassigned_assets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def clean_nan_values(data):

    if isinstance(data, dict):
        return {k: clean_nan_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_nan_values(item) for item in data]
    elif isinstance(data, (float, int)) and (math.isnan(data) or math.isinf(data)):  # Check for NaN or Infinity
        return None
    return data

@app.get("/employees-with-assets")
def get_employees_with_assets():
    try:
        employees = {}

        # Iterate through all collections in the database
        for collection_name in db.list_collection_names():
            collection = db[collection_name]

            # Find assets where "Issued To" is not None/empty
            assets = collection.find(
                {"Issued To": {"$ne": None, "$ne": ""}},
                {"_id": 0}  # Exclude MongoDB ID
            )

            for asset in assets:
                issued_to = asset.get("Issued To")

                if issued_to:
                    if issued_to not in employees:
                        employees[issued_to] = []

                    employees[issued_to].append(clean_nan_values({
                        "collection": collection_name,
                        "Stock Entry Number": asset.get("Stock Entry Number"),
                        "Issue Date": asset.get("Issue Date"),
                        "Material Name": asset.get("Material Name"),
                        "Remarks": asset.get("Remarks"),
                    }))

        if not employees:
            raise HTTPException(status_code=404, detail="No employees with assigned assets found")

        # Convert to JSON safely before returning
        response_data = json.loads(json.dumps({"employees": employees}, default=str))

        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Allowed Collections
VALID_COLLECTIONS = {
    "Inventory_DRISHTI_Any other Non - Consumable Item",
    "Inventory_DRISHTI_Furniture",
    "Inventory_DRISHTI_Laptop",
    "Inventory_DRISHTI_Mouse+Keyboard",
    "Inventory_DRISHTI_Others"
}

# Pydantic Model for Asset Input
class Asset(BaseModel):
    collection: str
    data: dict  # Asset details (dynamic fields)
    added_by: str  # Admin's email or name

@app.post("/add-asset/")
async def add_asset(asset: Asset):
    if asset.collection not in VALID_COLLECTIONS:
        raise HTTPException(status_code=400, detail="Invalid collection name.")

    # Add timestamp and 'Added By' field
    asset.data["timestamp"] = datetime.datetime.utcnow()
    asset.data["added_by"] = asset.added_by

    # Insert into MongoDB
    collection = db[asset.collection]
    result = collection.insert_one(asset.data)

    return {"message": "Asset added successfully", "inserted_id": str(result.inserted_id)}

# This is the key addition for Render deployment
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

