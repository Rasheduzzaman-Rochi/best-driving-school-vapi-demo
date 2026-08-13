import os
import logging
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

# Use Uvicorn logger so logs appear in Dokploy
logger = logging.getLogger("uvicorn.error")


# =========================================================
# BUSINESS RULES
# =========================================================

# Python weekday:
# Monday    = 0
# Tuesday   = 1
# Wednesday = 2
# Thursday  = 3
# Friday    = 4
# Saturday  = 5
# Sunday    = 6

# School open:
# Sunday - Thursday
# Closed:
# Friday + Saturday

OPEN_DAYS = {6, 0, 1, 2, 3}

# Five fixed 2-hour lesson slots
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
    Convert YYYY-MM-DD into Python date.
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
    True for Sunday-Thursday.
    """

    return booking_date.weekday() in OPEN_DAYS


def format_clock(value: str):
    """
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


def clean_vapi_string(value: str):
    """
    Vapi tool results should be a single-line string.
    This removes accidental newlines / extra whitespace.
    """

    return " ".join(
        str(value).split()
    )


# =========================================================
# AVAILABILITY API
# =========================================================

@app.post("/api/availability")
def check_availability(
    payload: AvailabilityRequest
):

    requested_date = parse_date(
        payload.booking_date
    )

    # -----------------------------------------------------
    # CLOSED DAY
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
    # GET CONFIRMED BOOKINGS
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
    # RESPONSE
    # -----------------------------------------------------

    return {
        "available": bool(available_slots),
        "closed": False,
        "booking_date": payload.booking_date,
        "service_type": payload.service_type,
        "available_slots": available_slots
    }


# =========================================================
# CREATE BOOKING API
# =========================================================

