import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

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

if not VAPI_TOOL_SECRET:
    raise RuntimeError("VAPI_TOOL_SECRET is missing.")


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
    version="1.0.0",
)

logger = logging.getLogger("uvicorn.error")


# =========================================================
# BUSINESS RULES
# =========================================================

# Python weekday():
# Monday = 0
# Tuesday = 1
# Wednesday = 2
# Thursday = 3
# Friday = 4
# Saturday = 5
# Sunday = 6
#
# Open: Sunday - Thursday
# Closed: Friday + Saturday

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
# REQUEST MODELS
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
        "message": "Best Driving School Booking API is running",
    }


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def parse_date(value: str):
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()

    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date must use YYYY-MM-DD format.",
        )


def is_open_day(booking_date) -> bool:
    return booking_date.weekday() in OPEN_DAYS


def format_clock(value: str) -> str:
    clean_value = value[:5]

    parsed_time = datetime.strptime(
        clean_value,
        "%H:%M"
    )

    return parsed_time.strftime("%-I:%M %p")


def format_slot(start: str, end: str) -> str:
    return (
        f"{format_clock(start)} "
        f"to {format_clock(end)}"
    )


def clean_vapi_string(value: str) -> str:
    """
    Keep Vapi result as a clean single-line string.
    """
    return " ".join(
        str(value).split()
    )


def first_not_none(*values):
    """
    Return the first value that is not None.
    """
    for value in values:
        if value is not None:
            return value

    return None


def normalize_parameters(raw: Any) -> Dict[str, Any]:
    """
    Vapi parameters normally arrive as dicts.
    Also supports JSON strings as a fallback.
    """

    if raw is None:
        return {}

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):

        try:
            parsed = json.loads(raw)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            pass

    return {}


