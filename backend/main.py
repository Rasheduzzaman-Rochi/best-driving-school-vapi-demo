import json
import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Header

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
VAPI_TOOL_SECRET = os.getenv("VAPI_TOOL_SECRET")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

app = FastAPI()


# Monday=0 ... Sunday=6
# Open: Sunday-Thursday
OPEN_DAYS = {6, 0, 1, 2, 3}

SLOTS = [
    ("09:00", "11:00"),
    ("11:00", "13:00"),
    ("13:00", "15:00"),
    ("15:00", "17:00"),
    ("17:00", "19:00"),
]


class AvailabilityRequest(BaseModel):
    booking_date: str
    service_type: Optional[str] = "driving_lesson"


class BookingRequest(BaseModel):
    customer_name: str
    callback_number: str
    service_type: str
    booking_date: str
    start_time: str
    notes: Optional[str] = None


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "Best Driving School Booking API is running"
    }


def parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date must use YYYY-MM-DD format"
        )


def format_clock(value: str):
    dt = datetime.strptime(value, "%H:%M")
    return dt.strftime("%-I:%M %p")


def format_slot(start: str, end: str):
    return f"{format_clock(start)} to {format_clock(end)}"


@app.post("/api/availability")
def check_availability(payload: AvailabilityRequest):

    requested_date = parse_date(payload.booking_date)

    # Friday / Saturday closed
    if requested_date.weekday() not in OPEN_DAYS:
        return {
            "available": False,
            "closed": True,
            "message": "The driving school is closed on Fridays and Saturdays.",
            "available_slots": []
        }

    response = (
        supabase
        .table("bookings")
        .select("start_time")
        .eq("booking_date", payload.booking_date)
        .eq("status", "confirmed")
        .execute()
    )

    booked_times = {
        row["start_time"][:5]
        for row in (response.data or [])
    }

    available_slots = []

    for start, end in SLOTS:
        if start not in booked_times:
            available_slots.append({
                "start_time": start,
                "end_time": end,
                "label": format_slot(start, end)
            })

    return {
        "available": bool(available_slots),
        "closed": False,
        "booking_date": payload.booking_date,
        "available_slots": available_slots
    }


@app.post("/api/bookings")
def create_booking(payload: BookingRequest):

    requested_date = parse_date(payload.booking_date)

    if requested_date.weekday() not in OPEN_DAYS:
        raise HTTPException(
            status_code=400,
            detail="The driving school is closed on Fridays and Saturdays."
        )

    valid_slots = {start: end for start, end in SLOTS}

    if payload.start_time not in valid_slots:
        raise HTTPException(
            status_code=400,
            detail="Invalid booking slot."
        )

    # Check again before saving
    existing = (
        supabase
        .table("bookings")
        .select("id")
        .eq("booking_date", payload.booking_date)
        .eq("start_time", payload.start_time)
        .eq("status", "confirmed")
        .execute()
    )

    if existing.data:
        return {
            "success": False,
            "reason": "slot_already_booked",
            "message": "That time slot is no longer available."
        }

    end_time = valid_slots[payload.start_time]

    try:
        response = (
            supabase
            .table("bookings")
            .insert({
                "customer_name": payload.customer_name,
                "callback_number": payload.callback_number,
                "service_type": payload.service_type,
                "booking_date": payload.booking_date,
                "start_time": payload.start_time,
                "end_time": end_time,
                "status": "confirmed",
                "notes": payload.notes
            })
            .execute()
        )

    except Exception:
        return {
            "success": False,
            "reason": "booking_conflict",
            "message": "That slot could not be booked."
        }

    booking = response.data[0]

    return {
        "success": True,
        "booking_id": booking["id"],
        "customer_name": booking["customer_name"],
        "booking_date": booking["booking_date"],
        "start_time": booking["start_time"],
        "end_time": booking["end_time"],
        "message": (
            f"Booking confirmed for "
            f"{format_slot(payload.start_time, end_time)}."
        )
    }

@app.post("/api/vapi-tools")
def handle_vapi_tools(
    payload: dict,
    authorization: Optional[str] = Header(default=None)
):
    # Protect production endpoint
    if VAPI_TOOL_SECRET:
        expected = f"Bearer {VAPI_TOOL_SECRET}"

        if authorization != expected:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized"
            )

    message = payload.get("message", {})
    tool_calls = message.get("toolCallList", [])

    if not tool_calls:
        raise HTTPException(
            status_code=400,
            detail="No Vapi tool calls found."
        )

    results = []

    for tool_call in tool_calls:

        tool_call_id = tool_call.get("id")
        tool_name = tool_call.get("name")
        arguments = tool_call.get("arguments", {})

        try:

            # ---------------------------
            # CHECK AVAILABLE SLOTS
            # ---------------------------

            if tool_name == "check_available_slots":

                availability_request = AvailabilityRequest(
                    booking_date=arguments["booking_date"],
                    service_type=arguments.get(
                        "service_type",
                        "driving_lesson"
                    )
                )

                result = check_availability(
                    availability_request
                )

            # ---------------------------
            # CREATE BOOKING
            # ---------------------------

            elif tool_name == "create_booking":

                booking_request = BookingRequest(
                    customer_name=arguments["customer_name"],
                    callback_number=arguments["callback_number"],
                    service_type=arguments["service_type"],
                    booking_date=arguments["booking_date"],
                    start_time=arguments["start_time"],
                    notes=arguments.get("notes")
                )

                result = create_booking(
                    booking_request
                )

            else:
                result = {
                    "success": False,
                    "message": f"Unknown tool: {tool_name}"
                }

        except Exception as error:

            result = {
                "success": False,
                "message": str(error)
            }

        results.append({
            "toolCallId": tool_call_id,
            "result": json.dumps(
        result,
        separators=(",", ":")
            )
        })

    return {
        "results": results
    }