import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from supabase import Client, create_client


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
VAPI_TOOL_SECRET = os.getenv("VAPI_TOOL_SECRET")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is missing.")

if not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_SECRET_KEY is missing.")


# =========================================================
# SUPABASE CLIENT
# =========================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(
    title="Best Driving School Booking API",
    version="1.0.0"
)


# =========================================================
# BUSINESS RULES
# =========================================================

# Python weekday values:
#
# Monday    = 0
# Tuesday   = 1
# Wednesday = 2
# Thursday  = 3
# Friday    = 4
# Saturday  = 5
# Sunday    = 6
#
# Best Driving School:
# Sunday - Thursday = Open
# Friday + Saturday = Closed

OPEN_DAYS = {6, 0, 1, 2, 3}


# Five fixed 2-hour driving lesson slots
SLOTS = [
    ("09:00", "11:00"),
    ("11:00", "13:00"),
    ("13:00", "15:00"),
    ("15:00", "17:00"),
    ("17:00", "19:00"),
]


# =========================================================
# PYDANTIC REQUEST MODELS
# =========================================================

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


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "Best Driving School Booking API is running"
    }


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def parse_date(value: str):
    """
    Convert YYYY-MM-DD string into Python date object.
    """

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date must use YYYY-MM-DD format."
        )


def is_open_day(booking_date):
    """
    Returns True if date falls Sunday-Thursday.
    """

    return booking_date.weekday() in OPEN_DAYS


def format_clock(value: str):
    """
    Converts 24-hour database time into human-friendly time.

    Examples:
    09:00    -> 9:00 AM
    13:00    -> 1:00 PM
    09:00:00 -> 9:00 AM
    """

    clean_value = value[:5]

    parsed_time = datetime.strptime(
        clean_value,
        "%H:%M"
    )

    return parsed_time.strftime("%-I:%M %p")


def format_slot(start: str, end: str):
    """
    Example:
    09:00 + 11:00
    -> 9:00 AM to 11:00 AM
    """

    return (
        f"{format_clock(start)} "
        f"to {format_clock(end)}"
    )


# =========================================================
# AVAILABILITY ENDPOINT
# =========================================================

@app.post("/api/availability")
def check_availability(
    payload: AvailabilityRequest
):

    requested_date = parse_date(
        payload.booking_date
    )

    # -----------------------------------------------------
    # CLOSED DAY CHECK
    # -----------------------------------------------------

    if not is_open_day(requested_date):

        return {
            "available": False,
            "closed": True,
            "booking_date": payload.booking_date,
            "service_type": payload.service_type,
            "message": (
                "The driving school is closed "
                "on Fridays and Saturdays."
            ),
            "available_slots": []
        }

    # -----------------------------------------------------
    # GET CONFIRMED BOOKINGS FOR THIS DATE
    # -----------------------------------------------------

    response = (
        supabase
        .table("bookings")
        .select("start_time")
        .eq(
            "booking_date",
            payload.booking_date
        )
        .eq(
            "status",
            "confirmed"
        )
        .execute()
    )

    booked_times = {
        row["start_time"][:5]
        for row in (response.data or [])
    }

    # -----------------------------------------------------
    # CALCULATE AVAILABLE SLOTS
    # -----------------------------------------------------

    available_slots = []

    for start, end in SLOTS:

        if start not in booked_times:

            available_slots.append({
                "start_time": start,
                "end_time": end,
                "label": format_slot(
                    start,
                    end
                )
            })

    # -----------------------------------------------------
    # FINAL AVAILABILITY RESPONSE
    # -----------------------------------------------------

    return {
        "available": bool(available_slots),
        "closed": False,
        "booking_date": payload.booking_date,
        "service_type": payload.service_type,
        "available_slots": available_slots
    }


# =========================================================
# CREATE BOOKING ENDPOINT
# =========================================================

