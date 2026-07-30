document.addEventListener("DOMContentLoaded", function () {
  var cardsEl = document.getElementById("cards");
  var summaryEl = document.getElementById("summary");
  var loadingEl = document.querySelector('[data-state="loading"]');
  var emptyEl = document.querySelector('[data-state="empty"]');
  var errorEl = document.querySelector('[data-state="error"]');
  var prevBtn = document.getElementById("prev-btn");
  var nextBtn = document.getElementById("next-btn");
  var pageInfoEl = document.getElementById("page-info");
  var formEl = document.getElementById("search-form");
  var queryInput = formEl.querySelector('input[name="query"]');

  var currentPage = 1;
  var totalPages = 1;
  var currentQuery = "";

  function setState(name) {
    loadingEl.hidden = name !== "loading";
    emptyEl.hidden = name !== "empty";
    errorEl.hidden = name !== "error";
  }

  function clearCards() {
    while (cardsEl.firstChild) {
      cardsEl.removeChild(cardsEl.firstChild);
    }
  }

  function pickText(item, keys) {
    for (var i = 0; i < keys.length; i += 1) {
      var value = item[keys[i]];
      if (typeof value === "string" && value.trim() !== "") {
        return value;
      }
    }
    return "";
  }

  function truncate(text, max) {
    if (text.length <= max) {
      return text;
    }
    return text.slice(0, max) + "...";
  }

  function addMeta(card, label, value) {
    if (typeof value !== "string" || value === "") {
      return;
    }
    var row = document.createElement("span");
    row.textContent = label + ": " + value;
    card.appendChild(row);
  }

  function renderCard(item) {
    var card = document.createElement("article");
    card.className = "card";

    var title = document.createElement("h2");
    var titleText = pickText(item, ["title"]);
    var permalink = typeof item.permalink === "string" ? item.permalink : "";
    if (permalink !== "") {
      var titleLink = document.createElement("a");
      titleLink.setAttribute("href", "/memory?permalink=" + encodeURIComponent(permalink));
      titleLink.textContent = titleText === "" ? "Untitled" : titleText;
      title.appendChild(titleLink);
    } else {
      title.textContent = titleText === "" ? "Untitled" : titleText;
    }
    card.appendChild(title);

    var excerpt = pickText(item, ["content", "body", "summary", "text"]);
    if (excerpt !== "") {
      var body = document.createElement("p");
      body.textContent = truncate(excerpt, 240);
      card.appendChild(body);
    }

    var meta = document.createElement("div");
    meta.className = "meta";
    addMeta(meta, "type", typeof item.type === "string" ? item.type : "");
    addMeta(meta, "created", typeof item.created_at === "string" ? item.created_at : "");
    card.appendChild(meta);

    return card;
  }

  function updatePager() {
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;
    pageInfoEl.textContent = "Page " + currentPage + " of " + totalPages;
  }

  function load(page) {
    setState("loading");
    clearCards();
    var url = "/api/memories?page=1&page_size=12";
    if (page !== 1) {
      url = "/api/memories?page=" + page + "&page_size=12";
    }
    if (currentQuery !== "") {
      url += "&query=" + encodeURIComponent(currentQuery);
    }
    fetch(url)
      .then(function (res) {
        if (!res.ok) {
          throw new Error("request failed with status " + res.status);
        }
        return res.json();
      })
      .then(function (data) {
        var items = Array.isArray(data.items) ? data.items : [];
        currentPage = typeof data.page === "number" ? data.page : page;
        totalPages = typeof data.total_pages === "number" ? data.total_pages : 1;
        var total = typeof data.total === "number" ? data.total : items.length;

        if (items.length === 0) {
          summaryEl.textContent = currentQuery === ""
            ? "No recent activity"
            : 'No results for "' + currentQuery + '"';
          setState("empty");
        } else {
          summaryEl.textContent = currentQuery === ""
            ? total + " memories"
            : 'Search results for "' + currentQuery + '" (' + total + ")";
          setState("ready");
          for (var i = 0; i < items.length; i += 1) {
            cardsEl.appendChild(renderCard(items[i] || {}));
          }
        }
        updatePager();
      })
      .catch(function () {
        summaryEl.textContent = "";
        setState("error");
        updatePager();
      });
  }

  formEl.addEventListener("submit", function (event) {
    event.preventDefault();
    currentQuery = queryInput.value.trim();
    load(1);
  });

  queryInput.addEventListener("input", function () {
    if (queryInput.value.trim() === "" && currentQuery !== "") {
      currentQuery = "";
      load(1);
    }
  });

  prevBtn.addEventListener("click", function () {
    if (currentPage > 1) {
      load(currentPage - 1);
    }
  });

  nextBtn.addEventListener("click", function () {
    if (currentPage < totalPages) {
      load(currentPage + 1);
    }
  });

  load(1);
});
