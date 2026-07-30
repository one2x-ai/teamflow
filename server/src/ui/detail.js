document.addEventListener("DOMContentLoaded", function () {
  var loadingEl = document.querySelector('[data-state="loading"]');
  var errorEl = document.querySelector('[data-state="error"]');
  var detailEl = document.getElementById("detail");
  var titleEl = document.getElementById("detail-title");
  var contentEl = document.getElementById("detail-content");
  var permalink = new URLSearchParams(window.location.search).get("permalink") || "";

  if (permalink === "") {
    loadingEl.hidden = true;
    errorEl.hidden = false;
    return;
  }

  fetch("/api/memory?permalink=" + encodeURIComponent(permalink))
    .then(function (res) {
      if (!res.ok) {
        throw new Error("request failed with status " + res.status);
      }
      return res.json();
    })
    .then(function (memory) {
      titleEl.textContent = typeof memory.title === "string" && memory.title !== ""
        ? memory.title
        : "Untitled";
      contentEl.textContent = typeof memory.content === "string" ? memory.content : "";
      loadingEl.hidden = true;
      detailEl.hidden = false;
    })
    .catch(function () {
      loadingEl.hidden = true;
      errorEl.hidden = false;
    });
});