@app.post("/api/bookings")
def create_booking(
    payload: BookingRequest
):

    requested_date = parse_date(
        payload.booking_date
    )

    # -----------------------------------------------------
    # CLOSED DAY CHECK
    # -----------------------------------------------------

    if not is_open_day(requested_date):

        raise HTTPException(
            status_code=400,
            detail=(
                "The driving school is closed "
                "on Fridays and Saturdays."
            )
        )

    # -----------------------------------------------------
    # VALID SLOT CHECK
    # -----------------------------------------------------

    valid_slots = {
        start: end
        for start, end in SLOTS
    }

    if payload.start_time not in valid_slots:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid booking slot. "
                "Valid start times are "
                "09:00, 11:00, 13:00, "
                "15:00 and 17:00."
            )
        )

    # -----------------------------------------------------
    # CHECK IF SLOT IS ALREADY BOOKED
    # -----------------------------------------------------

    existing = (
        supabase
        .table("bookings")
        .select("id")
        .eq(
            "booking_date",
            payload.booking_date
        )
        .eq(
            "start_time",
            payload.start_time
        )
        .eq(
            "status",
            "confirmed"
        )
        .execute()
    )

    if existing.data:

        updated_availability = check_availability(
            AvailabilityRequest(
                booking_date=payload.booking_date,
                service_type=payload.service_type
            )
        )

        return {
            "success": False,
            "reason": "slot_already_booked",
            "message": (
                "That time slot is no longer available."
            ),
            "available_slots": updated_availability.get(
                "available_slots",
                []
            )
        }

    # -----------------------------------------------------
    # DETERMINE END TIME
    # -----------------------------------------------------

    end_time = valid_slots[
        payload.start_time
    ]

    # -----------------------------------------------------
    # CREATE DATABASE BOOKING
    # -----------------------------------------------------

    try:

        response = (
            supabase
            .table("bookings")
            .insert({
                "customer_name":
                    payload.customer_name,

                "callback_number":
                    payload.callback_number,

                "service_type":
                    payload.service_type,

                "booking_date":
                    payload.booking_date,

                "start_time":
                    payload.start_time,

                "end_time":
                    end_time,

                "status":
                    "confirmed",

                "notes":
                    payload.notes
            })
            .execute()
        )

    except Exception:

        # Could happen if another caller booked
        # the same slot at almost the same moment.

        updated_availability = check_availability(
            AvailabilityRequest(
                booking_date=payload.booking_date,
                service_type=payload.service_type
            )
        )

        return {
            "success": False,
            "reason": "booking_conflict",
            "message": (
                "That slot could not be booked "
                "because it may have just become unavailable."
            ),
            "available_slots": updated_availability.get(
                "available_slots",
                []
            )
        }

    # -----------------------------------------------------
    # MAKE SURE INSERT WORKED
    # -----------------------------------------------------

    if not response.data:

        raise HTTPException(
            status_code=500,
            detail="Booking could not be created."
        )

    booking = response.data[0]

    confirmed_time = format_slot(
        booking["start_time"],
        booking["end_time"]
    )

    # -----------------------------------------------------
    # SUCCESS RESPONSE
    # -----------------------------------------------------

    return {
        "success": True,
        "booking_id": booking["id"],
        "customer_name": booking["customer_name"],
        "callback_number": booking["callback_number"],
        "service_type": booking["service_type"],
        "booking_date": booking["booking_date"],
        "start_time": booking["start_time"],
        "end_time": booking["end_time"],
        "status": booking["status"],
        "message": (
            f"Booking confirmed for {confirmed_time}."
        )
    }


# =========================================================
# VAPI CUSTOM TOOL ENDPOINT
# =========================================================

