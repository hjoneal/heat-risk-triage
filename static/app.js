/* The only script in the application, and it adds nothing the page cannot do
   without it. Server-rendered HTML remains the whole interface: this makes the
   crew-capacity slider report its value as it moves and submit itself when
   released, then removes the Apply button it has just made redundant. With
   scripting unavailable the button is still there and the form still works.

   No network call of its own, no dependency, no framework. */
(function () {
  "use strict";

  var form = document.querySelector("form[data-autosubmit]");
  if (!form) return;

  var slider = form.querySelector('input[type="range"]');
  var readout = form.querySelector("output");
  var fallback = form.querySelector("[data-fallback-submit]");
  if (!slider || !readout) return;

  /* Keep the wording identical to the server's, so the number the reader sees
     mid-drag reads the same as the one that comes back. */
  var suffix = readout.textContent.replace(/^\s*\d+\s*/, "") || "interventions";

  slider.addEventListener("input", function () {
    readout.textContent = slider.value + " " + suffix;
  });

  /* `change` fires on release for a range input, so the page reloads once per
     adjustment rather than once per pixel of travel. */
  slider.addEventListener("change", function () {
    form.submit();
  });

  if (fallback) fallback.hidden = true;
  form.setAttribute("data-enhanced", "true");
})();
