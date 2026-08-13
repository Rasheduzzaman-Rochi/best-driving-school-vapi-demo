const supabaseClient = window.supabase.createClient(
  window.APP_CONFIG.SUPABASE_URL,
  window.APP_CONFIG.SUPABASE_PUBLISHABLE_KEY
);

const loginForm = document.getElementById("loginForm");
const loginButton = document.getElementById("loginButton");
const loginMessage = document.getElementById("loginMessage");

// If already logged in as an admin, go directly to dashboard.
checkExistingSession();

async function checkExistingSession() {
  const {
    data: { session }
  } = await supabaseClient.auth.getSession();

  if (!session) {
    return;
  }

  const isAdmin = await verifyAdmin(session.user.id);

  if (isAdmin) {
    window.location.href = "/admin/dashboard/";
  }
}

async function verifyAdmin(userId) {
  const { data, error } = await supabaseClient
    .from("admin_profiles")
    .select("id, role")
    .eq("id", userId)
    .eq("role", "admin")
    .maybeSingle();

  if (error) {
    console.error("Admin verification error:", error);
    return false;
  }

  return Boolean(data);
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  loginMessage.textContent = "";
  loginMessage.className = "form-message";

  loginButton.disabled = true;
  loginButton.textContent = "Signing in...";

  const email = document
    .getElementById("email")
    .value
    .trim();

  const password = document
    .getElementById("password")
    .value;

  try {
    const {
      data,
      error
    } = await supabaseClient.auth.signInWithPassword({
      email,
      password
    });

    if (error) {
      showError("Invalid email or password.");
      return;
    }

    if (!data.user) {
      showError("Unable to sign in.");
      return;
    }

    const isAdmin = await verifyAdmin(data.user.id);

    if (!isAdmin) {
      await supabaseClient.auth.signOut();

      showError(
        "This account does not have administrator access."
      );

      return;
    }

    loginMessage.textContent =
      "Login successful. Opening dashboard...";

    loginMessage.classList.add("success");

    window.location.href = "/admin/dashboard/";

  } catch (error) {
    console.error(error);

    showError(
      "Something went wrong. Please try again."
    );

  } finally {
    loginButton.disabled = false;
    loginButton.textContent = "Sign In";
  }
});

function showError(message) {
  loginMessage.textContent = message;
  loginMessage.classList.add("error");

  loginButton.disabled = false;
  loginButton.textContent = "Sign In";
}