@app.post("/api/vapi-tools")
def handle_vapi_tools(
    payload: dict,
    authorization: Optional[str] = Header(
        default=None
    )
):

    # -----------------------------------------------------
    # VERIFY VAPI BEARER TOKEN
    # -----------------------------------------------------

    if VAPI_TOOL_SECRET:

        expected_authorization = (
            f"Bearer {VAPI_TOOL_SECRET}"
        )

        if authorization != expected_authorization:

            raise HTTPException(
                status_code=401,
                detail="Unauthorized"
            )

    # -----------------------------------------------------
    # READ VAPI REQUEST
    # -----------------------------------------------------

    message = payload.get(
        "message",
        {}
    )

    tool_calls = message.get(
        "toolCallList",
        []
    )

    if not tool_calls:

        raise HTTPException(
            status_code=400,
            detail="No Vapi tool calls found."
        )

    results = []

    # -----------------------------------------------------
    # PROCESS VAPI TOOL CALLS
    # -----------------------------------------------------

    for tool_call in tool_calls:

        tool_call_id = tool_call.get(
            "id"
        )

        tool_name = tool_call.get(
            "name"
        )

        arguments = tool_call.get(
            "arguments",
            {}
        )

        try:

            # =================================================
            # TOOL 1: CHECK AVAILABLE SLOTS
            # =================================================

            if tool_name == "check_available_slots":

                availability_request = (
                    AvailabilityRequest(
                        booking_date=arguments[
                            "booking_date"
                        ],
                        service_type=arguments.get(
                            "service_type",
                            "driving_lesson"
                        )
                    )
                )

                result = check_availability(
                    availability_request
                )

                # ---------------------------------------------
                # CLOSED DAY
                # ---------------------------------------------

                if result.get("closed"):

                    booking_date = result.get(
                        "booking_date"
                    )

                    vapi_result = (
                        "The driving school is closed "
                        f"on {booking_date}. "
                        "The school is closed on Fridays "
                        "and Saturdays. "
                        "Ask the caller to choose a date "
                        "from Sunday through Thursday."
                    )

                # ---------------------------------------------
                # NO AVAILABLE SLOT
                # ---------------------------------------------

                elif not result.get("available"):

                    booking_date = result.get(
                        "booking_date"
                    )

                    vapi_result = (
                        "There are no available driving "
                        "lesson slots for "
                        f"{booking_date}. "
                        "Tell the caller there are no "
                        "open slots on that date and "
                        "ask them for another date. "
                        "Do not invent availability."
                    )

                # ---------------------------------------------
                # AVAILABLE SLOTS FOUND
                # ---------------------------------------------

                else:

                    slot_labels = [
                        slot["label"]
                        for slot
                        in result.get(
                            "available_slots",
                            []
                        )
                    ]

                    slots_text = ", ".join(
                        slot_labels
                    )

                    booking_date = result.get(
                        "booking_date"
                    )

                    vapi_result = (
                        "Available driving lesson slots "
                        f"for {booking_date}: "
                        f"{slots_text}. "
                        "Tell the caller these exact "
                        "available times now. "
                        "Ask which one they would like "
                        "to book. "
                        "Do not offer any time that is "
                        "not listed here."
                    )

            # =================================================
            # TOOL 2: CREATE BOOKING
            # =================================================

            elif tool_name == "create_booking":

                booking_request = BookingRequest(
                    customer_name=arguments[
                        "customer_name"
                    ],

                    callback_number=arguments[
                        "callback_number"
                    ],

                    service_type=arguments[
                        "service_type"
                    ],

                    booking_date=arguments[
                        "booking_date"
                    ],

                    start_time=arguments[
                        "start_time"
                    ],

                    notes=arguments.get(
                        "notes"
                    )
                )

                result = create_booking(
                    booking_request
                )

                # ---------------------------------------------
                # BOOKING SUCCESS
                # ---------------------------------------------

                if result.get("success"):

                    confirmed_time = format_slot(
                        result.get(
                            "start_time"
                        ),
                        result.get(
                            "end_time"
                        )
                    )

                    customer_name = result.get(
                        "customer_name"
                    )

                    service_type = result.get(
                        "service_type"
                    )

                    booking_date = result.get(
                        "booking_date"
                    )

                    vapi_result = (
                        "Booking successful. "
                        f"Customer: {customer_name}. "
                        f"Service: {service_type}. "
                        f"Date: {booking_date}. "
                        f"Time: {confirmed_time}. "
                        "The booking has been confirmed "
                        "and saved in the database. "
                        "Tell the caller their booking "
                        "has been successfully confirmed "
                        "for this exact date and time."
                    )

                # ---------------------------------------------
                # BOOKING FAILED
                # ---------------------------------------------

                else:

                    available_slots = result.get(
                        "available_slots",
                        []
                    )

                    # Alternative slots exist
                    if available_slots:

                        slot_labels = [
                            slot["label"]
                            for slot
                            in available_slots
                        ]

                        alternatives = ", ".join(
                            slot_labels
                        )

                        vapi_result = (
                            "The selected booking slot "
                            "is no longer available. "
                            "The currently available "
                            "alternatives are: "
                            f"{alternatives}. "
                            "Apologize briefly and ask "
                            "the caller to select one "
                            "of these available times."
                        )

                    # No alternatives returned
                    else:

                        error_message = result.get(
                            "message",
                            "Unknown booking error"
                        )

                        vapi_result = (
                            "The booking was not completed. "
                            f"Reason: {error_message}. "
                            "Do not tell the caller that "
                            "their booking is confirmed."
                        )

            # =================================================
            # UNKNOWN TOOL
            # =================================================

            else:

                vapi_result = (
                    f"Unknown tool: {tool_name}. "
                    "The requested booking operation "
                    "could not be completed."
                )

        # =====================================================
        # TOOL PROCESSING ERROR
        # =====================================================

        except Exception as error:

            vapi_result = (
                "The booking system encountered an error. "
                f"Error details: {str(error)}. "
                "Do not claim that availability was "
                "successfully checked and do not claim "
                "that any booking was confirmed."
            )

        # =====================================================
        # VAPI EXPECTS RESULT AS STRING
        # =====================================================

        results.append({
            "toolCallId": tool_call_id,
            "result": vapi_result
        })

    # =========================================================
    # FINAL VAPI RESPONSE
    # =========================================================

    return {
        "results": results
    }