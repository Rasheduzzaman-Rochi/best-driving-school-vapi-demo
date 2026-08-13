const supabaseClient = window.supabase.createClient(
  window.APP_CONFIG.SUPABASE_URL,
  window.APP_CONFIG.SUPABASE_PUBLISHABLE_KEY
);

const bookingTableBody =
  document.getElementById("bookingTableBody");

const logoutButton =
  document.getElementById("logoutButton");

const refreshButton =
  document.getElementById("refreshButton");

let currentBookings = [];

initializeDashboard();

async function initializeDashboard() {
  const {
    data: { session },
    error
  } = await supabaseClient.auth.getSession();

  if (error || !session) {
    redirectToLogin();
    return;
  }

  const { data: adminProfile } = await supabaseClient
    .from("admin_profiles")
    .select("full_name, role")
    .eq("id", session.user.id)
    .eq("role", "admin")
    .maybeSingle();

  if (!adminProfile) {
    await supabaseClient.auth.signOut();
    redirectToLogin();
    return;
  }

  document.getElementById("adminWelcome").textContent =
    `Welcome, ${adminProfile.full_name}`;

  await loadBookings();
}

async function loadBookings() {
  bookingTableBody.innerHTML = `
    <tr>
      <td colspan="7" class="empty-state">
        Loading bookings...
      </td>
    </tr>
  `;

  const { data, error } = await supabaseClient
    .from("bookings")
    .select(`
      id,
      customer_name,
      callback_number,
      service_type,
      booking_date,
      start_time,
      end_time,
      status,
      created_at
    `)
    .order("booking_date", { ascending: true })
    .order("start_time", { ascending: true });

  if (error) {
    console.error(error);

    bookingTableBody.innerHTML = `
      <tr>
        <td colspan="7" class="empty-state error-text">
          Could not load bookings.
        </td>
      </tr>
    `;

    return;
  }

  currentBookings = data || [];

  renderBookings();
  updateStats();
}

function renderBookings() {
  if (!currentBookings.length) {
    bookingTableBody.innerHTML = `
      <tr>
        <td colspan="7" class="empty-state">
          No bookings yet.
        </td>
      </tr>
    `;

    return;
  }

  bookingTableBody.innerHTML =
    currentBookings
      .map((booking) => {

        const statusClass =
          booking.status === "confirmed"
            ? "confirmed"
            : "cancelled";

        return `
          <tr>

            <td>
              <strong>
                ${escapeHtml(booking.customer_name)}
              </strong>
            </td>

            <td>
              ${escapeHtml(booking.callback_number)}
            </td>

            <td>
              ${formatService(booking.service_type)}
            </td>

            <td>
              ${formatDate(booking.booking_date)}
            </td>

            <td>
              ${formatTime(booking.start_time)}
              –
              ${formatTime(booking.end_time)}
            </td>

            <td>
              <span class="status-pill ${statusClass}">
                ${booking.status}
              </span>
            </td>

            <td>
              ${
                booking.status === "confirmed"
                  ? `
                    <button
                      class="cancel-booking-btn"
                      onclick="cancelBooking('${booking.id}')"
                    >
                      Cancel
                    </button>
                  `
                  : "—"
              }
            </td>

          </tr>
        `;
      })
      .join("");
}

function updateStats() {
  const today =
    new Date().toISOString().split("T")[0];

  const confirmed =
    currentBookings.filter(
      booking => booking.status === "confirmed"
    );

  const todayBookings =
    confirmed.filter(
      booking => booking.booking_date === today
    );

  const upcoming =
    confirmed.filter(
      booking => booking.booking_date > today
    );

  document.getElementById("todayCount").textContent =
    todayBookings.length;

  document.getElementById("upcomingCount").textContent =
    upcoming.length;

  document.getElementById("confirmedCount").textContent =
    confirmed.length;

  document.getElementById("totalCount").textContent =
    currentBookings.length;
}

async function cancelBooking(bookingId) {
  const confirmed =
    window.confirm(
      "Are you sure you want to cancel this booking?"
    );

  if (!confirmed) {
    return;
  }

  const { error } = await supabaseClient
    .from("bookings")
    .update({
      status: "cancelled"
    })
    .eq("id", bookingId);

  if (error) {
    console.error(error);

    alert("Unable to cancel this booking.");
    return;
  }

  await loadBookings();
}

refreshButton.addEventListener(
  "click",
  loadBookings
);

logoutButton.addEventListener(
  "click",
  async () => {

    await supabaseClient.auth.signOut();

    redirectToLogin();
  }
);

function redirectToLogin() {
  window.location.href = "/admin/login/";
}

function formatDate(dateString) {
  const date =
    new Date(`${dateString}T00:00:00`);

  return date.toLocaleDateString(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric"
    }
  );
}

function formatTime(timeString) {
  const [hourString, minuteString] =
    timeString.split(":");

  let hour = Number(hourString);

  const suffix =
    hour >= 12 ? "PM" : "AM";

  hour = hour % 12 || 12;

  return `${hour}:${minuteString} ${suffix}`;
}

function formatService(service) {
  if (!service) {
    return "—";
  }

  return escapeHtml(
    service
      .replaceAll("_", " ")
      .replace(/\b\w/g, char => char.toUpperCase())
  );
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}