def extract_vapi_tool_calls(
    message: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Supports Vapi's current documented formats:

    1)
    toolWithToolCallList:
      [
        {
          "name": "check_available_slots",
          "toolCall": {
            "id": "...",
            "parameters": {...}
          }
        }
      ]

    2)
    toolCallList:
      [
        {
          "id": "...",
          "name": "check_available_slots",
          "parameters": {...}
        }
      ]

    Also keeps fallbacks for:
    - arguments
    - OpenAI-style function.name / function.arguments
    """

    normalized: List[Dict[str, Any]] = []

    # -----------------------------------------------------
    # PREFERRED VAPI WRAPPED FORMAT
    # -----------------------------------------------------

    wrapped_calls = (
        message.get("toolWithToolCallList")
        or []
    )

    if wrapped_calls:

        for wrapped in wrapped_calls:

            tool_call = (
                wrapped.get("toolCall")
                or {}
            )

            function_block = (
                tool_call.get("function")
                or {}
            )

            tool_call_id = first_not_none(
                tool_call.get("id"),
                wrapped.get("id"),
            )

            tool_name = first_not_none(
                wrapped.get("name"),
                tool_call.get("name"),
                function_block.get("name"),
            )

            raw_parameters = first_not_none(
                tool_call.get("parameters"),
                tool_call.get("arguments"),
                function_block.get("parameters"),
                function_block.get("arguments"),
                wrapped.get("parameters"),
                wrapped.get("arguments"),
            )

            normalized.append({
                "id": tool_call_id,
                "name": tool_name,
                "parameters": normalize_parameters(
                    raw_parameters
                ),
            })

        return normalized

    # -----------------------------------------------------
    # DIRECT toolCallList FORMAT
    # -----------------------------------------------------

    direct_calls = (
        message.get("toolCallList")
        or []
    )

    for tool_call in direct_calls:

        function_block = (
            tool_call.get("function")
            or {}
        )

        tool_call_id = (
            tool_call.get("id")
        )

        tool_name = first_not_none(
            tool_call.get("name"),
            function_block.get("name"),
        )

        raw_parameters = first_not_none(
            tool_call.get("parameters"),
            tool_call.get("arguments"),
            function_block.get("parameters"),
            function_block.get("arguments"),
        )

        normalized.append({
            "id": tool_call_id,
            "name": tool_name,
            "parameters": normalize_parameters(
                raw_parameters
            ),
        })

    return normalized


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
    # CLOSED DAY CHECK
    # -----------------------------------------------------

    if not is_open_day(requested_date):

        return {
            "available": False,
            "closed": True,
            "booking_date":
                payload.booking_date,

            "service_type":
                payload.service_type,

            "message":
                "The driving school is closed "
                "on Fridays and Saturdays.",

            "available_slots": [],
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
                ),
            })

    return {
        "available":
            bool(available_slots),

        "closed":
            False,

        "booking_date":
            payload.booking_date,

        "service_type":
            payload.service_type,

        "available_slots":
            available_slots,
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
    # CLOSED DAY CHECK
    # -----------------------------------------------------

    if not is_open_day(requested_date):

        raise HTTPException(
            status_code=400,
            detail=(
                "The driving school is closed "
                "on Fridays and Saturdays."
            ),
        )

    # -----------------------------------------------------
    # VALID SLOT CHECK
    # -----------------------------------------------------

    valid_slots = {
        start: end
        for start, end in SLOTS
    }

    # Normalize:
    # 09:00:00 -> 09:00
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
            ),
        )

    # -----------------------------------------------------
    # CHECK SLOT AGAIN BEFORE SAVING
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

        updated_availability = (
            check_availability(
                AvailabilityRequest(
                    booking_date=
                        payload.booking_date,

                    service_type=
                        payload.service_type,
                )
            )
        )

        return {
            "success": False,

            "reason":
                "slot_already_booked",

            "message":
                "That time slot is no "
                "longer available.",

            "available_slots":
                updated_availability.get(
                    "available_slots",
                    []
                ),
        }

    end_time = (
        valid_slots[
            requested_start_time
        ]
    )

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
                    payload.notes,
            })
            .execute()
        )

    except Exception:

        logger.exception(
            "DATABASE BOOKING ERROR"
        )

        updated_availability = (
            check_availability(
                AvailabilityRequest(
                    booking_date=
                        payload.booking_date,

                    service_type=
                        payload.service_type,
                )
            )
        )

        return {
            "success": False,

            "reason":
                "booking_conflict",

            "message": (
                "That slot could not be booked "
                "because it may have just "
                "become unavailable."
            ),

            "available_slots":
                updated_availability.get(
                    "available_slots",
                    []
                ),
        }

    # -----------------------------------------------------
    # VERIFY INSERT
    # -----------------------------------------------------

    if not response.data:

        raise HTTPException(
            status_code=500,
            detail=(
                "Booking could not be created."
            ),
        )

    booking = response.data[0]

    confirmed_time = format_slot(
        booking["start_time"],
        booking["end_time"],
    )

    return {
        "success": True,

        "booking_id":
            booking["id"],

        "customer_name":
            booking["customer_name"],

        "callback_number":
            booking["callback_number"],

        "service_type":
            booking["service_type"],

        "booking_date":
            booking["booking_date"],

        "start_time":
            booking["start_time"],

        "end_time":
            booking["end_time"],

        "status":
            booking["status"],

        "message":
            f"Booking confirmed for "
            f"{confirmed_time}.",
    }


# =========================================================
# VAPI CUSTOM TOOL ENDPOINT
# =========================================================

@app.post("/api/vapi-tools")
def handle_vapi_tools(
    payload: dict,
    authorization: Optional[str] = Header(
        default=None
    ),
):

    # -----------------------------------------------------
    # BEARER AUTH
    # -----------------------------------------------------

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
            detail="Unauthorized",
        )

    # -----------------------------------------------------
    # READ MESSAGE
    # -----------------------------------------------------

    message = (
        payload.get("message")
        or {}
    )

    logger.info(
        "VAPI MESSAGE RECEIVED | "
        "type=%s | keys=%s",
        message.get("type"),
        list(message.keys()),
    )

    # -----------------------------------------------------
    # NORMALIZE VAPI TOOL CALL FORMAT
    # -----------------------------------------------------

    tool_calls = (
        extract_vapi_tool_calls(
            message
        )
    )

    if not tool_calls:

        logger.warning(
            "VAPI TOOL REQUEST: "
            "no usable tool calls found"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "No Vapi tool calls found."
            ),
        )

    logger.info(
        "VAPI TOOL REQUEST RECEIVED | "
        "count=%s",
        len(tool_calls),
    )

    results = []

    # -----------------------------------------------------
    # PROCESS TOOL CALLS
    # -----------------------------------------------------

    for tool_call in tool_calls:

        tool_call_id = (
            tool_call.get("id")
        )

        tool_name = (
            tool_call.get("name")
        )

        arguments = (
            tool_call.get("parameters")
            or {}
        )

        logger.info(
            "VAPI TOOL START | "
            "tool=%s | "
            "toolCallId=%s | "
            "argument_keys=%s",

            tool_name,
            tool_call_id,
            list(arguments.keys()),
        )

        if not tool_call_id:

            logger.warning(
                "VAPI TOOL CALL missing id | "
                "tool=%s",
                tool_name,
            )

            continue

        try:

            # =================================================
            # TOOL:
            # CHECK AVAILABLE SLOTS
            # =================================================

            if tool_name == (
                "check_available_slots"
            ):

                booking_date_argument = (
                    arguments.get(
                        "booking_date"
                    )
                )

                if not booking_date_argument:

                    raise ValueError(
                        "booking_date is required."
                    )

                availability = (
                    check_availability(
                        AvailabilityRequest(
                            booking_date=
                                booking_date_argument,

                            service_type=
                                arguments.get(
                                    "service_type",
                                    "driving_lesson",
                                ),
                        )
                    )
                )

                # ---------------------------------------------
                # CLOSED DAY
                # ---------------------------------------------

                if availability.get(
                    "closed"
                ):

                    booking_date = (
                        availability.get(
                            "booking_date"
                        )
                    )

                    vapi_result = (
                        "The driving school is "
                        f"closed on {booking_date}. "
                        "The school is closed on "
                        "Fridays and Saturdays. "
                        "Ask the caller to choose "
                        "a date from Sunday through "
                        "Thursday."
                    )

                # ---------------------------------------------
                # NO AVAILABLE SLOT
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
                        "There are no available "
                        "driving lesson slots for "
                        f"{booking_date}. "
                        "Tell the caller there are "
                        "no open lesson times on "
                        "that date and ask for "
                        "another date. "
                        "Do not invent availability."
                    )

                # ---------------------------------------------
                # AVAILABLE SLOTS
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
                        "Available driving lesson "
                        f"slots for {booking_date}: "
                        f"{slots_text}. "
                        "Tell the caller these exact "
                        "available times now and ask "
                        "which one they would like "
                        "to book. "
                        "Do not offer any time that "
                        "is not listed."
                    )

            # =================================================
            # TOOL:
            # CREATE BOOKING
            # =================================================

            elif tool_name == (
                "create_booking"
            ):

                required_fields = [
                    "customer_name",
                    "callback_number",
                    "service_type",
                    "booking_date",
                    "start_time",
                ]

                missing_fields = [
                    field
                    for field
                    in required_fields
                    if not arguments.get(
                        field
                    )
                ]

                if missing_fields:

                    raise ValueError(
                        "Missing required booking "
                        "fields: "
                        + ", ".join(
                            missing_fields
                        )
                    )

                booking_result = (
                    create_booking(
                        BookingRequest(
                            customer_name=
                                arguments[
                                    "customer_name"
                                ],

                            callback_number=
                                arguments[
                                    "callback_number"
                                ],

                            service_type=
                                arguments[
                                    "service_type"
                                ],

                            booking_date=
                                arguments[
                                    "booking_date"
                                ],

                            start_time=
                                arguments[
                                    "start_time"
                                ],

                            notes=
                                arguments.get(
                                    "notes"
                                ),
                        )
                    )
                )

                # ---------------------------------------------
                # BOOKING SUCCESS
                # ---------------------------------------------

                if booking_result.get(
                    "success"
                ):

                    confirmed_time = (
                        format_slot(
                            booking_result[
                                "start_time"
                            ],
                            booking_result[
                                "end_time"
                            ],
                        )
                    )

                    booking_date = (
                        booking_result.get(
                            "booking_date"
                        )
                    )

                    vapi_result = (
                        "The booking was "
                        "successfully saved in "
                        "the database. "
                        f"Confirmed date: "
                        f"{booking_date}. "
                        f"Confirmed time: "
                        f"{confirmed_time}. "
                        "Tell the caller their "
                        "lesson has been "
                        "successfully booked for "
                        "this exact date and time."
                    )

                # ---------------------------------------------
                # BOOKING FAILED / CONFLICT
                # ---------------------------------------------

                else:

                    available_slots = (
                        booking_result.get(
                            "available_slots",
                            []
                        )
                    )

                    if available_slots:

                        alternatives = (
                            ", ".join(
                                slot["label"]
                                for slot
                                in available_slots
                            )
                        )

                        vapi_result = (
                            "The selected slot "
                            "could not be booked "
                            "because it is no longer "
                            "available. "
                            "The currently available "
                            "times are: "
                            f"{alternatives}. "
                            "Apologize briefly and "
                            "ask the caller to choose "
                            "one of these remaining "
                            "times."
                        )

                    else:

                        error_message = (
                            booking_result.get(
                                "message",
                                "Booking could not "
                                "be completed.",
                            )
                        )

                        vapi_result = (
                            "The booking was not "
                            "completed. "
                            f"Reason: "
                            f"{error_message} "
                            "Do not tell the caller "
                            "that their booking is "
                            "confirmed."
                        )

            # =================================================
            # UNKNOWN TOOL
            # =================================================

            else:

                vapi_result = (
                    "The requested tool "
                    f"'{tool_name}' is not "
                    "supported by the booking API."
                )

            # -------------------------------------------------
            # CLEAN RESPONSE STRING
            # -------------------------------------------------

            vapi_result = (
                clean_vapi_string(
                    vapi_result
                )
            )

            # -------------------------------------------------
            # VAPI RESPONSE
            # -------------------------------------------------

            results.append({
                "name":
                    tool_name,

                "toolCallId":
                    tool_call_id,

                "result":
                    vapi_result,
            })

            # -------------------------------------------------
            # DEBUG LOGS
            # -------------------------------------------------

            if tool_name == (
                "check_available_slots"
            ):

                logger.info(
                    "VAPI TOOL RESULT | "
                    "tool=%s | "
                    "toolCallId=%s | "
                    "result=%s",

                    tool_name,
                    tool_call_id,
                    vapi_result,
                )

            else:

                logger.info(
                    "VAPI TOOL RESULT | "
                    "tool=%s | "
                    "toolCallId=%s | "
                    "completed=true",

                    tool_name,
                    tool_call_id,
                )

        # =====================================================
        # TOOL ERROR
        # =====================================================

        except Exception:

            logger.exception(
                "VAPI TOOL ERROR | "
                "tool=%s | "
                "toolCallId=%s",

                tool_name,
                tool_call_id,
            )

            error_result = (
                clean_vapi_string(
                    "The booking system "
                    "could not complete this "
                    "request. "
                    "Do not claim that "
                    "availability was checked "
                    "or that a booking was "
                    "confirmed."
                )
            )

            results.append({
                "name":
                    tool_name,

                "toolCallId":
                    tool_call_id,

                "result":
                    error_result,
            })

    # =========================================================
    # FINAL RESPONSE
    # =========================================================

    if not results:

        raise HTTPException(
            status_code=400,
            detail=(
                "No valid Vapi tool "
                "calls could be processed."
            ),
        )

    final_response = {
        "results": results
    }

    logger.info(
        "VAPI FINAL RESPONSE SENT | "
        "result_count=%s",
        len(results),
    )

    return final_response