document.querySelectorAll(".example-prompts button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelector("vapi-widget")?.scrollIntoView({
      behavior: "smooth",
      block: "center"
    });
  });
});

document.addEventListener("DOMContentLoaded", () => {
  const widget = document.querySelector("vapi-widget");

  if (!widget) return;

  widget.addEventListener("call-start", () => {
    console.log("Ava voice conversation started");
  });

  widget.addEventListener("call-end", () => {
    console.log("Ava voice conversation ended");
  });

  widget.addEventListener("error", (event) => {
    console.error("Vapi widget error:", event.detail);
  });
});