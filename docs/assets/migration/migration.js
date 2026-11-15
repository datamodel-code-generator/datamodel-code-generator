(function () {
  "use strict";

  // The upgrade guide hub ships its release selector hidden, so readers without JavaScript use the range table.
  const SELECTOR = "[data-migration-select]";

  if (typeof document === "undefined") {
    return;
  }

  function reveal() {
    document.querySelectorAll(`${SELECTOR}[hidden]`).forEach((element) => {
      element.hidden = false;
    });
  }

  // One delegated listener survives instant navigation, which swaps the page content without rerunning this script.
  document.addEventListener("change", (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || !select.closest(SELECTOR) || !select.value) {
      return;
    }
    window.location.assign(new URL(select.value, window.location.href).href);
  });
  new MutationObserver(reveal).observe(document.documentElement, { childList: true, subtree: true });
  reveal();
})();
