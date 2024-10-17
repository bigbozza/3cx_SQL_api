# main.py
from fastapi import FastAPI, HTTPException, Query, Security, Depends, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from .db import get_db
from sqlalchemy import text
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import logging
import os
from typing import Optional
import secrets

app = FastAPI()

# Configure logging based on LOG_LEVEL environment variable
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["100/hour"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Initialize HTTP Basic Authentication
security = HTTPBasic()

# Retrieve the API key and password from environment variables
API_KEY = os.getenv("FASTAPI_API_KEY", "your_default_api_key").strip()
API_PASSWORD = os.getenv("FASTAPI_API_PASSWORD", "X").strip()

# Middleware to log Authorization header in DEBUG mode
@app.middleware("http")
async def log_authorization_header(request: Request, call_next):
    if LOG_LEVEL == "DEBUG":
        auth_header = request.headers.get("Authorization", "")
        logger.debug(f"Authorization Header: {auth_header}")
    response = await call_next(request)
    return response

async def get_current_credentials(credentials: HTTPBasicCredentials = Security(security)):
    """
    Validates the incoming HTTP Basic credentials.
    The username should match the API key, and the password should match the expected value.
    """
    if LOG_LEVEL == "DEBUG":
        logger.debug(f"Received credentials - Username: {credentials.username}, Password: {credentials.password}")

    correct_username = secrets.compare_digest(credentials.username, API_KEY)
    correct_password = secrets.compare_digest(credentials.password, API_PASSWORD)
    if not (correct_username and correct_password):
        logger.warning("Invalid API Key or Password")
        raise HTTPException(
            status_code=403,
            detail="Could not validate credentials"
        )
    if LOG_LEVEL == "DEBUG":
        logger.debug("Authentication successful")
    return credentials.username  # or any other identifier if needed

@app.get("/contacts")
@limiter.limit("20/minute")  # Limit to 20 requests per minute per IP
async def lookup_contact(
    request: Request,
    Number: Optional[str] = Query(None, alias='Number'),
    Email: Optional[str] = Query(None, alias='Email'),
    api_key: str = Security(get_current_credentials),
    db: Session = Depends(get_db)
):
    if LOG_LEVEL == "DEBUG":
        logger.debug(f"Incoming request from {request.client.host}")
        logger.debug(f"Query Parameters - Number: {Number}, Email: {Email}")

    if not Number and not Email:
        logger.debug("Missing query parameters: Number or Email must be provided")
        raise HTTPException(status_code=400, detail="Either Number or Email must be provided")

    query = text("""
        SELECT 
            id AS contactid,
            RealName AS firstname,
            RealName AS lastname,  -- Assuming RealName contains the full name
            WorkPhone AS PhoneBusiness,
            MobilePhone AS PhoneMobile,
            HomePhone AS PhoneHome,
            EmailAddress AS Email,
            Organization AS company
        FROM Users
        WHERE 
            (:number IS NOT NULL AND (
                REPLACE(HomePhone, ' ', '') LIKE :like_pattern 
                OR REPLACE(WorkPhone, ' ', '') LIKE :like_pattern 
                OR REPLACE(MobilePhone, ' ', '') LIKE :like_pattern
            ))
            OR
            (:email IS NOT NULL AND EmailAddress = :exact_email)
    """)

    parameters = {}
    if Number:
        # Clean up the input number (remove spaces)
        search_number = Number.replace(' ', '')
        parameters['like_pattern'] = f"%{search_number}%"
        parameters['number'] = Number
    else:
        parameters['like_pattern'] = None
        parameters['number'] = None

    if Email:
        parameters['exact_email'] = Email
        parameters['email'] = Email
    else:
        parameters['exact_email'] = None
        parameters['email'] = None

    if LOG_LEVEL == "DEBUG":
        logger.debug(f"Executing SQL with parameters: {parameters}")

    try:
        result = db.execute(query, parameters)
        results = result.fetchall()
        if LOG_LEVEL == "DEBUG":
            logger.debug(f"Database returned {len(results)} results")
    except Exception as e:
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail="Database error occurred.")

    if not results:
        logger.debug("No contacts found for the given query parameters")
        raise HTTPException(status_code=404, detail="Contact not found")

    # Format the response as per 3CX requirements
    response = {
        "contacts": []
    }

    for contact in results:
        # Optionally, split RealName into first and last names if possible
        real_name = contact.firstname or ""
        name_parts = real_name.split(' ')
        first_name = name_parts[0] if name_parts else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        contact_data = {
            "contactid": contact.contactid,
            "firstname": first_name,
            "lastname": last_name,
            "company": contact.company if contact.company else "",
            "email": contact.Email,
            "phonebusiness": contact.PhoneBusiness if contact.PhoneBusiness else "",
            "phonemobile": contact.PhoneMobile if contact.PhoneMobile else "",
            "phonehome": contact.PhoneHome if contact.PhoneHome else "",
            # Add more fields if needed
        }
        if LOG_LEVEL == "DEBUG":
            logger.debug(f"Appending contact: {contact_data}")
        response["contacts"].append(contact_data)

    if LOG_LEVEL == "DEBUG":
        logger.debug(f"Response: {response}")

    return response