@app.post("/api/bookings")
def create_booking(
    payload: BookingRequest
):

    requested_date = parse_date(
        payload.booking_date
    )

    # -----------------------------------------------------
    # CLOSED DAY
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
    # VALID SLOT
    # -----------------------------------------------------

    valid_slots = {
        start: end
        for start, end in SLOTS
    }

    # Normalize 09:00:00 -> 09:00
    requested_start_time = (
        payload.start_time[:5]
    )

    if requested_start_time not in valid_slots:

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
    # CHECK IF SLOT ALREADY BOOKED
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
            requested_start_time
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
    # END TIME
    # -----------------------------------------------------

    end_time = valid_slots[
        requested_start_time
    ]

    # -----------------------------------------------------
    # INSERT BOOKING
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
                    requested_start_time,

                "end_time":
                    end_time,

                "status":
                    "confirmed",

                "notes":
                    payload.notes
            })
            .execute()
        )

    except Exception as error:

        logger.exception(
            "DATABASE BOOKING ERROR"
        )

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
    # VERIFY INSERT
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
    # SUCCESS
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
    # AUTHENTICATION
    # -----------------------------------------------------

    if VAPI_TOOL_SECRET:

        expected_authorization = (
            f"Bearer {VAPI_TOOL_SECRET}"
        )

        if authorization != expected_authorization:

            logger.warning(
                "VAPI TOOL REQUEST REJECTED: "
                "invalid authorization"
            )

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

        logger.warning(
            "VAPI TOOL REQUEST: "
            "No toolCallList found"
        )

        raise HTTPException(
            status_code=400,
            detail="No Vapi tool calls found."
        )

    logger.info(
        "VAPI TOOL REQUEST RECEIVED | count=%s",
        len(tool_calls)
    )

    results = []

    # -----------------------------------------------------
    # PROCESS EACH TOOL CALL
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

        # Do NOT log customer values / phone numbers.
        logger.info(
            "VAPI TOOL START | "
            "tool=%s | "
            "toolCallId=%s | "
            "argument_keys=%s",
            tool_name,
            tool_call_id,
            list(arguments.keys())
        )

        try:

            # =================================================
            # TOOL 1:
            # CHECK AVAILABLE SLOTS
            # =================================================

            if tool_name == "check_available_slots":

                booking_date_argument = (
                    arguments.get(
                        "booking_date"
                    )
                )

                if not booking_date_argument:

                    raise ValueError(
                        "booking_date is required."
                    )

                availability_request = (
                    AvailabilityRequest(
                        booking_date=(
                            booking_date_argument
                        ),
                        service_type=(
                            arguments.get(
                                "service_type",
                                "driving_lesson"
                            )
                        )
                    )
                )

                availability = check_availability(
                    availability_request
                )

                # ---------------------------------------------
                # CLOSED
                # ---------------------------------------------

                if availability.get("closed"):

                    booking_date = (
                        availability.get(
                            "booking_date"
                        )
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
                # NO SLOTS
                # ---------------------------------------------

                elif not availability.get(
                    "available"
                ):

                    booking_date = (
                        availability.get(
                            "booking_date"
                        )
                    )

                    vapi_result = (
                        "There are no available driving "
                        "lesson slots for "
                        f"{booking_date}. "
                        "Tell the caller there are no "
                        "open lesson times on that date "
                        "and ask for another date. "
                        "Do not invent availability."
                    )

                # ---------------------------------------------
                # SLOTS AVAILABLE
                # ---------------------------------------------

                else:

                    slot_labels = [
                        slot["label"]
                        for slot
                        in availability.get(
                            "available_slots",
                            []
                        )
                    ]

                    slots_text = ", ".join(
                        slot_labels
                    )

                    booking_date = (
                        availability.get(
                            "booking_date"
                        )
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
            # TOOL 2:
            # CREATE BOOKING
            # =================================================

            elif tool_name == "create_booking":

                required_booking_arguments = [
                    "customer_name",
                    "callback_number",
                    "service_type",
                    "booking_date",
                    "start_time",
                ]

                missing_arguments = [
                    key
                    for key in required_booking_arguments
                    if not arguments.get(key)
                ]

                if missing_arguments:

                    raise ValueError(
                        "Missing required booking fields: "
                        + ", ".join(
                            missing_arguments
                        )
                    )

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

                booking_result = create_booking(
                    booking_request
                )

                # ---------------------------------------------
                # BOOKING SUCCESS
                # ---------------------------------------------

                if booking_result.get(
                    "success"
                ):

                    confirmed_time = format_slot(
                        booking_result.get(
                            "start_time"
                        ),
                        booking_result.get(
                            "end_time"
                        )
                    )

                    booking_date = (
                        booking_result.get(
                            "booking_date"
                        )
                    )

                    vapi_result = (
                        "The booking was successfully "
                        "saved in the database. "
                        f"Confirmed date: {booking_date}. "
                        f"Confirmed time: {confirmed_time}. "
                        "Tell the caller their lesson "
                        "has been successfully booked "
                        "for this exact date and time."
                    )

                # ---------------------------------------------
                # BOOKING CONFLICT
                # ---------------------------------------------

                else:

                    available_slots = (
                        booking_result.get(
                            "available_slots",
                            []
                        )
                    )

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
                            "The selected slot could not "
                            "be booked because it is no "
                            "longer available. "
                            "The currently available "
                            f"times are: {alternatives}. "
                            "Apologize briefly and ask "
                            "the caller to choose one of "
                            "these remaining times."
                        )

                    else:

                        error_message = (
                            booking_result.get(
                                "message",
                                "Booking could not be completed."
                            )
                        )

                        vapi_result = (
                            "The booking was not completed. "
                            f"Reason: {error_message} "
                            "Do not tell the caller that "
                            "their booking is confirmed."
                        )

            # =================================================
            # UNKNOWN TOOL
            # =================================================

            else:

                error_text = (
                    f"Unknown tool: {tool_name}"
                )

                logger.warning(
                    "VAPI UNKNOWN TOOL | "
                    "tool=%s | toolCallId=%s",
                    tool_name,
                    tool_call_id
                )

                results.append({
                    "toolCallId": tool_call_id,
                    "error": clean_vapi_string(
                        error_text
                    )
                })

                continue

            # =================================================
            # IMPORTANT:
            # VAPI RESULT MUST BE SINGLE-LINE STRING
            # =================================================

            vapi_result = clean_vapi_string(
                vapi_result
            )

            results.append({
                "toolCallId": tool_call_id,
                "result": vapi_result
            })

            # For debugging availability result.
            # Does not expose phone number.
            if tool_name == "check_available_slots":

                logger.info(
                    "VAPI TOOL RESULT | "
                    "tool=%s | "
                    "toolCallId=%s | "
                    "result=%s",
                    tool_name,
                    tool_call_id,
                    vapi_result
                )

            else:

                logger.info(
                    "VAPI TOOL RESULT | "
                    "tool=%s | "
                    "toolCallId=%s | "
                    "completed=true",
                    tool_name,
                    tool_call_id
                )

        # =====================================================
        # TOOL ERROR
        # =====================================================

        except Exception as error:

            logger.exception(
                "VAPI TOOL ERROR | "
                "tool=%s | "
                "toolCallId=%s",
                tool_name,
                tool_call_id
            )

            error_message = clean_vapi_string(
                "The booking system could not complete "
                "this request. Please do not claim that "
                "availability was checked or that a booking "
                "was confirmed."
            )

            # Vapi supports an error string in results.
            results.append({
                "toolCallId": tool_call_id,
                "error": error_message
            })

    # =========================================================
    # FINAL RESPONSE
    # =========================================================

    final_response = {
        "results": results
    }

    logger.info(
        "VAPI FINAL RESPONSE SENT | "
        "result_count=%s",
        len(results)
    )

    return final_response