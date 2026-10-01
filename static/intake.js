// Staff intake: live catalog search, auto-crop previews, raw/graded and new-customer toggles.
(function () {
  var search = document.getElementById("card-search");
  var results = document.getElementById("card-results");
  var cardId = document.getElementById("card-id");
  var picked = document.getElementById("card-picked");
  var timer = null;

  function money(v) {
    return v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function clearNode(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function textEl(tag, text, cls) {
    var el = document.createElement(tag);
    el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function pick(card) {
    cardId.value = card.id;
    clearNode(picked);
    picked.appendChild(textEl("strong", card.label));
    picked.appendChild(textEl("div", card.set_line + (card.rarity ? " · " + card.rarity : ""), "muted small"));
    picked.appendChild(textEl("div", "Near Mint price: " + money(card.nm), "small"));
    picked.hidden = false;
    results.hidden = true;
    search.value = card.label;
  }

  search.addEventListener("input", function () {
    clearTimeout(timer);
    var q = search.value.trim();
    if (q.length < 2) { results.hidden = true; return; }
    timer = setTimeout(function () {
      fetch("/api/catalog/search?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (rows) {
          clearNode(results);
          if (!rows.length) {
            results.appendChild(textEl("div", "No match in the catalog.", "muted small"));
          }
          rows.forEach(function (card) {
            var b = document.createElement("button");
            b.type = "button";
            var left = document.createElement("span");
            left.appendChild(textEl("strong", card.label));
            left.appendChild(document.createElement("br"));
            left.appendChild(textEl("span", card.set_line, "muted small"));
            b.appendChild(left);
            b.appendChild(textEl("span", money(card.nm), "num"));
            b.addEventListener("click", function () { pick(card); });
            results.appendChild(b);
          });
          results.hidden = false;
        });
    }, 200);
  });

  // auto-crop previews
  function kind() { return document.querySelector("input[name=kind]:checked").value; }
  document.querySelectorAll("input[type=file][data-preview]").forEach(function (input) {
    input.addEventListener("change", function () {
      var img = document.getElementById(input.dataset.preview);
      var note = document.getElementById(input.dataset.preview + "-note");
      if (!input.files.length) { img.hidden = true; note.textContent = ""; return; }
      note.textContent = "Cropping…";
      var fd = new FormData();
      fd.append("image", input.files[0]);
      fd.append("kind", kind());
      fetch("/api/preview-crop", { method: "POST", body: fd })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.error) { note.textContent = data.error; img.hidden = true; return; }
          img.src = data.image;
          img.alt = "Automatic crop preview";
          img.hidden = false;
          note.textContent = data.warnings.length ? data.warnings.join(" ") : "Crop looks good.";
          note.className = "small " + (data.warnings.length ? "tag" : "up");
        })
        .catch(function () { note.textContent = "Preview failed; the card will still be processed on save."; });
    });
  });

  // raw vs graded
  document.querySelectorAll("input[name=kind]").forEach(function (r) {
    r.addEventListener("change", function () {
      var graded = kind() === "graded";
      document.getElementById("raw-fields").hidden = graded;
      document.getElementById("graded-fields").hidden = !graded;
    });
  });

  // new customer
  var cust = document.getElementById("customer");
  function syncCustomer() { document.getElementById("new-customer").hidden = cust.value !== "new"; }
  cust.addEventListener("change", syncCustomer);
  syncCustomer();

  document.getElementById("intake-form").addEventListener("submit", function (e) {
    if (!cardId.value) {
      e.preventDefault();
      search.focus();
      alert("Pick the card from the catalog search first.");
    }
  });
})();
