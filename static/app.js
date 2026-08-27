/* The only script in the application, and it adds nothing the page cannot do
   without it. Server-rendered HTML remains the whole interface: this makes the
   crew-capacity slider report its value as it moves and submit itself when
   released, removes the Apply button it has just made redundant, and keeps the
   reader's place in the queue across a sort and across recording a decision.
   With scripting unavailable the button is still there, both forms still work,
   and a decision still lands on its own row through the anchor the redirect
   carries, as it did before.

   No network call of its own, no dependency, no framework. */
(function () {
  "use strict";

  /* Sorting and moving the capacity slider are both full page loads, and a full
     page load starts at the top — which takes the row the reader was looking at
     out from under them. Store the position before leaving and put it back on
     arrival. Keyed on the path, so following a link through to an asset still
     arrives at the top of that page, which is a different page. */
  var SCROLL_KEY = "heat-triage-scroll";

  function remember() {
    try {
      sessionStorage.setItem(SCROLL_KEY, window.location.pathname + " " + window.scrollY);
    } catch (error) {
      /* Storage is unavailable in a private window in some browsers. Losing the
         scroll position is the whole cost, so there is nothing to report. */
    }
  }

  function stored() {
    var value;
    try {
      value = sessionStorage.getItem(SCROLL_KEY);
      sessionStorage.removeItem(SCROLL_KEY);
    } catch (error) {
      return null;
    }
    if (!value) return null;
    var separator = value.lastIndexOf(" ");
    if (value.slice(0, separator) !== window.location.pathname) return null;
    return parseInt(value.slice(separator + 1), 10) || 0;
  }

  /* Applied twice, and that is not belt-and-braces. Recording a decision
     redirects to the row's own anchor, which is what keeps the place for a
     reader without scripting — and the browser acts on that fragment at its own
     moment, which can be after this script has already run. Re-applying on load
     puts the reader back where they were rather than at the top of the row they
     just decided. */
  var pending = stored();

  function restore() {
    if (pending === null) return;
    window.scrollTo(0, pending);
  }

  if (pending !== null) {
    /* Only when this script is taking the position over. Left on, it would also
       disable the browser's own restoration on a back navigation — returning
       from an asset page to the queue — which is behaviour worth keeping. */
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    restore();
    window.addEventListener("load", function () {
      restore();
      pending = null;
      if ("scrollRestoration" in history) history.scrollRestoration = "auto";
    });
  }

  /* Accept, deny and clear are all full page loads, like a sort. */
  var decisions = document.querySelectorAll("button.decbtn");
  for (var d = 0; d < decisions.length; d++) {
    decisions[d].addEventListener("click", remember);
  }

  var sorts = document.querySelectorAll("a.sort");
  for (var i = 0; i < sorts.length; i++) {
    sorts[i].addEventListener("click", remember);
  }

  var form = document.querySelector("form[data-autosubmit]");
  if (!form) return;

  var slider = form.querySelector('input[type="range"]');
  var readout = form.querySelector("output");
  var fallback = form.querySelector("[data-fallback-submit]");
  if (!slider || !readout) return;

  /* Keep the wording identical to the server's, so the number the reader sees
     mid-drag reads the same as the one that comes back. */
  var suffix = readout.textContent.replace(/^\s*\d+\s*/, "") || "crew visits";

  slider.addEventListener("input", function () {
    readout.textContent = slider.value + " " + suffix;
  });

  /* `change` fires on release for a range input, so the page reloads once per
     adjustment rather than once per pixel of travel. */
  slider.addEventListener("change", function () {
    /* form.submit() does not fire a submit event, so the position is stored
       here rather than by a listener on the form. */
    remember();
    form.submit();
  });

  if (fallback) fallback.hidden = true;
  form.setAttribute("data-enhanced", "true");
})();
