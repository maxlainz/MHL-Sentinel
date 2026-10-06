/* MHL Sentinel · proposal D. Progressive enhancement only: every page works without it. */
(function () {
  "use strict";

  // A refused button (409, e.g. Seal on a project that is no longer unsealed) still carries the
  // refreshed fragment with the reason: swap it instead of silently doing nothing.
  document.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.xhr && e.detail.xhr.status === 409) {
      e.detail.shouldSwap = true;
      e.detail.isError = false;
    }
  });

  // Confirmation dialogs (Retire, Verify now): native <dialog>; delegated, so they keep working
  // after a live refresh replaces the fragment.
  document.addEventListener("click", function (e) {
    var t = e.target instanceof Element ? e.target : null;
    if (!t) return;
    var open = t.closest("[data-dialog-open]");
    if (open) {
      var d = document.getElementById(open.dataset.dialogOpen);
      if (d && typeof d.showModal === "function") d.showModal();
      return;
    }
    var close = t.closest("[data-dialog-close]");
    if (close) {
      var parent = close.closest("dialog");
      if (parent) parent.close();
      return;
    }
    var dl = t.closest("[data-download-url]");
    if (dl) {
      // "Retire and download MHL": fetch the zip first (Retire deletes the saved history), save
      // it, and only then submit the form. If the download fails the project is not retired.
      var form = dl.closest("form");
      dl.disabled = true;
      fetch(dl.dataset.downloadUrl).then(function (r) {
        if (!r.ok) throw new Error("download failed");
        return r.blob();
      }).then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = dl.dataset.downloadName || "ascmhl.zip";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 10000);
        if (form && form.requestSubmit) form.requestSubmit();
      }).catch(function () {
        dl.disabled = false;
        window.alert("The MHL history could not be downloaded, so the project was not forgotten.");
      });
    }
  });

  // Type-to-filter on the archived files list (D75).
  function wireFilters(root) {
    root.querySelectorAll("[data-filter]").forEach(function (input) {
      if (input.dataset.wired) return;
      input.dataset.wired = "1";
      input.hidden = false;
      var list = document.getElementById(input.dataset.filter);
      if (!list) return;
      input.addEventListener("input", function () {
        var q = input.value.trim().toLowerCase();
        list.querySelectorAll("li[data-name], tr[data-name]").forEach(function (li) {
          li.hidden = q !== "" && li.dataset.name.toLowerCase().indexOf(q) === -1;
        });
      });
    });
  }

  // Main-screen search and group chips (D76, D77). The input lives outside the #projects
  // fragment, so SSE refreshes never wipe it; the query and the chosen chip are re-applied after
  // every swap. The chips are rendered inside the fragment (their counts are fresh after each
  // swap). Folds with something to show are forced open (data-forced) while a query or a chip is
  // active and go back to their remembered state when both are cleared.
  var CHIP_KEY = "sentinel.chip";
  var chip = "all";
  try { chip = window.localStorage.getItem(CHIP_KEY) || "all"; } catch (err) { chip = "all"; }

  function setChip(value) {
    chip = value;
    try { window.localStorage.setItem(CHIP_KEY, value); } catch (err) { /* storage unavailable */ }
  }

  function applySearch() {
    var input = document.getElementById("project-search");
    var proj = document.getElementById("projects");
    if (!input || !proj) return;
    var q = input.value.trim().toLowerCase();
    // A remembered chip whose group is now empty (no chip rendered) falls back to All.
    var chips = proj.querySelectorAll("[data-chip]");
    var known = Array.prototype.some.call(chips, function (c) { return c.dataset.chip === chip; });
    var active = known ? chip : "all";
    chips.forEach(function (c) {
      c.setAttribute("aria-pressed", c.dataset.chip === active ? "true" : "false");
    });
    var forced = q !== "" || active !== "all";
    var shown = 0;
    proj.querySelectorAll("section.section[data-group]").forEach(function (sec) {
      var inScope = active === "all" || sec.dataset.group === active;
      var visible = 0;
      sec.querySelectorAll("li[data-name]").forEach(function (li) {
        var hit = q === "" || li.dataset.name.toLowerCase().indexOf(q) !== -1;
        li.hidden = !hit;
        if (hit) visible++;
      });
      sec.querySelectorAll("details.fold").forEach(function (d) {
        var match = forced && d.querySelector("li[data-name]:not([hidden])") !== null;
        d.hidden = q !== "" && !match;
        if (match && !d.open) { d.open = true; d.dataset.forced = "1"; }
        if (!match && d.dataset.forced) { d.open = false; delete d.dataset.forced; }
      });
      var n = sec.querySelector(".sec-head .count");
      if (n) {
        if (!n.dataset.total) n.dataset.total = n.textContent;
        n.textContent = q === "" ? n.dataset.total : visible + " of " + n.dataset.total;
      }
      sec.hidden = !inScope || (q !== "" && visible === 0);
      if (inScope) shown += visible;
    });
    // The closing line and the ignored fold: only on All; the ignored ones are searchable.
    var closing = proj.querySelector("section.closing");
    if (closing) {
      var text = closing.querySelector(".closing-text");
      if (text) text.hidden = forced;
      closing.querySelectorAll("details.fold").forEach(function (d) {
        var any = 0;
        d.querySelectorAll("li[data-name]").forEach(function (li) {
          var hit = q === "" || li.dataset.name.toLowerCase().indexOf(q) !== -1;
          li.hidden = !hit;
          if (hit) any++;
        });
        var match = q !== "" && any > 0;
        d.hidden = q !== "" && !match;
        if (match && !d.open) { d.open = true; d.dataset.forced = "1"; }
        if (!match && d.dataset.forced) { d.open = false; delete d.dataset.forced; }
        if (active === "all") shown += q === "" ? 0 : any;
      });
      closing.hidden = active !== "all" || (q !== "" && closing.querySelector("li[data-name]:not([hidden])") === null);
    }
    var none = document.getElementById("no-match");
    if (none) none.hidden = q === "" || shown > 0;
    var note = document.getElementById("search-note");
    if (note) {
      note.textContent = q === "" ? note.dataset.idle :
        shown + " match" + (shown === 1 ? "" : "es") + " \u00b7 Esc clears";
    }
  }

  document.addEventListener("input", function (e) {
    if (e.target && e.target.id === "project-search") applySearch();
  });
  document.addEventListener("submit", function (e) {
    if (e.target && e.target.id === "search-form") {
      e.preventDefault(); // no request: the list filters in the browser
      applySearch();
    }
  });
  document.addEventListener("click", function (e) {
    var t = e.target instanceof Element ? e.target.closest("[data-chip]") : null;
    if (!t) return;
    setChip(t.dataset.chip);
    applySearch();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && e.target && e.target.id === "project-search") {
      e.target.value = "";
      applySearch();
    }
  });

  // Live refreshes (SSE) replace whole fragments: keep folded sections open and the filter text.
  var openIds = [];
  var filterText = {};
  var focus = null; // {id, start, end} of a focused text field about to be replaced
  document.addEventListener("htmx:beforeSwap", function () {
    openIds = Array.prototype.map.call(
      document.querySelectorAll("details[id][open]:not([data-forced])"),
      function (d) { return d.id; });
    document.querySelectorAll("[data-filter]").forEach(function (i) {
      filterText[i.dataset.filter] = i.value;
    });
    var a = document.activeElement;
    focus = a && a.id && a.matches("input") ?
      { id: a.id, start: a.selectionStart, end: a.selectionEnd } : null;
  });

  function restore(root) {
    openIds.forEach(function (id) {
      var d = document.getElementById(id);
      if (d) d.open = true;
    });
    root.querySelectorAll("[data-filter]").forEach(function (i) {
      var v = filterText[i.dataset.filter];
      if (v && !i.value) {
        i.value = v;
        i.dispatchEvent(new Event("input"));
      }
    });
    // A live refresh must not steal the cursor from someone typing in the filter.
    if (focus) {
      var el = document.getElementById(focus.id);
      if (el && el !== document.activeElement) {
        el.focus({ preventScroll: true });
        try { el.setSelectionRange(focus.start, focus.end); } catch (err) { /* type=search */ }
      }
      focus = null;
    }
  }

  document.addEventListener("DOMContentLoaded", function () { wireFilters(document); applySearch(); });
  document.addEventListener("htmx:afterSettle", function (e) {
    wireFilters(e.target);
    restore(e.target);
    applySearch();
  });
  document.addEventListener("htmx:afterSwap", applySearch);
})();
