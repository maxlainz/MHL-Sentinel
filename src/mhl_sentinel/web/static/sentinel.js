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
        window.alert("The MHL history could not be downloaded, so the project was not retired.");
      });
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
