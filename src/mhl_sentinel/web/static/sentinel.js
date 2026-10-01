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

  // Type-to-filter on the folded "all projects" list.
  function wireFilters(root) {
    root.querySelectorAll("[data-filter]").forEach(function (input) {
      if (input.dataset.wired) return;
      input.dataset.wired = "1";
      input.hidden = false;
      var list = document.getElementById(input.dataset.filter);
      if (!list) return;
      input.addEventListener("input", function () {
        var q = input.value.trim().toLowerCase();
        list.querySelectorAll("li[data-name]").forEach(function (li) {
          li.hidden = q !== "" && li.dataset.name.toLowerCase().indexOf(q) === -1;
        });
      });
    });
  }

  // Live refreshes (SSE) replace whole fragments: keep folded sections open and the filter text.
  var openIds = [];
  var filterText = {};
  var focus = null; // {id, start, end} of a focused text field about to be replaced
  document.addEventListener("htmx:beforeSwap", function () {
    openIds = Array.prototype.map.call(document.querySelectorAll("details[id][open]"),
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

  document.addEventListener("DOMContentLoaded", function () { wireFilters(document); });
  document.addEventListener("htmx:afterSettle", function (e) {
    wireFilters(e.target);
    restore(e.target);
  });
})();
